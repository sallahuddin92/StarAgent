from client.macagent_client import _StreamRenderer


def test_stream_renderer_compact_summarizes_steps(capsys):
    r = _StreamRenderer("compact")
    r.feed("[ORCHESTRATOR] Received task: Build app\n")
    r.feed("[BACKEND_AGENT] [ROUTER] BACKEND_AGENT -> LongCat-Flash-Chat\n")
    r.feed("[BACKEND_AGENT] [STEP] write_file(\"scratch/app/main.py\", \"print('x')\")\n")
    r.feed("[BACKEND_AGENT] [RESULT] Successfully wrote to scratch/app/main.py\n")
    r.feed("[TESTING_AGENT] [STEP] run_command(\"python3 main.py\", \"scratch/app\")\n")
    r.feed("[TESTING_AGENT] [RESULT] [Success] Output: ok\n")
    r.feed("[ORCHESTRATOR] Verifier Gate: PASS ✅ (5/5 checks)\n")
    r.finalize(trace_id="ma_123")

    out = capsys.readouterr().out
    assert "[ORCHESTRATOR] planning task..." in out
    assert "[ROUTER] BACKEND_AGENT -> LongCat-Flash-Chat" in out
    assert "[BACKEND_AGENT] write_file scratch/app/main.py ✅" in out
    assert "[TESTING_AGENT] run_command python3 main.py ✅" in out
    assert "[VERIFY] PASS ✅ (5/5 checks) ✅" in out
    assert "Full trace: ./scripts/staragent trace ma_123" in out


def test_stream_renderer_compact_no_docs_requirement_line(capsys):
    r = _StreamRenderer("compact")
    r.feed("[ORCHESTRATOR] Received task: Build app\n")
    r.finalize(trace_id=None)
    out = capsys.readouterr().out
    assert "[DOCS] no project-doc requirement detected" in out


def test_stream_renderer_quiet_minimal(capsys):
    r = _StreamRenderer("quiet")
    r.feed("[ORCHESTRATOR] Received task: Build app\n")
    r.feed("[ORCHESTRATOR] Dispatching to [BACKEND_AGENT]: Write code\n")
    r.feed("[BACKEND_AGENT] [STEP] write_file(\"scratch/app/main.py\", \"x\")\n")
    r.feed("[BACKEND_AGENT] [RESULT] Successfully wrote to scratch/app/main.py\n")
    r.finalize(trace_id="ma_999")
    out = capsys.readouterr().out
    assert "[TASK] started" in out
    assert "[TASK] in progress (BACKEND_AGENT)" in out
    assert "Full trace: ./scripts/staragent trace ma_999" in out
