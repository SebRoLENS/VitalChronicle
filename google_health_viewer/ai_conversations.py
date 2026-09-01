"""Private, local persistence for AI conversation threads."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

from .ai_pipeline import ensure_compact_evidence

SCHEMA_VERSION = 1
MODEL_ROLES = {"user", "assistant"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_path() -> Path:
    directory = Path(user_data_dir("GoogleHealthViewer", "SebastianoRomi"))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "ai_conversations.json"


def _snapshot_with_ai_cache(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Persist the compact model packet with the deterministic snapshot.

    The snapshot is rebuilt only when the user refreshes the conversation data,
    so this small packet is naturally reused until the local health-data revision changes.
    """

    cached = copy.deepcopy(snapshot)
    cached["ai_compact_evidence"] = ensure_compact_evidence(cached)
    return cached


class ConversationStore:
    """Store compact AI threads without sending them outside the computer."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": SCHEMA_VERSION, "threads": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": SCHEMA_VERSION, "threads": []}
        if not isinstance(payload, dict) or not isinstance(payload.get("threads"), list):
            return {"schema_version": SCHEMA_VERSION, "threads": []}
        payload["schema_version"] = SCHEMA_VERSION
        return payload

    def _save(self) -> None:
        raw = json.dumps(self._data, ensure_ascii=False, indent=2)
        descriptor, temporary = tempfile.mkstemp(
            prefix="ai-conversations-", suffix=".json", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def list_threads(self) -> list[dict[str, Any]]:
        threads = sorted(
            self._data["threads"], key=lambda item: str(item.get("updated_at", "")), reverse=True
        )
        return copy.deepcopy(threads)

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        thread = next((item for item in self._data["threads"] if item.get("id") == thread_id), None)
        return copy.deepcopy(thread) if thread is not None else None

    def create_thread(
        self,
        *,
        title: str,
        model: str,
        scope: str,
        period: dict[str, Any],
        snapshot: dict[str, Any],
        snapshot_revision: str | None,
    ) -> dict[str, Any]:
        created = _now()
        cached_snapshot = _snapshot_with_ai_cache(snapshot)
        thread = {
            "id": uuid.uuid4().hex,
            "title": title.strip() or "New health conversation",
            "created_at": created,
            "updated_at": created,
            "model": model,
            "scope": scope,
            "period": copy.deepcopy(period),
            "snapshot": cached_snapshot,
            "snapshot_revision": snapshot_revision,
            "snapshot_observed_at": snapshot.get("observation_context", {}).get("observed_at"),
            "messages": [],
        }
        self._data["threads"].append(thread)
        self._save()
        return copy.deepcopy(thread)

    def rename_thread(self, thread_id: str, title: str) -> dict[str, Any] | None:
        thread = self._mutable_thread(thread_id)
        if thread is None:
            return None
        thread["title"] = title.strip() or thread["title"]
        thread["updated_at"] = _now()
        self._save()
        return copy.deepcopy(thread)

    def delete_thread(self, thread_id: str) -> bool:
        before = len(self._data["threads"])
        self._data["threads"] = [
            item for item in self._data["threads"] if item.get("id") != thread_id
        ]
        changed = len(self._data["threads"]) != before
        if changed:
            self._save()
        return changed

    def clear(self) -> None:
        self._data = {"schema_version": SCHEMA_VERSION, "threads": []}
        self._save()

    def add_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        *,
        evidence_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        thread = self._mutable_thread(thread_id)
        if thread is None:
            raise KeyError(thread_id)
        message = {
            "id": uuid.uuid4().hex,
            "role": role,
            "content": content,
            "created_at": _now(),
        }
        if evidence_ids:
            message["evidence_ids"] = list(evidence_ids)
        thread.setdefault("messages", []).append(message)
        thread["updated_at"] = message["created_at"]
        if role == "user" and len([m for m in thread["messages"] if m.get("role") == "user"]) == 1:
            concise = " ".join(content.strip().split())
            if concise:
                thread["title"] = concise[:64] + ("…" if len(concise) > 64 else "")
        self._save()
        return copy.deepcopy(message)

    def remove_last_assistant(self, thread_id: str) -> bool:
        thread = self._mutable_thread(thread_id)
        if thread is None:
            return False
        messages = thread.get("messages", [])
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "assistant":
                del messages[index]
                thread["updated_at"] = _now()
                self._save()
                return True
            if messages[index].get("role") == "user":
                break
        return False

    def update_snapshot(
        self,
        thread_id: str,
        snapshot: dict[str, Any],
        snapshot_revision: str | None,
        period: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        thread = self._mutable_thread(thread_id)
        if thread is None:
            return None
        thread["snapshot"] = _snapshot_with_ai_cache(snapshot)
        thread["snapshot_revision"] = snapshot_revision
        thread["snapshot_observed_at"] = snapshot.get("observation_context", {}).get("observed_at")
        if period is not None:
            thread["period"] = copy.deepcopy(period)
        thread["updated_at"] = _now()
        self._save()
        return copy.deepcopy(thread)

    def model_history(
        self, thread_id: str, *, exclude_last_user: bool = False, recent_messages: int = 8
    ) -> list[dict[str, str]]:
        thread = self.get_thread(thread_id)
        if not thread:
            return []
        messages = [
            {"role": str(item["role"]), "content": str(item.get("content", ""))}
            for item in thread.get("messages", [])
            if item.get("role") in MODEL_ROLES and str(item.get("content", "")).strip()
        ]
        if exclude_last_user and messages and messages[-1]["role"] == "user":
            messages.pop()
        if len(messages) <= recent_messages:
            return messages
        older = messages[:-recent_messages]
        recent = messages[-recent_messages:]
        excerpts = []
        for message in older:
            content = " ".join(message["content"].split())
            excerpts.append(f"{message['role']}: {content[:280]}")
        summary = {
            "role": "user",
            "content": (
                "Earlier conversation excerpts for continuity (not new health evidence):\n"
                + "\n".join(excerpts[-8:])
            ),
        }
        return [summary, *recent]

    def _mutable_thread(self, thread_id: str) -> dict[str, Any] | None:
        return next((item for item in self._data["threads"] if item.get("id") == thread_id), None)
