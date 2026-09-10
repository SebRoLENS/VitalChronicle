from pathlib import Path

p = Path('google_health_viewer/agent_tools.py')
text = p.read_text()
text = text.replace(
    'MAX_SERIES_POINTS = 360\nMAX_LEARNED_STEPS = 12\n',
    'MAX_SERIES_POINTS = 360\nMAX_LEARNED_STEPS = 12\nMIN_ACUTE_LOAD_OBSERVED_DAYS = 5\nMIN_CHRONIC_LOAD_OBSERVED_DAYS = 21\n',
    1,
)
old_load = '''    def _load(self, right: date) -> dict[str, Any]:
        daily, method = self._cardio_daily(right - timedelta(days=34), right)
        acute_days = {(right - timedelta(days=i)).isoformat() for i in range(7)}
        chronic_days = {(right - timedelta(days=i)).isoformat() for i in range(7, 35)}
        acute = sum(value for day, value in daily.items() if day in acute_days)
        chronic = sum(value for day, value in daily.items() if day in chronic_days) / 4
        return {
            "acute_7d": round(acute, 2),
            "chronic_weekly_equivalent_28d": round(chronic, 2),
            "acute_chronic_ratio": None if chronic <= 1e-9 else round(acute / chronic, 3),
            "observed_days": len(daily),
            "method": method,
        }
'''
new_load = '''    def _load(self, right: date) -> dict[str, Any]:
        daily, method = self._cardio_daily(right - timedelta(days=34), right)
        acute_days = {(right - timedelta(days=i)).isoformat() for i in range(7)}
        chronic_days = {(right - timedelta(days=i)).isoformat() for i in range(7, 35)}
        acute_values = [value for day, value in daily.items() if day in acute_days]
        chronic_values = [value for day, value in daily.items() if day in chronic_days]
        acute_observed = len(acute_values)
        chronic_observed = len(chronic_values)
        acute_sufficient = acute_observed >= MIN_ACUTE_LOAD_OBSERVED_DAYS
        chronic_sufficient = chronic_observed >= MIN_CHRONIC_LOAD_OBSERVED_DAYS
        acute = (
            statistics.fmean(acute_values) * 7
            if acute_sufficient and acute_values
            else None
        )
        chronic = (
            statistics.fmean(chronic_values) * 7
            if chronic_sufficient and chronic_values
            else None
        )
        ratio = (
            None
            if acute is None or chronic is None or chronic <= 1e-9
            else acute / chronic
        )
        if not acute_sufficient:
            history_status = "insufficient_acute_history"
        elif not chronic_sufficient:
            history_status = "insufficient_chronic_history"
        else:
            history_status = "sufficient"
        return {
            "acute_7d": None if acute is None else round(acute, 2),
            "chronic_weekly_equivalent_28d": None if chronic is None else round(chronic, 2),
            "acute_chronic_ratio": None if ratio is None else round(ratio, 3),
            "acute_observed_total": round(sum(acute_values), 2),
            "chronic_observed_total": round(sum(chronic_values), 2),
            "acute_observed_days": acute_observed,
            "chronic_observed_days": chronic_observed,
            "acute_coverage": round(acute_observed / 7, 3),
            "chronic_coverage": round(chronic_observed / 28, 3),
            "acute_history_sufficient": acute_sufficient,
            "chronic_history_sufficient": chronic_sufficient,
            "history_status": history_status,
            "observed_days": len(daily),
            "method": method + "; weekly equivalents are normalized over observed days only when coverage is sufficient",
        }
'''
assert old_load in text
text = text.replace(old_load, new_load, 1)
old_tools = '''    def _tool_calculate_acute_load(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        item = self._load(right)
        return {
            "acute_load_7d": item["acute_7d"],
            "confidence": _confidence(item["observed_days"], 35),
            "method": item["method"],
        }

    def _tool_calculate_chronic_load(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        item = self._load(right)
        return {
            "chronic_load_weekly_equivalent_28d": item["chronic_weekly_equivalent_28d"],
            "confidence": _confidence(item["observed_days"], 35),
            "method": item["method"],
        }
'''
new_tools = '''    def _tool_calculate_acute_load(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        item = self._load(right)
        sufficient = bool(item["acute_history_sufficient"])
        return {
            "acute_load_7d": item["acute_7d"],
            "observed_total": item["acute_observed_total"],
            "observed_days": item["acute_observed_days"],
            "coverage": item["acute_coverage"],
            "status": "available" if sufficient else "insufficient_history",
            "confidence": _confidence(item["acute_observed_days"], 7) if sufficient else 0.0,
            "method": item["method"],
            "limitations": None if sufficient else "At least 5 observed days in the recent 7-day window are required; missing days are not treated as zero.",
        }

    def _tool_calculate_chronic_load(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        item = self._load(right)
        sufficient = bool(item["chronic_history_sufficient"])
        return {
            "chronic_load_weekly_equivalent_28d": item["chronic_weekly_equivalent_28d"],
            "observed_total": item["chronic_observed_total"],
            "observed_days": item["chronic_observed_days"],
            "coverage": item["chronic_coverage"],
            "status": "available" if sufficient else "insufficient_history",
            "confidence": _confidence(item["chronic_observed_days"], 28) if sufficient else 0.0,
            "method": item["method"],
            "limitations": None if sufficient else "At least 21 observed days in the prior 28-day window are required; missing days are not treated as zero.",
        }
'''
assert old_tools in text
text = text.replace(old_tools, new_tools, 1)
old_target = '''    def _target(self, right: date) -> dict[str, Any]:
        load = self._load(right)
        readiness = self._readiness(right)
        chronic = float(load["chronic_weekly_equivalent_28d"])
        score = readiness["score"]
        factor = 1.0 if score is None else 0.70 + 0.006 * float(score)
        centre = chronic * factor
        return {
            "target_weekly_load": {
                "lower": round(centre * 0.85, 2),
                "upper": round(centre * 1.15, 2),
            },
            "current_acute_load": load["acute_7d"],
            "readiness": readiness,
            "confidence": round(
                min(readiness["confidence"], _confidence(load["observed_days"], 35)), 3
            ),
            "method": "VitalChronicle heuristic: personal chronic load scaled by readiness with ±15% range; not Fitbit Target Load.",
        }
'''
new_target = '''    def _target(self, right: date) -> dict[str, Any]:
        load = self._load(right)
        readiness = self._readiness(right)
        chronic = load["chronic_weekly_equivalent_28d"]
        if chronic is None:
            return {
                "target_weekly_load": None,
                "current_acute_load": load["acute_7d"],
                "readiness": readiness,
                "status": "insufficient_history",
                "confidence": 0.0,
                "method": "VitalChronicle target load requires a usable prior 28-day chronic-load estimate.",
                "limitations": "Not enough observed chronic-load history; no target is estimated and missing days are not treated as zero.",
            }
        score = readiness["score"]
        factor = 1.0 if score is None else 0.70 + 0.006 * float(score)
        centre = float(chronic) * factor
        return {
            "target_weekly_load": {
                "lower": round(centre * 0.85, 2),
                "upper": round(centre * 1.15, 2),
            },
            "current_acute_load": load["acute_7d"],
            "readiness": readiness,
            "status": "available",
            "confidence": round(
                min(readiness["confidence"], _confidence(load["chronic_observed_days"], 28)), 3
            ),
            "method": "VitalChronicle heuristic: personal chronic load scaled by readiness with ±15% range; not Fitbit Target Load.",
            "limitations": None,
        }
'''
assert old_target in text
text = text.replace(old_target, new_target, 1)
old_resilience = '''    def _resilience(self, right: date) -> dict[str, Any]:
        readiness = self._readiness(right)
        regularity = self._tool_calculate_sleep_regularity(
            {"start": (right - timedelta(days=41)).isoformat(), "end": right.isoformat()}
        )
        load = self._load(right)
        ratio = load["acute_chronic_ratio"]
        r = float(readiness["score"] if readiness["score"] is not None else 50)
        s = float(
            regularity["regularity_score"] if regularity["regularity_score"] is not None else 50
        )
        balance = 70 if ratio is None else max(0.0, min(100.0, 100 - abs(float(ratio) - 1) * 70))
        subjective = [
            x
            for x in self.agent_store.user_model()
            if "load" in x["key"].lower() or "fatigue" in x["key"].lower()
        ]
        bonus = min(5.0, sum(float(x["confidence"]) * 1.5 for x in subjective))
        score = max(0.0, min(100.0, 0.50 * r + 0.25 * s + 0.25 * balance + bonus))
        return {
            "score": round(score, 1),
            "label": "optimal" if score >= 75 else "balanced" if score >= 50 else "low",
            "components": {
                "readiness": round(r, 1),
                "sleep_regularity": round(s, 1),
                "load_balance": round(balance, 1),
            },
            "subjective_personalisation_used": bool(subjective),
            "method": "VitalChronicle medium-term resilience heuristic; feedback cannot remove objective safety warnings.",
        }
'''
new_resilience = '''    def _resilience(self, right: date) -> dict[str, Any]:
        readiness = self._readiness(right)
        regularity = self._tool_calculate_sleep_regularity(
            {"start": (right - timedelta(days=41)).isoformat(), "end": right.isoformat()}
        )
        load = self._load(right)
        ratio = load["acute_chronic_ratio"]
        r = readiness["score"]
        s = regularity["regularity_score"]
        balance = (
            None
            if ratio is None
            else max(0.0, min(100.0, 100 - abs(float(ratio) - 1) * 70))
        )
        values = {
            "readiness": None if r is None else float(r),
            "sleep_regularity": None if s is None else float(s),
            "load_balance": balance,
        }
        weights = {"readiness": 0.50, "sleep_regularity": 0.25, "load_balance": 0.25}
        available = {key: value for key, value in values.items() if value is not None}
        weight_sum = sum(weights[key] for key in available)
        objective_score = (
            None
            if not available
            else sum(float(value) * weights[key] for key, value in available.items()) / weight_sum
        )
        subjective = [
            x
            for x in self.agent_store.user_model()
            if "load" in x["key"].lower() or "fatigue" in x["key"].lower()
        ]
        bonus = min(5.0, sum(float(x["confidence"]) * 1.5 for x in subjective))
        score = (
            None
            if objective_score is None
            else max(0.0, min(100.0, objective_score + bonus))
        )
        load_balance_label = (
            "unavailable_insufficient_history"
            if balance is None
            else "balanced"
            if balance >= 85
            else "moderately_unbalanced"
            if balance >= 50
            else "unbalanced"
        )
        return {
            "score": None if score is None else round(score, 1),
            "label": None
            if score is None
            else ("optimal" if score >= 75 else "balanced" if score >= 50 else "low"),
            "components": {
                "readiness": None if r is None else round(float(r), 1),
                "sleep_regularity": None if s is None else round(float(s), 1),
                "load_balance": None if balance is None else round(balance, 1),
            },
            "component_status": {
                "readiness": "available" if r is not None else "unavailable",
                "sleep_regularity": "available" if s is not None else "unavailable",
                "load_balance": "available" if balance is not None else load["history_status"],
            },
            "load_balance_label": load_balance_label,
            "load_history": {
                "acute_observed_days": load["acute_observed_days"],
                "chronic_observed_days": load["chronic_observed_days"],
                "acute_coverage": load["acute_coverage"],
                "chronic_coverage": load["chronic_coverage"],
                "acute_chronic_ratio": ratio,
            },
            "effective_weights": {
                key: round(weights[key] / weight_sum, 3) for key in available
            } if weight_sum else {},
            "subjective_personalisation_used": bool(subjective),
            "method": "VitalChronicle medium-term resilience heuristic: readiness 50%, sleep regularity 25%, load balance 25%; unavailable components are omitted and remaining weights are renormalized. Feedback cannot remove objective safety warnings.",
            "limitations": (
                "Load balance is unavailable because acute/chronic history is insufficient; it is not treated as neutral or zero."
                if balance is None
                else None
            ),
        }
'''
assert old_resilience in text
text = text.replace(old_resilience, new_resilience, 1)
p.write_text(text)

runtime = Path('google_health_viewer/agent_runtime.py')
rtext = runtime.read_text()
needle = '10. When confidence or coverage is low, state that clearly.\n'
replacement = '10. When confidence or coverage is low, state that clearly. A missing/None score component means unavailable evidence, never a neutral or zero value.\n'
assert needle in rtext
runtime.write_text(rtext.replace(needle, replacement, 1))

Path('tests/test_agent_resilience_load_history.py').write_text('''from datetime import date, timedelta\n\nimport pytest\n\nfrom google_health_viewer.agent_tools import SafeToolExecutor\n\n\nclass _Store:\n    def user_model(self):\n        return []\n\n\ndef _executor():\n    item = SafeToolExecutor.__new__(SafeToolExecutor)\n    item.agent_store = _Store()\n    return item\n\n\ndef test_load_ratio_requires_enough_chronic_history():\n    executor = _executor()\n    right = date(2026, 9, 10)\n    start = right - timedelta(days=15)\n    daily = {(start + timedelta(days=i)).isoformat(): 100.0 for i in range(16)}\n    executor._cardio_daily = lambda _left, _right: (daily, "test load")\n\n    load = executor._load(right)\n\n    assert load["acute_observed_days"] == 7\n    assert load["chronic_observed_days"] == 9\n    assert load["acute_7d"] == pytest.approx(700.0)\n    assert load["chronic_weekly_equivalent_28d"] is None\n    assert load["acute_chronic_ratio"] is None\n    assert load["history_status"] == "insufficient_chronic_history"\n\n\ndef test_load_ratio_uses_observed_day_normalization_with_sufficient_coverage():\n    executor = _executor()\n    right = date(2026, 9, 10)\n    start = right - timedelta(days=34)\n    daily = {(start + timedelta(days=i)).isoformat(): 100.0 for i in range(35)}\n    executor._cardio_daily = lambda _left, _right: (daily, "test load")\n\n    load = executor._load(right)\n\n    assert load["acute_observed_days"] == 7\n    assert load["chronic_observed_days"] == 28\n    assert load["acute_7d"] == pytest.approx(700.0)\n    assert load["chronic_weekly_equivalent_28d"] == pytest.approx(700.0)\n    assert load["acute_chronic_ratio"] == pytest.approx(1.0)\n    assert load["history_status"] == "sufficient"\n\n\ndef test_resilience_reweights_when_load_history_is_insufficient():\n    executor = _executor()\n    executor._readiness = lambda _right: {"score": 56.8}\n    executor._tool_calculate_sleep_regularity = lambda _args: {"regularity_score": 14.9}\n    executor._load = lambda _right: {\n        "acute_chronic_ratio": None,\n        "history_status": "insufficient_chronic_history",\n        "acute_observed_days": 7,\n        "chronic_observed_days": 9,\n        "acute_coverage": 1.0,\n        "chronic_coverage": 9 / 28,\n    }\n\n    result = executor._resilience(date(2026, 9, 10))\n\n    assert result["components"]["load_balance"] is None\n    assert result["component_status"]["load_balance"] == "insufficient_chronic_history"\n    assert result["load_balance_label"] == "unavailable_insufficient_history"\n    assert result["effective_weights"] == {"readiness": 0.667, "sleep_regularity": 0.333}\n    assert result["score"] == pytest.approx(42.8)\n    assert "not treated as neutral or zero" in result["limitations"]\n\n\ndef test_zero_load_balance_is_labeled_unbalanced_not_neutral():\n    executor = _executor()\n    executor._readiness = lambda _right: {"score": 60.0}\n    executor._tool_calculate_sleep_regularity = lambda _args: {"regularity_score": 60.0}\n    executor._load = lambda _right: {\n        "acute_chronic_ratio": 2.5,\n        "history_status": "sufficient",\n        "acute_observed_days": 7,\n        "chronic_observed_days": 28,\n        "acute_coverage": 1.0,\n        "chronic_coverage": 1.0,\n    }\n\n    result = executor._resilience(date(2026, 9, 10))\n\n    assert result["components"]["load_balance"] == 0.0\n    assert result["load_balance_label"] == "unbalanced"\n    assert result["component_status"]["load_balance"] == "available"\n''')
