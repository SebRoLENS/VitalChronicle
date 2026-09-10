from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"marker not found in {path}: {old[:160]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# AgentStore: dated subjective events + temporal personal context.
# ---------------------------------------------------------------------------
replace_once(
    "google_health_viewer/agent_store.py",
    "from datetime import datetime, timezone\n",
    "from datetime import datetime, timedelta, timezone\n",
)
replace_once(
    "google_health_viewer/agent_store.py",
    'AGENT_SCHEMA_VERSION = 1\n_TOKEN_RE = re.compile(r"[a-z0-9]+")\n',
    '''AGENT_SCHEMA_VERSION = 2\n_TOKEN_RE = re.compile(r"[a-z0-9]+")\n\n_TEMPORAL_KEY_TTLS = {\n    "current_training_goal": 90,\n    "sleep_schedule_context": 60,\n    "recent_training_context": 42,\n}\n_TEMPORAL_MARKERS = (\n    "da poco",\n    "recentemente",\n    "in questo periodo",\n    "al momento",\n    "attualmente",\n    "questa settimana",\n    "queste settimane",\n    "ho ricominciato",\n    "recently",\n    "right now",\n    "currently",\n    "these weeks",\n    "this week",\n    "just restarted",\n    "started again",\n)\n\n\ndef _parse_datetime(value: Any) -> datetime | None:\n    if not value:\n        return None\n    try:\n        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))\n    except ValueError:\n        return None\n    if parsed.tzinfo is None:\n        parsed = parsed.replace(tzinfo=timezone.utc)\n    return parsed.astimezone(timezone.utc)\n\n\ndef _temporal_profile(\n    key: str,\n    statement: str,\n    context: dict[str, Any] | None = None,\n    *,\n    now: datetime | None = None,\n) -> dict[str, Any]:\n    context = context or {}\n    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)\n    explicit_scope = str(context.get("temporal_scope") or "").strip().lower()\n    explicit_ttl = context.get("ttl_days")\n    ttl: int | None = None\n    if explicit_ttl is not None:\n        try:\n            ttl = max(1, min(3650, int(explicit_ttl)))\n        except (TypeError, ValueError):\n            ttl = None\n\n    combined = f"{key} {statement}".casefold()\n    temporary = explicit_scope == "temporary"\n    if explicit_scope == "stable":\n        temporary = False\n    elif ttl is not None or key in _TEMPORAL_KEY_TTLS or any(marker in combined for marker in _TEMPORAL_MARKERS):\n        temporary = True\n\n    if not temporary:\n        return {"scope": "stable", "valid_from": current.isoformat(), "valid_until": None, "ttl_days": None}\n\n    if ttl is None:\n        ttl = 42 if any(marker in combined for marker in _TEMPORAL_MARKERS) else _TEMPORAL_KEY_TTLS.get(key, 60)\n    valid_from = _parse_datetime(context.get("valid_from")) or current\n    valid_until = _parse_datetime(context.get("valid_until")) or (valid_from + timedelta(days=ttl))\n    return {\n        "scope": "temporary",\n        "valid_from": valid_from.isoformat(),\n        "valid_until": valid_until.isoformat(),\n        "ttl_days": ttl,\n    }\n''',
)
replace_once(
    "google_health_viewer/agent_store.py",
    '''                CREATE INDEX IF NOT EXISTS idx_agent_feedback\n                    ON feedback(thread_id, answered_at, created_at);\n                CREATE TABLE IF NOT EXISTS tool_events (\n''',
    '''                CREATE INDEX IF NOT EXISTS idx_agent_feedback\n                    ON feedback(thread_id, answered_at, created_at);\n                CREATE TABLE IF NOT EXISTS self_reports (\n                    report_id TEXT PRIMARY KEY,\n                    thread_id TEXT,\n                    category TEXT NOT NULL DEFAULT 'wellbeing',\n                    statement TEXT NOT NULL,\n                    intensity REAL,\n                    observed_at TEXT NOT NULL,\n                    context_json TEXT NOT NULL DEFAULT '{}',\n                    created_at TEXT NOT NULL,\n                    updated_at TEXT NOT NULL\n                );\n                CREATE INDEX IF NOT EXISTS idx_agent_self_reports\n                    ON self_reports(category, observed_at, created_at);\n                CREATE TABLE IF NOT EXISTS tool_events (\n''',
)
replace_once(
    "google_health_viewer/agent_store.py",
    '''    def ask_feedback(\n        self,\n        question: str,\n''',
    '''    def record_self_report(\n        self,\n        statement: str,\n        *,\n        category: str = "wellbeing",\n        thread_id: str | None = None,\n        observed_at: str | None = None,\n        intensity: float | None = None,\n        context: dict[str, Any] | None = None,\n    ) -> dict[str, Any]:\n        statement = statement.strip()\n        if not statement:\n            raise ValueError("Self-report statement cannot be empty")\n        now = datetime.now(timezone.utc)\n        created_at = now.isoformat()\n        observed = _parse_datetime(observed_at) or now\n        cutoff = (now - timedelta(hours=12)).isoformat()\n        normalized_category = str(category or "wellbeing").strip().lower()[:40] or "wellbeing"\n        with self._connect() as db:\n            duplicate = db.execute(\n                "SELECT report_id FROM self_reports WHERE COALESCE(thread_id,'')=COALESCE(?,'') "\n                "AND category=? AND statement=? AND created_at>=? ORDER BY created_at DESC LIMIT 1",\n                (thread_id, normalized_category, statement, cutoff),\n            ).fetchone()\n            if duplicate:\n                return self.self_report(str(duplicate["report_id"])) or {}\n            report_id = str(uuid.uuid4())\n            db.execute(\n                "INSERT INTO self_reports(report_id,thread_id,category,statement,intensity,observed_at,"\n                "context_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",\n                (\n                    report_id,\n                    thread_id,\n                    normalized_category,\n                    statement,\n                    float(intensity) if isinstance(intensity, (int, float)) else None,\n                    observed.isoformat(),\n                    _json(context or {}),\n                    created_at,\n                    created_at,\n                ),\n            )\n        return self.self_report(report_id) or {}\n\n    def self_report(self, report_id: str) -> dict[str, Any] | None:\n        with self._connect() as db:\n            row = db.execute("SELECT * FROM self_reports WHERE report_id=?", (report_id,)).fetchone()\n        if not row:\n            return None\n        return {\n            "report_id": str(row["report_id"]),\n            "thread_id": row["thread_id"],\n            "category": str(row["category"]),\n            "statement": str(row["statement"]),\n            "intensity": row["intensity"],\n            "observed_at": str(row["observed_at"]),\n            "context": _loads(row["context_json"], {}),\n            "created_at": str(row["created_at"]),\n            "updated_at": str(row["updated_at"]),\n        }\n\n    def recent_self_reports(\n        self, *, days: int = 30, limit: int = 50, category: str | None = None\n    ) -> list[dict[str, Any]]:\n        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, min(3650, int(days))))).isoformat()\n        params: list[Any] = [cutoff]\n        category_clause = ""\n        if category:\n            category_clause = "AND category=?"\n            params.append(str(category).strip().lower())\n        params.append(max(1, min(200, int(limit))))\n        with self._connect() as db:\n            rows = db.execute(\n                f"SELECT report_id FROM self_reports WHERE observed_at>=? {category_clause} "\n                "ORDER BY observed_at DESC LIMIT ?",\n                params,\n            ).fetchall()\n        return [item for row in rows if (item := self.self_report(str(row["report_id"]))) is not None]\n\n    def update_self_report_feedback(self, report_id: str, answer: str) -> bool:\n        item = self.self_report(report_id)\n        if not item:\n            return False\n        context = dict(item.get("context") or {})\n        context["follow_up_answer"] = answer.strip()\n        context["follow_up_answered_at"] = _now()\n        with self._connect() as db:\n            cursor = db.execute(\n                "UPDATE self_reports SET context_json=?,updated_at=? WHERE report_id=?",\n                (_json(context), _now(), report_id),\n            )\n        return bool(cursor.rowcount)\n\n    def has_recent_feedback_key(self, learning_key: str, *, days: int = 14) -> bool:\n        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()\n        with self._connect() as db:\n            row = db.execute(\n                "SELECT feedback_id FROM feedback WHERE learning_key=? AND created_at>=? "\n                "ORDER BY created_at DESC LIMIT 1",\n                (learning_key, cutoff),\n            ).fetchone()\n        return row is not None\n\n    def ask_feedback(\n        self,\n        question: str,\n''',
)
replace_once(
    "google_health_viewer/agent_store.py",
    '''        key = str(item.get("learning_key") or "").strip()\n        if key:\n            context = item.get("context") or {}\n            observation = context.get("observation") or context\n''',
    '''        key = str(item.get("learning_key") or "").strip()\n        context = item.get("context") or {}\n        self_report_id = str(context.get("self_report_id") or "").strip()\n        if self_report_id:\n            self.update_self_report_feedback(self_report_id, answer)\n            return self.feedback(feedback_id)\n        if key:\n            observation = context.get("observation") or context\n''',
)
replace_once(
    "google_health_viewer/agent_store.py",
    '''            self.learn_user_model(\n                key,\n                statement,\n                evidence={"question": item["question"], "answer": answer, "context": context},\n                source="feedback",\n            )\n''',
    '''            self.learn_user_model(\n                key,\n                statement,\n                evidence={\n                    "question": item["question"],\n                    "answer": answer,\n                    "context": context,\n                    "temporal": _temporal_profile(key, f"{statement} {answer}", context),\n                },\n                source="feedback",\n            )\n''',
)
replace_once(
    "google_health_viewer/agent_store.py",
    '''        now = _now()\n        with self._connect() as db:\n            row = db.execute("SELECT * FROM user_model WHERE model_key=?", (key,)).fetchone()\n            if row:\n                items = _loads(row["evidence_json"], [])\n                if evidence:\n                    items.append(evidence)\n''',
    '''        now = _now()\n        evidence_item = dict(evidence or {})\n        if "temporal" not in evidence_item:\n            evidence_item["temporal"] = _temporal_profile(key, statement, evidence_item)\n        with self._connect() as db:\n            row = db.execute("SELECT * FROM user_model WHERE model_key=?", (key,)).fetchone()\n            if row:\n                items = _loads(row["evidence_json"], [])\n                if evidence_item:\n                    items.append(evidence_item)\n''',
)
replace_once(
    "google_health_viewer/agent_store.py",
    '''                    (key, statement, _json([evidence] if evidence else []), 0.43, 1, source, now, now),\n''',
    '''                    (key, statement, _json([evidence_item]), 0.43, 1, source, now, now),\n''',
)
replace_once(
    "google_health_viewer/agent_store.py",
    '''        return {\n            "key": str(row["model_key"]),\n            "statement": str(row["statement"]),\n            "confidence": float(row["confidence"]),\n            "evidence_count": int(row["evidence_count"]),\n            "source": str(row["source"]),\n            "evidence": _loads(row["evidence_json"], []),\n            "updated_at": str(row["updated_at"]),\n        }\n\n    def user_model(self) -> list[dict[str, Any]]:\n''',
    '''        evidence = _loads(row["evidence_json"], [])\n        temporal = {}\n        for candidate in reversed(evidence):\n            if isinstance(candidate, dict) and isinstance(candidate.get("temporal"), dict):\n                temporal = dict(candidate["temporal"])\n                break\n        if not temporal:\n            temporal = _temporal_profile(\n                str(row["model_key"]),\n                str(row["statement"]),\n                {"valid_from": str(row["updated_at"])},\n                now=_parse_datetime(str(row["updated_at"])) or datetime.now(timezone.utc),\n            )\n        now_dt = datetime.now(timezone.utc)\n        valid_from = _parse_datetime(temporal.get("valid_from"))\n        valid_until = _parse_datetime(temporal.get("valid_until"))\n        is_current = valid_until is None or now_dt <= valid_until\n        freshness = 1.0\n        if str(temporal.get("scope") or "stable") == "temporary" and valid_from and valid_until:\n            total = max(1.0, (valid_until - valid_from).total_seconds())\n            remaining = max(0.0, (valid_until - now_dt).total_seconds())\n            freshness = max(0.0, min(1.0, remaining / total))\n        stored_confidence = float(row["confidence"])\n        effective_confidence = 0.0 if not is_current else stored_confidence * (0.35 + 0.65 * freshness)\n        return {\n            "key": str(row["model_key"]),\n            "statement": str(row["statement"]),\n            "confidence": round(effective_confidence, 4),\n            "stored_confidence": stored_confidence,\n            "evidence_count": int(row["evidence_count"]),\n            "source": str(row["source"]),\n            "evidence": evidence,\n            "updated_at": str(row["updated_at"]),\n            "temporal_scope": str(temporal.get("scope") or "stable"),\n            "valid_from": temporal.get("valid_from"),\n            "valid_until": temporal.get("valid_until"),\n            "ttl_days": temporal.get("ttl_days"),\n            "freshness": round(freshness, 4),\n            "is_current": bool(is_current),\n        }\n\n    def user_model(self, *, include_expired: bool = False) -> list[dict[str, Any]]:\n''',
)
replace_once(
    "google_health_viewer/agent_store.py",
    '''        return [\n            item\n            for row in rows\n            if (item := self.user_model_entry(str(row["model_key"]))) is not None\n        ]\n''',
    '''        return [\n            item\n            for row in rows\n            if (item := self.user_model_entry(str(row["model_key"]))) is not None\n            and (include_expired or item.get("is_current", True))\n        ]\n''',
)
replace_once(
    "google_health_viewer/agent_store.py",
    '''                "DELETE FROM tools; DELETE FROM user_model; DELETE FROM feedback; "\n                "DELETE FROM tool_events; DELETE FROM agent_meta;"\n''',
    '''                "DELETE FROM tools; DELETE FROM user_model; DELETE FROM feedback; "\n                "DELETE FROM self_reports; DELETE FROM tool_events; DELETE FROM agent_meta;"\n''',
)

# ---------------------------------------------------------------------------
# Tools: explicit self-report APIs and temporal association metadata.
# ---------------------------------------------------------------------------
replace_once(
    "google_health_viewer/agent_tools.py",
    '''    (\n        "search_tool_registry",\n''',
    '''    (\n        "record_self_report",\n        "Store an explicit dated subjective self-report in the local agent store without promoting it to a stable association.",\n        "agent.self_report",\n        _obj(\n            {\n                "statement": {"type": "string"},\n                "category": {"type": "string"},\n                "observed_at": {"type": "string"},\n                "intensity": {"type": "number", "minimum": 0, "maximum": 10},\n                "context": {"type": "object"},\n            },\n            ("statement",),\n        ),\n    ),\n    (\n        "get_recent_self_reports",\n        "Read recent explicit subjective self-reports from the local agent store.",\n        "agent.self_reports",\n        _obj(\n            {\n                "days": {"type": "integer", "minimum": 1, "maximum": 3650},\n                "limit": {"type": "integer", "minimum": 1, "maximum": 200},\n                "category": {"type": "string"},\n            }\n        ),\n    ),\n    (\n        "search_tool_registry",\n''',
)
replace_once(
    "google_health_viewer/agent_tools.py",
    '''                "evidence": {"type": "object"},\n            },\n            ("key", "statement"),\n''',
    '''                "evidence": {"type": "object"},\n                "temporal_scope": {"type": "string", "enum": ["stable", "temporary"]},\n                "ttl_days": {"type": "integer", "minimum": 1, "maximum": 3650},\n            },\n            ("key", "statement"),\n''',
)
replace_once(
    "google_health_viewer/agent_tools.py",
    '''    def _tool_get_user_model(self, args, **_):\n''',
    '''    def _tool_record_self_report(self, args, *, thread_id=None, **_):\n        item = self.agent_store.record_self_report(\n            str(args.get("statement") or ""),\n            category=str(args.get("category") or "wellbeing"),\n            thread_id=thread_id,\n            observed_at=str(args.get("observed_at") or "") or None,\n            intensity=args.get("intensity") if isinstance(args.get("intensity"), (int, float)) else None,\n            context=args.get("context") if isinstance(args.get("context"), dict) else {},\n        )\n        return {\n            "stored": bool(item),\n            "report": item,\n            "rule": "A single subjective report is a dated event, not a stable learned association.",\n        }\n\n    def _tool_get_recent_self_reports(self, args, **_):\n        return {\n            "reports": self.agent_store.recent_self_reports(\n                days=max(1, min(3650, int(args.get("days") or 30))),\n                limit=max(1, min(200, int(args.get("limit") or 50))),\n                category=str(args.get("category") or "").strip() or None,\n            ),\n            "rule": "Use recent self-reports as dated subjective context; do not treat them as diagnoses or stable traits.",\n        }\n\n    def _tool_get_user_model(self, args, **_):\n''',
)
replace_once(
    "google_health_viewer/agent_tools.py",
    '''        return {\n            "learned": self.agent_store.learn_user_model(\n                str(args.get("key") or ""),\n                statement,\n                evidence=args.get("evidence") if isinstance(args.get("evidence"), dict) else {},\n                source="agent",\n            )\n        }\n''',
    '''        evidence = dict(args.get("evidence") if isinstance(args.get("evidence"), dict) else {})\n        temporal_scope = str(args.get("temporal_scope") or "").strip().lower()\n        if temporal_scope:\n            evidence["temporal_scope"] = temporal_scope\n        if isinstance(args.get("ttl_days"), int):\n            evidence["ttl_days"] = int(args["ttl_days"])\n        return {\n            "learned": self.agent_store.learn_user_model(\n                str(args.get("key") or ""),\n                statement,\n                evidence=evidence,\n                source="agent",\n            )\n        }\n''',
)

# ---------------------------------------------------------------------------
# Base agent prompt/context: separate events from learned associations.
# ---------------------------------------------------------------------------
replace_once(
    "google_health_viewer/agent_runtime.py",
    '''5. Subjective user feedback can teach personal tolerance, preferences and associations. It never\n   proves that a physiological state is medically safe and never suppresses objective safety advice.\n6. Ask a targeted feedback question only when the answer would materially reduce uncertainty or\n   improve future personalization. Avoid routine or repetitive questionnaires.\n7. Separate measured observations, deterministic calculations, user-reported context, learned\n''',
    '''5. An explicit current subjective statement such as feeling tired, sore, sleepy, stressed or unusually energetic\n   is a dated self-report event. Store it locally as an event; do not immediately promote one report to a stable trait.\n6. Ask at most one targeted follow-up when it would materially improve interpretation of a new self-report. Avoid\n   routine or repetitive questionnaires. Follow-up details remain attached to that dated report unless repeated evidence\n   later supports a genuine association.\n7. Learned personal context has time semantics. Temporary context such as "recently restarted training", current goals\n   or a short-lived schedule change must lose weight with age and stop being used after its validity window. Never use\n   expired context as if it were current. Stable preferences/associations require repeated evidence or an explicitly\n   stable user statement. Subjective context never proves physiological safety or suppresses objective safety advice.\n8. Separate measured observations, deterministic calculations, user-reported context, learned\n''',
)
replace_once(
    "google_health_viewer/agent_runtime.py",
    '''8. Never diagnose disease, change treatment, or present wearable-derived scores as medical clearance.\n9. Readiness, cardio load, target load, training status and resilience returned by tools are\n''',
    '''9. Never diagnose disease, change treatment, or present wearable-derived scores as medical clearance.\n10. Readiness, cardio load, target load, training status and resilience returned by tools are\n''',
)
replace_once(
    "google_health_viewer/agent_runtime.py",
    '''10. When confidence or coverage is low, state that clearly.\n''',
    '''11. When confidence or coverage is low, state that clearly.\n''',
)
replace_once(
    "google_health_viewer/agent_runtime.py",
    '''            "personal_model": self.agent_store.user_model()[:20],\n            "safe_tool_count": len(self.tools.tool_schemas()),\n''',
    '''            "personal_model": self.agent_store.user_model()[:20],\n            "recent_self_reports": self.agent_store.recent_self_reports(days=30, limit=20),\n            "safe_tool_count": len(self.tools.tool_schemas()),\n''',
)

# ---------------------------------------------------------------------------
# V2 runtime: deterministic capture + one useful follow-up with anti-spam.
# ---------------------------------------------------------------------------
replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    '''def _without_factory_creation(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:\n''',
    '''_SELF_REPORT_PATTERNS = {\n    "fatigue": (\n        "mi sento stanco", "mi sento stanca", "sono stanco", "sono stanca",\n        "mi sento affaticato", "mi sento affaticata", "sono affaticato", "sono affaticata",\n        "i feel tired", "i'm tired", "i am tired", "i feel fatigued",\n    ),\n    "sleepiness": ("ho sonno", "mi sento assonnato", "mi sento assonnata", "i feel sleepy", "i'm sleepy"),\n    "soreness": ("sono indolenzito", "sono indolenzita", "dolori muscolari", "muscoli indolenziti", "i feel sore", "muscle soreness"),\n    "stress": ("mi sento stressato", "mi sento stressata", "sono stressato", "sono stressata", "i feel stressed", "i'm stressed"),\n    "energy": ("mi sento energico", "mi sento energica", "pieno di energia", "piena di energia", "i feel energetic", "full of energy"),\n}\n\n\ndef _detect_self_report(question: str) -> dict[str, str] | None:\n    text = question.strip()\n    folded = text.casefold()\n    for category, markers in _SELF_REPORT_PATTERNS.items():\n        if any(marker in folded for marker in markers):\n            return {"category": category, "statement": text}\n    return None\n\n\ndef _self_report_follow_up(category: str) -> tuple[str, str]:\n    if category == "fatigue":\n        return (\n            _("Is today's tiredness mainly muscular fatigue, sleepiness, or a more general lack of energy?"),\n            _("This helps distinguish training-related fatigue from sleepiness or more general low energy in future personal analyses."),\n        )\n    if category == "sleepiness":\n        return (\n            _("Would you describe the sleepiness as mild, moderate, or strong, and is it unusual for this time of day?"),\n            _("This adds useful subjective context to sleep and recovery measurements without turning it into a diagnosis."),\n        )\n    if category == "soreness":\n        return (\n            _("Is the soreness mainly in muscles you trained recently, and would you call it mild, moderate, or strong?"),\n            _("This helps relate future subjective recovery reports to recent training without treating soreness as a medical diagnosis."),\n        )\n    if category == "stress":\n        return (\n            _("Does today's stress feel mainly mental, physical, or mixed?"),\n            _("This helps keep subjective stress context separate from wearable-derived physiological strain."),\n        )\n    return (\n        _("Is this feeling unusual for you today, and would you rate it as mild, moderate, or strong?"),\n        _("One concise detail can make future personal interpretation more specific without creating a stable trait from one report."),\n    )\n\n\ndef _without_factory_creation(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:\n''',
)
replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    '''        initial = self._initial_context(snapshot)\n        request = question.strip() or _(\n            "Analyse my complete local health history and identify the most useful personal patterns."\n        )\n        initial["tool_factory_decision_hint"] = _factory_hint(request)\n''',
    '''        request = question.strip() or _(\n            "Analyse my complete local health history and identify the most useful personal patterns."\n        )\n        detected_self_report = _detect_self_report(request)\n        captured_self_report = None\n        if detected_self_report is not None:\n            captured_self_report = self.agent_store.record_self_report(\n                detected_self_report["statement"],\n                category=detected_self_report["category"],\n                thread_id=thread_id,\n                context={"source": "conversation", "explicit_self_report": True},\n            )\n            event(_("Subjective self-report saved locally as a dated event."))\n            learning_key = f"self_report_detail:{detected_self_report['category']}"\n            if captured_self_report and not self.agent_store.has_recent_feedback_key(learning_key, days=14):\n                feedback_question, feedback_reason = _self_report_follow_up(detected_self_report["category"])\n                queued = self.agent_store.ask_feedback(\n                    feedback_question,\n                    thread_id=thread_id,\n                    reason=feedback_reason,\n                    learning_key=learning_key,\n                    context={\n                        "self_report_id": captured_self_report.get("report_id"),\n                        "category": detected_self_report["category"],\n                        "feedback_mode": "self_report_detail",\n                    },\n                )\n                if queued:\n                    event(_("One targeted follow-up was queued to improve future personalisation."))\n        initial = self._initial_context(snapshot)\n        if captured_self_report:\n            initial["current_self_report"] = captured_self_report\n            initial["self_report_rule"] = (\n                "This was stored as a dated subjective event. Do not promote it to a stable association from one occurrence."\n            )\n        initial["tool_factory_decision_hint"] = _factory_hint(request)\n''',
)

# ---------------------------------------------------------------------------
# UI: expired context remains inspectable; recent self-reports are visible separately.
# ---------------------------------------------------------------------------
replace_once(
    "google_health_viewer/agent_ui.py",
    '''            f"{_('Updated')}: {_pretty_detail(item.get('updated_at'))}",\n            "",\n            _("Learned association"),\n''',
    '''            f"{_('Updated')}: {_pretty_detail(item.get('updated_at'))}",\n            f"{_('Temporal scope')}: {_pretty_detail(item.get('temporal_scope'))}",\n            f"{_('Valid until')}: {_pretty_detail(item.get('valid_until'))}",\n            f"{_('Freshness')}: {float(item.get('freshness') or 0) * 100:.0f}%",\n            f"{_('Current')}: {_pretty_detail(item.get('is_current'))}",\n            "",\n            _("Learned association"),\n''',
)
replace_once(
    "google_health_viewer/agent_ui.py",
    '''def _show_detail_dialog(parent: QWidget, title: str, text: str) -> None:\n''',
    '''def _self_report_detail_text(item: dict[str, Any]) -> str:\n    context = item.get("context") if isinstance(item.get("context"), dict) else {}\n    return "\\n".join(\n        [\n            f"{_('When')}: {_pretty_detail(item.get('observed_at'))}",\n            f"{_('Category')}: {_pretty_detail(item.get('category'))}",\n            f"{_('Intensity')}: {_pretty_detail(item.get('intensity'))}",\n            "",\n            _("Self-report"),\n            _pretty_detail(item.get("statement")),\n            "",\n            _("Follow-up detail"),\n            _pretty_detail(context.get("follow_up_answer")),\n        ]\n    )\n\n\ndef _show_detail_dialog(parent: QWidget, title: str, text: str) -> None:\n''',
)
replace_once(
    "google_health_viewer/agent_ui.py",
    '''    window.agent_model_tree.clear()\n    for item in store.user_model():\n''',
    '''    window.agent_model_tree.clear()\n    for item in store.user_model(include_expired=True):\n''',
)
replace_once(
    "google_health_viewer/agent_ui.py",
    '''                str(item["source"]),\n            ]\n        )\n''',
    '''                str(item["source"]),\n                _("current") if item.get("is_current", True) else _("expired"),\n            ]\n        )\n''',
)
replace_once(
    "google_health_viewer/agent_ui.py",
    '''        window.agent_model_tree.addTopLevelItem(row)\n\n    window.agent_events_list.clear()\n''',
    '''        window.agent_model_tree.addTopLevelItem(row)\n\n    if hasattr(window, "agent_reports_tree"):\n        window.agent_reports_tree.clear()\n        for item in store.recent_self_reports(days=90, limit=100):\n            context = item.get("context") if isinstance(item.get("context"), dict) else {}\n            row = QTreeWidgetItem(\n                [\n                    str(item.get("observed_at") or "").replace("T", " ")[:16],\n                    str(item.get("statement") or ""),\n                    str(item.get("category") or ""),\n                    str(context.get("follow_up_answer") or ""),\n                ]\n            )\n            row.setData(0, Qt.UserRole, item)\n            row.setToolTip(1, str(item.get("statement") or ""))\n            window.agent_reports_tree.addTopLevelItem(row)\n\n    window.agent_events_list.clear()\n''',
)
replace_once(
    "google_health_viewer/agent_ui.py",
    '''    window.agent_model_tree.setHeaderLabels(\n        [_("Learned about you"), _("Confidence"), _("Evidence"), _("Source")]\n    )\n''',
    '''    window.agent_model_tree.setHeaderLabels(\n        [_("Learned about you"), _("Confidence"), _("Evidence"), _("Source"), _("Status")]\n    )\n''',
)
replace_once(
    "google_health_viewer/agent_ui.py",
    '''    model_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)\n    model_layout.addWidget(window.agent_model_tree, 1)\n    model_actions = QHBoxLayout()\n''',
    '''    model_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)\n    model_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)\n    model_layout.addWidget(window.agent_model_tree, 1)\n\n    reports_label = QLabel(_("Recent self-reports"))\n    reports_label.setObjectName("chatSectionTitle")\n    model_layout.addWidget(reports_label)\n    reports_hint = QLabel(\n        _(\n            "Dated subjective observations are kept separate from learned associations. One report does not become a stable trait."\n        )\n    )\n    reports_hint.setObjectName("pageSubtitle")\n    reports_hint.setWordWrap(True)\n    model_layout.addWidget(reports_hint)\n    window.agent_reports_tree = QTreeWidget()\n    window.agent_reports_tree.setHeaderLabels(\n        [_("When"), _("Self-report"), _("Category"), _("Follow-up detail")]\n    )\n    window.agent_reports_tree.setAlternatingRowColors(True)\n    window.agent_reports_tree.setWordWrap(True)\n    window.agent_reports_tree.setTextElideMode(Qt.ElideNone)\n    reports_header = window.agent_reports_tree.header()\n    reports_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)\n    reports_header.setSectionResizeMode(1, QHeaderView.Stretch)\n    reports_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)\n    reports_header.setSectionResizeMode(3, QHeaderView.Stretch)\n    window.agent_reports_tree.setMaximumHeight(190)\n    model_layout.addWidget(window.agent_reports_tree)\n\n    model_actions = QHBoxLayout()\n''',
)
replace_once(
    "google_health_viewer/agent_ui.py",
    '''    def delete_selected_tool() -> None:\n''',
    '''    def show_selected_report_details() -> None:\n        item = _selected_payload(window.agent_reports_tree)\n        if not item:\n            return\n        _show_detail_dialog(window, _("Self-report details"), _self_report_detail_text(item))\n\n    def delete_selected_tool() -> None:\n''',
)
replace_once(
    "google_health_viewer/agent_ui.py",
    '''    window.agent_model_tree.itemDoubleClicked.connect(\n        lambda _item, _column: show_selected_model_details()\n    )\n    forget.clicked.connect(forget_selected)\n''',
    '''    window.agent_model_tree.itemDoubleClicked.connect(\n        lambda _item, _column: show_selected_model_details()\n    )\n    window.agent_reports_tree.itemDoubleClicked.connect(\n        lambda _item, _column: show_selected_report_details()\n    )\n    forget.clicked.connect(forget_selected)\n''',
)
replace_once(
    "google_health_viewer/agent_ui.py",
    '''                    "This deletes learned tools, feedback and personal associations. Your health "\n''',
    '''                    "This deletes learned tools, feedback, self-reports and personal associations. Your health "\n''',
)

# ---------------------------------------------------------------------------
# Documentation.
# ---------------------------------------------------------------------------
replace_once(
    "docs/personal-health-agent.md",
    '''Answers are explicit user-reported context. They may teach preferences or associations such as subjective tolerance of a recurring workload, but they are never treated as evidence that a physiological state is medically safe. Subjective feedback cannot override safety-oriented language or convert a wearable-derived estimate into medical clearance.\n''',
    '''Answers are explicit user-reported context. Spontaneous statements such as “I feel tired today” are stored first as dated **self-report events**, not immediately promoted to stable traits. VitalChronicle may queue at most one targeted follow-up when one concise detail would materially improve future interpretation. Repeated, concordant observations can later support a learned association.\n\nPersonal context carries time semantics. Stable preferences can remain active, while temporary statements such as “I recently restarted the gym”, a current training goal, or a short-lived schedule change receive a validity window and freshness decay. Expired temporary context remains inspectable locally but is not injected into new agent analyses as current information.\n\nSubjective reports and learned associations are never treated as evidence that a physiological state is medically safe. Subjective feedback cannot override safety-oriented language or convert a wearable-derived estimate into medical clearance.\n''',
)

# ---------------------------------------------------------------------------
# Focused regression tests.
# ---------------------------------------------------------------------------
Path("tests/test_agent_self_reports_temporal.py").write_text(
    '''from __future__ import annotations\n\nfrom datetime import datetime, timedelta, timezone\nfrom pathlib import Path\n\nfrom google_health_viewer.agent_runtime_v2 import _detect_self_report\nfrom google_health_viewer.agent_store import AgentStore\n\n\ndef test_explicit_fatigue_statement_is_detected():\n    item = _detect_self_report("Mi sento stanco oggi")\n    assert item is not None\n    assert item["category"] == "fatigue"\n\n\ndef test_general_question_is_not_misclassified_as_self_report():\n    assert _detect_self_report("Perché una persona può sentirsi stanca?") is None\n\n\ndef test_self_report_feedback_stays_attached_to_event_not_user_model(tmp_path: Path):\n    store = AgentStore(tmp_path / "agent.sqlite3")\n    report = store.record_self_report(\n        "Mi sento stanco oggi", category="fatigue", thread_id="thread-1"\n    )\n    feedback = store.ask_feedback(\n        "Che tipo di stanchezza?",\n        thread_id="thread-1",\n        learning_key="self_report_detail:fatigue",\n        context={"self_report_id": report["report_id"], "feedback_mode": "self_report_detail"},\n    )\n    store.answer_feedback(feedback["feedback_id"], "Soprattutto muscolare")\n\n    refreshed = store.self_report(report["report_id"])\n    assert refreshed is not None\n    assert refreshed["context"]["follow_up_answer"] == "Soprattutto muscolare"\n    assert store.user_model() == []\n\n\ndef test_recent_training_statement_gets_temporary_validity(tmp_path: Path):\n    store = AgentStore(tmp_path / "agent.sqlite3")\n    item = store.learn_user_model(\n        "current_training_goal",\n        "User feedback: ho ricominciato palestra da poco",\n        evidence={"answer": "Ho ricominciato palestra da poco"},\n        source="feedback",\n    )\n    assert item["temporal_scope"] == "temporary"\n    assert item["is_current"] is True\n    assert item["valid_until"] is not None\n    valid_from = datetime.fromisoformat(item["valid_from"])\n    valid_until = datetime.fromisoformat(item["valid_until"])\n    assert 35 <= (valid_until - valid_from).days <= 50\n\n\ndef test_expired_temporary_context_is_not_injected_but_remains_inspectable(tmp_path: Path):\n    store = AgentStore(tmp_path / "agent.sqlite3")\n    past_start = datetime.now(timezone.utc) - timedelta(days=90)\n    past_end = datetime.now(timezone.utc) - timedelta(days=30)\n    store.learn_user_model(\n        "old_training_context",\n        "Temporary training context",\n        evidence={\n            "temporal": {\n                "scope": "temporary",\n                "valid_from": past_start.isoformat(),\n                "valid_until": past_end.isoformat(),\n                "ttl_days": 60,\n            }\n        },\n        source="feedback",\n    )\n    assert store.user_model() == []\n    all_items = store.user_model(include_expired=True)\n    assert len(all_items) == 1\n    assert all_items[0]["is_current"] is False\n    assert all_items[0]["confidence"] == 0.0\n\n\ndef test_feedback_key_antispam(tmp_path: Path):\n    store = AgentStore(tmp_path / "agent.sqlite3")\n    assert store.has_recent_feedback_key("self_report_detail:fatigue") is False\n    store.ask_feedback(\n        "Che tipo di stanchezza?", learning_key="self_report_detail:fatigue"\n    )\n    assert store.has_recent_feedback_key("self_report_detail:fatigue") is True\n''',
    encoding="utf-8",
)

print("self-report temporal patch prepared")
