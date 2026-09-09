from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

AGENT_SCHEMA_VERSION = 1
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _similarity(left: str, right: str) -> float:
    a = set(_TOKEN_RE.findall(left.lower()))
    b = set(_TOKEN_RE.findall(right.lower()))
    if not a or not b:
        return 0.0
    jaccard = len(a & b) / len(a | b)
    seq = SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
    return 0.45 * jaccard + 0.55 * seq


class AgentStore:
    """Agent state kept in a SQLite database separate from the health archive."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @classmethod
    def beside_health_store(cls, health_store) -> "AgentStore":
        return cls(Path(health_store.path).with_name("vitalchronicle_agent.sqlite3"))

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tools (
                    tool_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active',
                    capability TEXT NOT NULL,
                    description TEXT NOT NULL,
                    parameters_json TEXT NOT NULL DEFAULT '{}',
                    outputs_json TEXT NOT NULL DEFAULT '{}',
                    pipeline_json TEXT NOT NULL DEFAULT '[]',
                    dependencies_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    replacement TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_tools
                    ON tools(capability, status, kind);
                CREATE TABLE IF NOT EXISTS user_model (
                    model_key TEXT PRIMARY KEY,
                    statement TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL DEFAULT 0.35,
                    evidence_count INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL DEFAULT 'feedback',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id TEXT PRIMARY KEY,
                    thread_id TEXT,
                    question TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    learning_key TEXT NOT NULL DEFAULT '',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    answer TEXT,
                    created_at TEXT NOT NULL,
                    answered_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_feedback
                    ON feedback(thread_id, answered_at, created_at);
                CREATE TABLE IF NOT EXISTS tool_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    tool_name TEXT,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            db.execute(
                "INSERT INTO agent_meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(AGENT_SCHEMA_VERSION),),
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM agent_meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO agent_meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    @staticmethod
    def _tool_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "tool_id": str(row["tool_id"]),
            "name": str(row["name"]),
            "kind": str(row["kind"]),
            "version": int(row["version"]),
            "status": str(row["status"]),
            "capability": str(row["capability"]),
            "description": str(row["description"]),
            "parameters": _loads(row["parameters_json"], {}),
            "outputs": _loads(row["outputs_json"], {}),
            "pipeline": _loads(row["pipeline_json"], []),
            "dependencies": _loads(row["dependencies_json"], []),
            "confidence": float(row["confidence"]),
            "replacement": row["replacement"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "use_count": int(row["use_count"]),
            "last_used_at": row["last_used_at"],
        }

    def list_tools(
        self, *, include_superseded: bool = False, kind: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_superseded:
            clauses.append("status='active'")
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM tools {where} ORDER BY kind,name", params
            ).fetchall()
        return [self._tool_row(row) for row in rows]

    def tool(self, name: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM tools WHERE name=?", (name,)).fetchone()
        return self._tool_row(row) if row else None

    def _equivalence_score(self, incoming: dict[str, Any], existing: dict[str, Any]) -> float:
        left_cap = str(incoming.get("capability") or incoming.get("name") or "")
        right_cap = str(existing.get("capability") or existing.get("name") or "")
        if left_cap.lower().strip() == right_cap.lower().strip():
            return 1.0
        left_pipeline = incoming.get("pipeline")
        right_pipeline = existing.get("pipeline")
        if left_pipeline and right_pipeline and _json(left_pipeline) == _json(right_pipeline):
            return 0.99
        score = _similarity(
            f"{left_cap} {incoming.get('description','')}",
            f"{right_cap} {existing.get('description','')}",
        )
        if incoming.get("parameters") == existing.get("parameters"):
            score = min(1.0, score + 0.06)
        return score

    def find_similar_tools(
        self,
        candidate: dict[str, Any],
        *,
        limit: int = 5,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        matches = []
        for item in self.list_tools(include_superseded=include_superseded):
            if item["name"] == candidate.get("name"):
                continue
            score = self._equivalence_score(candidate, item)
            if score >= 0.58:
                matches.append({**item, "similarity": round(score, 4)})
        matches.sort(
            key=lambda item: (float(item["similarity"]), item["kind"] == "builtin"),
            reverse=True,
        )
        return matches[:limit]

    def _upsert_tool(self, spec: dict[str, Any], kind: str) -> None:
        now = _now()
        name = str(spec["name"])
        with self._connect() as db:
            old = db.execute(
                "SELECT tool_id,created_at,use_count,last_used_at FROM tools WHERE name=?",
                (name,),
            ).fetchone()
            values = (
                str(old["tool_id"]) if old else str(uuid.uuid4()),
                name,
                kind,
                int(spec.get("version", 1)),
                str(spec.get("status", "active")),
                str(spec.get("capability") or name),
                str(spec.get("description") or ""),
                _json(spec.get("parameters") or {"type": "object", "properties": {}}),
                _json(spec.get("outputs") or {}),
                _json(spec.get("pipeline") or []),
                _json(spec.get("dependencies") or []),
                float(spec.get("confidence", 1.0 if kind == "builtin" else 0.6)),
                spec.get("replacement"),
                str(old["created_at"]) if old else now,
                now,
                int(old["use_count"]) if old else 0,
                old["last_used_at"] if old else None,
            )
            db.execute(
                """
                INSERT INTO tools(
                    tool_id,name,kind,version,status,capability,description,
                    parameters_json,outputs_json,pipeline_json,dependencies_json,
                    confidence,replacement,created_at,updated_at,use_count,last_used_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    kind=excluded.kind,version=excluded.version,status=excluded.status,
                    capability=excluded.capability,description=excluded.description,
                    parameters_json=excluded.parameters_json,outputs_json=excluded.outputs_json,
                    pipeline_json=excluded.pipeline_json,dependencies_json=excluded.dependencies_json,
                    confidence=excluded.confidence,replacement=excluded.replacement,
                    updated_at=excluded.updated_at
                """,
                values,
            )

    def sync_builtin_tools(self, specs: Iterable[dict[str, Any]]) -> None:
        for spec in specs:
            normalized = {**spec, "status": "active", "confidence": 1.0}
            self._upsert_tool(normalized, "builtin")
        builtins = self.list_tools(kind="builtin")
        learned = self.list_tools(include_superseded=True, kind="learned")
        for item in learned:
            best = max(
                ((self._equivalence_score(item, built), built) for built in builtins),
                default=(0.0, None),
                key=lambda pair: pair[0],
            )
            if best[1] is not None and best[0] >= 0.94:
                if item["status"] != "superseded" or item.get("replacement") != best[1]["name"]:
                    self.supersede_tool(item["name"], best[1]["name"])
                    self.log_tool_event(
                        "tool_superseded",
                        f"Learned tool {item['name']} is now covered by built-in {best[1]['name']}.",
                        tool_name=item["name"],
                        payload={"replacement": best[1]["name"], "similarity": best[0]},
                    )

    def add_learned_tool(self, spec: dict[str, Any]) -> dict[str, Any]:
        candidate = {**spec, "status": "active"}
        if not str(candidate.get("name") or "").strip():
            raise ValueError("Learned tools require a name")
        similar = self.find_similar_tools(candidate, limit=6)
        equivalent = next(
            (item for item in similar if float(item["similarity"]) >= 0.94), None
        )
        if equivalent:
            self.log_tool_event(
                "tool_reused",
                f"Skipped creation of {candidate['name']}; equivalent tool {equivalent['name']} already exists.",
                tool_name=equivalent["name"],
                payload={"candidate": candidate["name"], "similarity": equivalent["similarity"]},
            )
            return {"status": "reused", "tool": equivalent, "similar_tools": similar}
        self._upsert_tool(candidate, "learned")
        created = self.tool(str(candidate["name"]))
        self.log_tool_event(
            "tool_created",
            f"Created learned tool {candidate['name']}.",
            tool_name=str(candidate["name"]),
            payload={"similar_tools": [{"name": x["name"], "similarity": x["similarity"]} for x in similar]},
        )
        return {"status": "created", "tool": created, "similar_tools": similar}

    def supersede_tool(self, name: str, replacement: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE tools SET status='superseded',replacement=?,updated_at=? "
                "WHERE name=? AND kind='learned'",
                (replacement, _now(), name),
            )

    def delete_learned_tool(self, name: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM tools WHERE name=? AND kind='learned'", (name,))
        if cursor.rowcount:
            self.log_tool_event("tool_deleted", f"Deleted learned tool {name}.", tool_name=name)
        return bool(cursor.rowcount)

    def record_tool_use(self, name: str) -> None:
        now = _now()
        with self._connect() as db:
            db.execute(
                "UPDATE tools SET use_count=use_count+1,last_used_at=?,updated_at=? WHERE name=?",
                (now, now, name),
            )

    def log_tool_event(
        self,
        event_type: str,
        message: str,
        *,
        tool_name: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO tool_events(created_at,event_type,tool_name,message,payload_json) "
                "VALUES(?,?,?,?,?)",
                (_now(), event_type, tool_name, message, _json(payload or {})),
            )

    def recent_tool_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM tool_events ORDER BY event_id DESC LIMIT ?", (max(1, limit),)
            ).fetchall()
        return [
            {
                "created_at": row["created_at"],
                "event_type": row["event_type"],
                "tool_name": row["tool_name"],
                "message": row["message"],
                "payload": _loads(row["payload_json"], {}),
            }
            for row in reversed(rows)
        ]

    def ask_feedback(
        self,
        question: str,
        *,
        thread_id: str | None = None,
        reason: str = "",
        learning_key: str = "",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT feedback_id FROM feedback WHERE COALESCE(thread_id,'')=COALESCE(?,'') "
                "AND answered_at IS NULL AND question=? ORDER BY created_at DESC LIMIT 1",
                (thread_id, question),
            ).fetchone()
            if row:
                return self.feedback(str(row["feedback_id"])) or {}
            feedback_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO feedback(feedback_id,thread_id,question,reason,learning_key,context_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (feedback_id, thread_id, question.strip(), reason.strip(), learning_key.strip(), _json(context or {}), _now()),
            )
        return self.feedback(feedback_id) or {}

    def feedback(self, feedback_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM feedback WHERE feedback_id=?", (feedback_id,)).fetchone()
        if not row:
            return None
        return {
            "feedback_id": str(row["feedback_id"]),
            "thread_id": row["thread_id"],
            "question": str(row["question"]),
            "reason": str(row["reason"]),
            "learning_key": str(row["learning_key"]),
            "context": _loads(row["context_json"], {}),
            "answer": row["answer"],
            "created_at": str(row["created_at"]),
            "answered_at": row["answered_at"],
        }

    def pending_feedback(self, thread_id: str | None = None) -> dict[str, Any] | None:
        clause = "AND COALESCE(thread_id,'')=COALESCE(?,'')" if thread_id is not None else ""
        params = [thread_id] if thread_id is not None else []
        with self._connect() as db:
            row = db.execute(
                f"SELECT feedback_id FROM feedback WHERE answered_at IS NULL {clause} "
                "ORDER BY created_at DESC LIMIT 1",
                params,
            ).fetchone()
        return self.feedback(str(row["feedback_id"])) if row else None

    def dismiss_feedback(self, feedback_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE feedback SET answer='[skipped]',answered_at=? "
                "WHERE feedback_id=? AND answered_at IS NULL",
                (_now(), feedback_id),
            )
        return bool(cursor.rowcount)

    def answer_feedback(self, feedback_id: str, answer: str) -> dict[str, Any] | None:
        item = self.feedback(feedback_id)
        answer = answer.strip()
        if not item or not answer:
            return item
        with self._connect() as db:
            db.execute(
                "UPDATE feedback SET answer=?,answered_at=? WHERE feedback_id=?",
                (answer, _now(), feedback_id),
            )
        key = str(item.get("learning_key") or "").strip()
        if key:
            context = item.get("context") or {}
            observation = context.get("observation") or context
            statement = (
                f"When {observation}, the user reported: {answer}"
                if observation
                else f"User feedback: {answer}"
            )
            self.learn_user_model(
                key,
                statement,
                evidence={"question": item["question"], "answer": answer, "context": context},
                source="feedback",
            )
        return self.feedback(feedback_id)

    def learn_user_model(
        self,
        key: str,
        statement: str,
        *,
        evidence: dict[str, Any] | None = None,
        source: str = "feedback",
    ) -> dict[str, Any]:
        key = key.strip()
        if not key:
            raise ValueError("User-model key cannot be empty")
        now = _now()
        with self._connect() as db:
            row = db.execute("SELECT * FROM user_model WHERE model_key=?", (key,)).fetchone()
            if row:
                items = _loads(row["evidence_json"], [])
                if evidence:
                    items.append(evidence)
                count = int(row["evidence_count"]) + 1
                confidence = min(0.95, 0.35 + 0.08 * count)
                db.execute(
                    "UPDATE user_model SET statement=?,evidence_json=?,confidence=?,"
                    "evidence_count=?,source=?,updated_at=? WHERE model_key=?",
                    (statement, _json(items[-40:]), confidence, count, source, now, key),
                )
            else:
                db.execute(
                    "INSERT INTO user_model(model_key,statement,evidence_json,confidence,"
                    "evidence_count,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (key, statement, _json([evidence] if evidence else []), 0.43, 1, source, now, now),
                )
        return self.user_model_entry(key) or {}

    def user_model_entry(self, key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM user_model WHERE model_key=?", (key,)).fetchone()
        if not row:
            return None
        return {
            "key": str(row["model_key"]),
            "statement": str(row["statement"]),
            "confidence": float(row["confidence"]),
            "evidence_count": int(row["evidence_count"]),
            "source": str(row["source"]),
            "evidence": _loads(row["evidence_json"], []),
            "updated_at": str(row["updated_at"]),
        }

    def user_model(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT model_key FROM user_model ORDER BY confidence DESC,evidence_count DESC,updated_at DESC"
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self.user_model_entry(str(row["model_key"]))) is not None
        ]

    def forget_user_model(self, key: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM user_model WHERE model_key=?", (key,))
        return bool(cursor.rowcount)

    def mark_calibrated(self, version: int = 1) -> None:
        self.set_meta("calibration_version", str(version))
        self.set_meta("calibrated_at", _now())

    def calibration_version(self) -> int:
        try:
            return int(self.get_meta("calibration_version", "0") or 0)
        except ValueError:
            return 0

    def clear(self) -> None:
        with self._connect() as db:
            db.executescript(
                "DELETE FROM tools; DELETE FROM user_model; DELETE FROM feedback; "
                "DELETE FROM tool_events; DELETE FROM agent_meta;"
            )
        self._initialize()
