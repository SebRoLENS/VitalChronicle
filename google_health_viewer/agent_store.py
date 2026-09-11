from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

AGENT_SCHEMA_VERSION = 3
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CONFIRMATION_MARKERS = (
    "si",
    "sì",
    "yes",
    "esatto",
    "esatto,",
    "corretto",
    "confermo",
    "va bene",
    "ok",
    "okay",
    "certo",
    "giusto",
)


def _is_explicit_confirmation(answer: str) -> bool:
    normalized = re.sub(r"[^a-zàèéìòù0-9 ]+", " ", answer.casefold())
    normalized = " ".join(normalized.split())
    return normalized in _CONFIRMATION_MARKERS or normalized.startswith(
        ("si ", "sì ", "yes ", "esatto ", "corretto ", "confermo ")
    )

PERSONAL_CONTEXT_KEY_SPECS: dict[str, dict[str, Any]] = {
    "sleep_schedule_context": {
        "description": "Usual sleep timing and chronotype.",
        "topics": ("sleep",),
        "default_scope": "stable",
        "ttl_days": 60,
        "markers": (
            "di solito dormo",
            "normalmente dormo",
            "di solito vado a letto",
            "normalmente vado a letto",
            "la mia routine del sonno",
            "mi sveglio di solito",
            "vado a letto di solito",
        ),
    },
    "sleep_quality_context": {
        "description": "Usual subjective sleep quality.",
        "topics": ("sleep", "recovery"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "di solito dormo bene",
            "normalmente dormo bene",
            "dormo male di solito",
            "la qualità del mio sonno",
            "il mio sonno è di solito",
        ),
    },
    "subjective_sleep_need_context": {
        "description": "Amount of sleep the user feels they need.",
        "topics": ("sleep", "recovery"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "mi bastano",
            "ho bisogno di",
            "ore di sonno sto bene",
            "mi sento riposato con",
            "mi sento riposata con",
        ),
    },
    "training_routine_context": {
        "description": "Usual training modality and routine.",
        "topics": ("training", "recovery"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "di solito mi alleno",
            "normalmente mi alleno",
            "mi alleno regolarmente",
            "la mia routine di allenamento",
            "il mio allenamento abituale",
        ),
    },
    "training_frequency_context": {
        "description": "Usual training frequency.",
        "topics": ("training", "recovery"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "giorni a settimana",
            "volte a settimana",
            "tutti i giorni",
            "ogni giorno",
            "quotidianamente",
        ),
    },
    "current_training_goal": {
        "description": "Current training objective.",
        "topics": ("training",),
        "default_scope": "stable",
        "ttl_days": 90,
        "markers": (
            "il mio obiettivo",
            "sto cercando di allenarmi",
            "ho ricominciato ad allenarmi",
            "ho ricominciato palestra",
            "voglio migliorare",
            "punto a",
        ),
    },
    "training_preferences": {
        "description": "Preferred training activities or style.",
        "topics": ("training",),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "preferisco allenarmi",
            "mi piace allenarmi",
            "preferisco la bicicletta",
            "preferisco correre",
            "il mio sport preferito",
        ),
    },
    "training_constraints": {
        "description": "Training limitations or constraints explicitly stated by the user.",
        "topics": ("training", "recovery"),
        "default_scope": "stable",
        "ttl_days": 90,
        "markers": (
            "non posso allenarmi",
            "devo evitare",
            "ho un infortunio",
            "ho dolore a",
            "mi limita",
            "non riesco ad allenarmi",
        ),
    },
    "subjective_recovery_baseline": {
        "description": "Usual subjective recovery speed or state.",
        "topics": ("recovery", "training"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "di solito recupero",
            "normalmente recupero",
            "mi riprendo rapidamente",
            "mi riprendo lentamente",
            "recupero rapidamente",
            "recupero lentamente",
        ),
    },
    "high_load_subjective_tolerance": {
        "description": "How the user usually tolerates high training load.",
        "topics": ("training", "recovery"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "reggo bene i carichi",
            "tollero bene i carichi",
            "carichi elevati",
            "allenamenti intensi mi lasciano",
            "gestisco bene gli allenamenti intensi",
        ),
    },
    "usual_energy_level": {
        "description": "Usual subjective energy level.",
        "topics": ("recovery", "training"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "di solito ho energia",
            "normalmente ho energia",
            "il mio livello di energia",
            "di solito mi sento energico",
            "di solito mi sento energica",
        ),
    },
    "usual_fatigue_response": {
        "description": "Usual subjective response and duration of fatigue.",
        "topics": ("recovery", "training", "sleep"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "dopo allenamenti intensi mi sento",
            "di solito la fatica",
            "la fatica mi dura",
            "mi stanco facilmente",
            "non mi stanco quasi mai",
        ),
    },
    "stress_context": {
        "description": "Ongoing stress context affecting interpretation.",
        "topics": ("recovery", "sleep"),
        "default_scope": "temporary",
        "ttl_days": 42,
        "markers": (
            "sono sotto stress",
            "periodo stressante",
            "il lavoro mi stressa",
            "sto vivendo un periodo difficile",
            "sono molto stressato",
            "sono molto stressata",
        ),
    },
    "work_schedule_context": {
        "description": "Work schedule or commuting pattern relevant to health data.",
        "topics": ("sleep", "recovery", "training"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "il mio orario di lavoro",
            "lavoro dalle",
            "lavoro a turni",
            "faccio i turni",
            "faccio il pendolare",
            "vado al lavoro in bici",
        ),
    },
    "nutrition_context": {
        "description": "Stable dietary context relevant to health interpretation.",
        "topics": ("training", "recovery"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "la mia alimentazione",
            "seguo una dieta",
            "di solito mangio",
            "mangio ogni giorno",
            "la mia dieta",
        ),
    },
    "stimulant_context": {
        "description": "Usual caffeine or alcohol context.",
        "topics": ("sleep", "recovery"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "bevo caffè",
            "bevo caffe",
            "assumo caffeina",
            "consumo caffeina",
            "bevo alcol",
            "consumo alcol",
        ),
    },
    "illness_context": {
        "description": "Recent illness or recovery from illness explicitly reported.",
        "topics": ("recovery", "sleep"),
        "default_scope": "temporary",
        "ttl_days": 21,
        "markers": (
            "sono malato",
            "sono malata",
            "ho la febbre",
            "sono influenzato",
            "sono influenzata",
            "sto recuperando da",
        ),
    },
    "medication_context": {
        "description": "Medication or supplement context explicitly reported.",
        "topics": ("recovery", "sleep"),
        "default_scope": "stable",
        "ttl_days": 90,
        "markers": (
            "assumo regolarmente",
            "assumo ogni giorno",
            "prendo ogni giorno",
            "terapia farmacologica",
            "integratore che prendo",
        ),
    },
    "travel_context": {
        "description": "Recent travel or timezone change.",
        "topics": ("sleep", "recovery"),
        "default_scope": "temporary",
        "ttl_days": 21,
        "markers": (
            "sono in viaggio",
            "ho viaggiato",
            "jet lag",
            "cambio di fuso",
            "fuso orario",
        ),
    },
    "personal_analysis_preferences": {
        "description": "Preferred metrics, comparisons, and analysis format.",
        "topics": ("sleep", "training", "recovery"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "preferisco vedere",
            "voglio che l'analisi",
            "mi interessa soprattutto",
            "considera sempre",
        ),
    },
    "coaching_preferences": {
        "description": "Preferred coaching style and recommendation format.",
        "topics": ("sleep", "training", "recovery"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "preferisco consigli",
            "non voglio consigli",
            "spiegazioni brevi",
            "voglio una risposta breve",
            "voglio una spiegazione dettagliata",
        ),
    },
    "primary_health_coaching_goal": {
        "description": "Main health question the user wants to explore.",
        "topics": ("sleep", "training", "recovery"),
        "default_scope": "stable",
        "ttl_days": None,
        "markers": (
            "voglio capire",
            "vorrei concentrarmi su",
            "mi interessa capire",
            "il mio obiettivo principale",
        ),
    },
}

_TEMPORAL_KEY_TTLS = {
    key: int(spec["ttl_days"])
    for key, spec in PERSONAL_CONTEXT_KEY_SPECS.items()
    if spec.get("ttl_days") is not None
}

_TEMPORAL_MARKERS = (
    "da poco",
    "recentemente",
    "in questo periodo",
    "al momento",
    "attualmente",
    "questa settimana",
    "queste settimane",
    "ho ricominciato",
    "recently",
    "right now",
    "currently",
    "these weeks",
    "this week",
    "just restarted",
    "started again",
)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _temporal_profile(
    key: str,
    statement: str,
    context: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    context = context or {}
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    explicit_scope = str(context.get("temporal_scope") or "").strip().lower()
    explicit_ttl = context.get("ttl_days")
    ttl: int | None = None
    if explicit_ttl is not None:
        try:
            ttl = max(1, min(3650, int(explicit_ttl)))
        except (TypeError, ValueError):
            ttl = None

    combined = f"{key} {statement}".casefold()
    temporary = explicit_scope == "temporary"
    if explicit_scope == "stable":
        temporary = False
    elif (
        ttl is not None
        or key in _TEMPORAL_KEY_TTLS
        or any(marker in combined for marker in _TEMPORAL_MARKERS)
    ):
        temporary = True

    if not temporary:
        return {
            "scope": "stable",
            "valid_from": current.isoformat(),
            "valid_until": None,
            "ttl_days": None,
        }

    if ttl is None:
        ttl = (
            42
            if any(marker in combined for marker in _TEMPORAL_MARKERS)
            else _TEMPORAL_KEY_TTLS.get(key, 60)
        )
    valid_from = _parse_datetime(context.get("valid_from")) or current
    valid_until = _parse_datetime(context.get("valid_until")) or (valid_from + timedelta(days=ttl))
    return {
        "scope": "temporary",
        "valid_from": valid_from.isoformat(),
        "valid_until": valid_until.isoformat(),
        "ttl_days": ttl,
    }


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
                CREATE TABLE IF NOT EXISTS self_reports (
                    report_id TEXT PRIMARY KEY,
                    thread_id TEXT,
                    category TEXT NOT NULL DEFAULT 'wellbeing',
                    statement TEXT NOT NULL,
                    intensity REAL,
                    observed_at TEXT NOT NULL,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_self_reports
                    ON self_reports(category, observed_at, created_at);
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
            self._migrate_legacy_personal_context(db)
            db.execute(
                "INSERT INTO agent_meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(AGENT_SCHEMA_VERSION),),
            )

    @staticmethod
    def _legacy_context_statement(statement: str) -> str | None:
        markers = (
            "di solito mi alleno",
            "normalmente mi alleno",
            "mi alleno in bici",
            "mi alleno in bicicletta",
            "la mia routine di allenamento",
        )
        for sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+|(?<=;)\s+", statement.strip()):
            clean = sentence.strip(" \t\n.;")
            if clean and any(marker in clean.casefold() for marker in markers):
                if not clean.endswith(("?", "？")):
                    return clean
        return None

    @classmethod
    def _scrub_legacy_candidate(cls, value: Any, statement: str) -> Any:
        if isinstance(value, dict):
            return {
                str(key): (
                    statement
                    if key == "candidate_statement"
                    else cls._scrub_legacy_candidate(item, statement)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._scrub_legacy_candidate(item, statement) for item in value]
        return value

    def _migrate_legacy_personal_context(self, db: sqlite3.Connection) -> None:
        row = db.execute(
            "SELECT * FROM user_model WHERE model_key='current_training_goal'"
        ).fetchone()
        if not row:
            return
        statement = self._legacy_context_statement(str(row["statement"]))
        if not statement:
            return
        evidence = self._scrub_legacy_candidate(
            _loads(row["evidence_json"], []), statement
        )
        target = db.execute(
            "SELECT model_key FROM user_model WHERE model_key='training_routine_context'"
        ).fetchone()
        if target:
            db.execute("DELETE FROM user_model WHERE model_key='current_training_goal'")
        else:
            db.execute(
                "UPDATE user_model SET model_key=?,statement=?,evidence_json=?,updated_at=? "
                "WHERE model_key='current_training_goal'",
                (
                    "training_routine_context",
                    statement,
                    _json(evidence),
                    _now(),
                ),
            )
        db.execute(
            "INSERT INTO tool_events(created_at,event_type,tool_name,message,payload_json) "
            "VALUES(?,?,?,?,?)",
            (
                _now(),
                "personal_context_migrated",
                None,
                "Migrated a legacy training routine from current_training_goal.",
                _json(
                    {
                        "from_key": "current_training_goal",
                        "to_key": "training_routine_context",
                        "statement": statement,
                        "scrubbed_full_request": True,
                    }
                ),
            ),
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
            rows = db.execute(f"SELECT * FROM tools {where} ORDER BY kind,name", params).fetchall()
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
            f"{left_cap} {incoming.get('description', '')}",
            f"{right_cap} {existing.get('description', '')}",
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
        equivalent = next((item for item in similar if float(item["similarity"]) >= 0.94), None)
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
            payload={
                "similar_tools": [
                    {"name": x["name"], "similarity": x["similarity"]} for x in similar
                ]
            },
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

    def record_self_report(
        self,
        statement: str,
        *,
        category: str = "wellbeing",
        thread_id: str | None = None,
        observed_at: str | None = None,
        intensity: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        statement = statement.strip()
        if not statement:
            raise ValueError("Self-report statement cannot be empty")
        now = datetime.now(timezone.utc)
        created_at = now.isoformat()
        observed = _parse_datetime(observed_at) or now
        cutoff = (now - timedelta(hours=12)).isoformat()
        normalized_category = str(category or "wellbeing").strip().lower()[:40] or "wellbeing"
        with self._connect() as db:
            duplicate = db.execute(
                "SELECT report_id FROM self_reports WHERE COALESCE(thread_id,'')=COALESCE(?,'') "
                "AND category=? AND statement=? AND created_at>=? ORDER BY created_at DESC LIMIT 1",
                (thread_id, normalized_category, statement, cutoff),
            ).fetchone()
            if duplicate:
                return self.self_report(str(duplicate["report_id"])) or {}
            report_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO self_reports(report_id,thread_id,category,statement,intensity,observed_at,"
                "context_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    report_id,
                    thread_id,
                    normalized_category,
                    statement,
                    float(intensity) if isinstance(intensity, (int, float)) else None,
                    observed.isoformat(),
                    _json(context or {}),
                    created_at,
                    created_at,
                ),
            )
        return self.self_report(report_id) or {}

    def self_report(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM self_reports WHERE report_id=?", (report_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "report_id": str(row["report_id"]),
            "thread_id": row["thread_id"],
            "category": str(row["category"]),
            "statement": str(row["statement"]),
            "intensity": row["intensity"],
            "observed_at": str(row["observed_at"]),
            "context": _loads(row["context_json"], {}),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def recent_self_reports(
        self, *, days: int = 30, limit: int = 50, category: str | None = None
    ) -> list[dict[str, Any]]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, min(3650, int(days))))
        ).isoformat()
        params: list[Any] = [cutoff]
        category_clause = ""
        if category:
            category_clause = "AND category=?"
            params.append(str(category).strip().lower())
        params.append(max(1, min(200, int(limit))))
        with self._connect() as db:
            rows = db.execute(
                f"SELECT report_id FROM self_reports WHERE observed_at>=? {category_clause} "
                "ORDER BY observed_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [
            item for row in rows if (item := self.self_report(str(row["report_id"]))) is not None
        ]

    def update_self_report_feedback(self, report_id: str, answer: str) -> bool:
        item = self.self_report(report_id)
        if not item:
            return False
        context = dict(item.get("context") or {})
        context["follow_up_answer"] = answer.strip()
        context["follow_up_answered_at"] = _now()
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE self_reports SET context_json=?,updated_at=? WHERE report_id=?",
                (_json(context), _now(), report_id),
            )
        return bool(cursor.rowcount)

    def has_recent_feedback_key(self, learning_key: str, *, days: int = 14) -> bool:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()
        with self._connect() as db:
            row = db.execute(
                "SELECT feedback_id FROM feedback WHERE learning_key=? AND created_at>=? "
                "ORDER BY created_at DESC LIMIT 1",
                (learning_key, cutoff),
            ).fetchone()
        return row is not None

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
                (
                    feedback_id,
                    thread_id,
                    question.strip(),
                    reason.strip(),
                    learning_key.strip(),
                    _json(context or {}),
                    _now(),
                ),
            )
        return self.feedback(feedback_id) or {}

    def feedback(self, feedback_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM feedback WHERE feedback_id=?", (feedback_id,)
            ).fetchone()
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
        context = item.get("context") or {}
        if context.get("feedback_mode") == "durable_context_confirmation":
            candidate_statement = str(context.get("candidate_statement") or "").strip()
            model_key = str(context.get("model_key") or key).strip()
            if candidate_statement and _is_explicit_confirmation(answer):
                self.learn_user_model(
                    model_key,
                    candidate_statement,
                    evidence={
                        "question": item["question"],
                        "answer": answer,
                        "context": context,
                        "confirmation": "explicit_user_confirmation",
                        "temporal": _temporal_profile(
                            model_key, candidate_statement, context
                        ),
                    },
                    source="explicit_user_confirmation",
                )
            return self.feedback(feedback_id)
        self_report_id = str(context.get("self_report_id") or "").strip()
        if self_report_id:
            self.update_self_report_feedback(self_report_id, answer)
            return self.feedback(feedback_id)
        if key:
            observation = context.get("observation") or context
            statement = (
                f"When {observation}, the user reported: {answer}"
                if observation
                else f"User feedback: {answer}"
            )
            self.learn_user_model(
                key,
                statement,
                evidence={
                    "question": item["question"],
                    "answer": answer,
                    "context": context,
                    "temporal": _temporal_profile(key, f"{statement} {answer}", context),
                },
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
        evidence_item = dict(evidence or {})
        if "temporal" not in evidence_item:
            evidence_item["temporal"] = _temporal_profile(key, statement, evidence_item)
        with self._connect() as db:
            row = db.execute("SELECT * FROM user_model WHERE model_key=?", (key,)).fetchone()
            if row:
                items = _loads(row["evidence_json"], [])
                if evidence_item:
                    items.append(evidence_item)
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
                    (key, statement, _json([evidence_item]), 0.43, 1, source, now, now),
                )
        return self.user_model_entry(key) or {}

    def user_model_entry(self, key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM user_model WHERE model_key=?", (key,)).fetchone()
        if not row:
            return None
        evidence = _loads(row["evidence_json"], [])
        temporal = {}
        for candidate in reversed(evidence):
            if isinstance(candidate, dict) and isinstance(candidate.get("temporal"), dict):
                temporal = dict(candidate["temporal"])
                break
        if not temporal:
            temporal = _temporal_profile(
                str(row["model_key"]),
                str(row["statement"]),
                {"valid_from": str(row["updated_at"])},
                now=_parse_datetime(str(row["updated_at"])) or datetime.now(timezone.utc),
            )
        now_dt = datetime.now(timezone.utc)
        valid_from = _parse_datetime(temporal.get("valid_from"))
        valid_until = _parse_datetime(temporal.get("valid_until"))
        is_current = valid_until is None or now_dt <= valid_until
        freshness = 1.0
        if str(temporal.get("scope") or "stable") == "temporary" and valid_from and valid_until:
            total = max(1.0, (valid_until - valid_from).total_seconds())
            remaining = max(0.0, (valid_until - now_dt).total_seconds())
            freshness = max(0.0, min(1.0, remaining / total))
        stored_confidence = float(row["confidence"])
        effective_confidence = (
            0.0 if not is_current else stored_confidence * (0.35 + 0.65 * freshness)
        )
        return {
            "key": str(row["model_key"]),
            "statement": str(row["statement"]),
            "confidence": round(effective_confidence, 4),
            "stored_confidence": stored_confidence,
            "evidence_count": int(row["evidence_count"]),
            "source": str(row["source"]),
            "evidence": evidence,
            "updated_at": str(row["updated_at"]),
            "temporal_scope": str(temporal.get("scope") or "stable"),
            "valid_from": temporal.get("valid_from"),
            "valid_until": temporal.get("valid_until"),
            "ttl_days": temporal.get("ttl_days"),
            "freshness": round(freshness, 4),
            "is_current": bool(is_current),
        }

    def user_model(self, *, include_expired: bool = False) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT model_key FROM user_model ORDER BY confidence DESC,evidence_count DESC,updated_at DESC"
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self.user_model_entry(str(row["model_key"]))) is not None
            and (include_expired or item.get("is_current", True))
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
                "DELETE FROM self_reports; DELETE FROM tool_events; DELETE FROM agent_meta;"
            )
        self._initialize()
