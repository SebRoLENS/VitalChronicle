from google_health_viewer.agent_ui import _tool_detail_text, _user_model_detail_text


def test_tool_details_keep_full_name_description_and_pipeline():
    long_name = "analyze_cardio_load_deep_sleep_response_with_personal_recovery"
    text = _tool_detail_text(
        {
            "name": long_name,
            "kind": "learned",
            "version": 1,
            "status": "active",
            "capability": "analysis.temporal.event.response.recovery",
            "description": "A deliberately long description that must remain fully inspectable.",
            "confidence": 0.7,
            "use_count": 3,
            "pipeline": [{"op": "call_tool", "tool": "calculate_cardio_load"}],
        }
    )
    assert long_name in text
    assert "deliberately long description" in text
    assert "calculate_cardio_load" in text


def test_user_model_details_keep_full_statement_and_evidence():
    statement = "A long learned association that should never be hidden by the compact table view."
    text = _user_model_detail_text(
        {
            "key": "recovery_preference",
            "statement": statement,
            "confidence": 0.63,
            "evidence_count": 2,
            "source": "feedback",
            "evidence": [{"answer": "Example personal feedback"}],
        }
    )
    assert statement in text
    assert "Example personal feedback" in text
