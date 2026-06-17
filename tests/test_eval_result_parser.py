from app.eval_result_parser import detect_agent_status, has_honest_stress_diagnostics


def test_failed_status_beats_completed_text():
    out = """
    [ORCHESTRATOR] some completed-looking text
    [x_agent_status] completed
    ... later verifier failed ...
    [x_agent_status] failed
    """
    assert detect_agent_status(out) == "failed"


def test_stress_expected_fail_diagnostics_detected():
    out = """
    Verifier Gate: FAIL ❌
    Failures:
      - Required files missing: scratch/eval_issue_tracker/backend/main.py
      - Required command not verified as successful: npm run build
    [x_agent_status] failed
    """
    assert has_honest_stress_diagnostics(out) is True

