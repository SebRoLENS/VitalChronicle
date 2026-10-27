#!/usr/bin/env python3
"""Run repeatable local-model prompts against a read-only health archive.

Prompt files contain a list of {id, question, history?} objects. All answers,
traces and temporary agent state are saved to the explicit output directory.
No health records or existing conversations/personalisation are modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google_health_viewer.agent_runtime_v2 import AgentRuntime
from google_health_viewer.agent_store import AgentStore
from google_health_viewer.ai_engine import OptimizedOllamaClient, _request_budget
from google_health_viewer.ai_query_planner_core import (
    _parse_json_object,
    _planner_messages,
    build_data_catalog,
    build_planned_snapshot,
    resolve_data_plan,
)
from google_health_viewer.i18n import set_language
from google_health_viewer.online_ai import is_online_model
from google_health_viewer.storage import HealthStore


class ReadOnlyHealthStore(HealthStore):
    def __init__(self, path: Path):
        self.path = path.resolve(strict=True)

    def _connect(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-db", type=Path, required=True)
    parser.add_argument("--prompts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--context", type=int, default=32768)
    parser.add_argument("--tokens", type=int, default=2800)
    parser.add_argument("--profile", choices=["fast", "standard", "max"], default="standard")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    # Only use Ollama locally; this script must never silently switch to a cloud provider.
    if is_online_model(args.model):
        parser.error("Pass an installed local Ollama model name.")
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    set_language("it")
    health = ReadOnlyHealthStore(args.health_db)
    catalog = build_data_catalog(health)
    cases = json.loads(args.prompts.read_text())
    client = OptimizedOllamaClient(model=args.model, performance_profile=args.profile)
    for case in cases:
        case_id = str(case["id"])
        if args.only and case_id not in args.only:
            continue
        if not args.worker:
            # Reload the production modules per case, also allowing a later case
            # to verify fixes made while this audit is running.
            subprocess.run(
                [sys.executable, __file__, *sys.argv[1:], "--worker", "--only", case_id],
                check=False,
            )
            continue
        if not case_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Prompt IDs must be safe file names.")
        dest = args.output / case_id
        dest.mkdir(mode=0o700)
        started = time.monotonic()
        digest = hashlib.sha256()
        for path in sorted(
            (Path(__file__).resolve().parents[1] / "google_health_viewer").rglob("*.py")
        ):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        result = {
            "id": case_id,
            "question": case["question"],
            "model": args.model,
            "profile": args.profile,
            "source_sha256": digest.hexdigest(),
            "archive_revision": health.data_revision(),
        }
        with (dest / "trace.jsonl").open("w") as trace:

            def log(kind, value, started=started, case_id=case_id):
                trace.write(
                    json.dumps(
                        {
                            "elapsed": round(time.monotonic() - started, 2),
                            "kind": kind,
                            "value": value,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )
                trace.flush()
                if kind == "event":
                    print(case_id, value, flush=True)

            try:
                print("START", case_id, flush=True)
                history = case.get("history", [])
                messages = _planner_messages(catalog, case["question"], history)
                ctx, predict, _ = _request_budget(messages, 560, args.context)
                raw = client._chat_stream(
                    messages,
                    think=False,
                    num_predict=predict,
                    num_ctx=ctx,
                    thinking_callback=None,
                    answer_callback=None,
                    cancel_callback=None,
                )
                log("planner_raw", raw)
                plan = resolve_data_plan(_parse_json_object(raw), catalog)
                result["plan"] = plan
                snapshot, period = build_planned_snapshot(health, plan)
                result["period"] = period
                (dest / "snapshot.json").write_text(
                    json.dumps(snapshot, ensure_ascii=False, default=str)
                )
                runtime = AgentRuntime(health, AgentStore(dest / "agent.sqlite3"))

                def observed_chat(_chat=runtime._chat_once, _log=log, **kwargs):
                    _log(
                        "model_input",
                        {
                            "messages": kwargs.get("messages"),
                            "tools": [
                                s.get("function", {}).get("name") for s in kwargs.get("tools", [])
                            ],
                            "num_ctx": kwargs.get("num_ctx"),
                            "num_predict": kwargs.get("num_predict"),
                        },
                    )
                    return _chat(**kwargs)

                runtime._chat_once = observed_chat
                result["answer"] = runtime.analyze(
                    model=args.model,
                    snapshot=snapshot,
                    question=case["question"],
                    history=history,
                    max_tokens=args.tokens,
                    model_context_limit=args.context,
                    performance_profile=args.profile,
                    thread_id=case_id,
                    event_callback=lambda x: log("event", x),
                    prompt_callback=lambda x: log("trace", x),
                )
                result["model_calls"] = runtime._agent_model_calls
                result["input_tokens"] = runtime._agent_prompt_tokens_total
                result["output_tokens"] = runtime._agent_generated_tokens_total
                (dest / "answer.md").write_text(result["answer"])
            except Exception:  # noqa: BLE001 - retain a failed case and continue the audit.
                result["error"] = traceback.format_exc()
                print(result["error"], flush=True)
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (dest / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(
            "DONE",
            case_id,
            result["elapsed_seconds"],
            "error" if "error" in result else "ok",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
