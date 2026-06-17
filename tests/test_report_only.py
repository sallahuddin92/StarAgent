"""Tests for report-only task output.

Validates that report-only tasks emit structured [REPORT] lines
and that the compact stream renders them.
"""
from __future__ import annotations

import pytest
from client.macagent_client import _StreamRenderer
from app.multi_agent import _is_report_only_task
from app.trace_logger import TraceLogger


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------

class TestReportOnlyDetection:
    def test_report_marker(self):
        assert _is_report_only_task("Read the repo and report discovered commands") is True

    def test_summarize_marker(self):
        assert _is_report_only_task("Summarize the codebase") is True

    def test_recommend_marker(self):
        assert _is_report_only_task("Recommend one smallest safe fix") is True

    def test_do_not_modify(self):
        assert _is_report_only_task("Do not modify files yet") is True

    def test_read_only(self):
        assert _is_report_only_task("Read-only audit of the project") is True

    def test_stop_with_report(self):
        assert _is_report_only_task("Otherwise stop with a report") is True

    def test_normal_task(self):
        assert _is_report_only_task("Create a FastAPI backend with /health") is False

    def test_build_task(self):
        assert _is_report_only_task("Build a calculator app") is False


# ---------------------------------------------------------------------------
# Compact stream rendering of [REPORT] lines
# ---------------------------------------------------------------------------

class TestReportCompactRendering:
    def test_report_lines_rendered(self):
        renderer = _StreamRenderer("compact")
        lines = [
            "[ORCHESTRATOR] [REPORT] === Task Report ===\n",
            "[ORCHESTRATOR] [REPORT] files_read: 3\n",
            "[ORCHESTRATOR] [REPORT]   - /repo/Makefile\n",
            "[ORCHESTRATOR] [REPORT]   - /repo/README.md\n",
            "[ORCHESTRATOR] [REPORT]   - /repo/apps/backend/requirements.txt\n",
            "[ORCHESTRATOR] [REPORT] discovered_commands: 2\n",
            "[ORCHESTRATOR] [REPORT]   - make dev\n",
            "[ORCHESTRATOR] [REPORT]   - make test\n",
            "[ORCHESTRATOR] [REPORT] no files modified ✅\n",
            "[ORCHESTRATOR] [REPORT] recommendation: review discovered commands before applying changes\n",
            "[ORCHESTRATOR] [REPORT] === End Report ===\n",
        ]
        import io
        import sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            for line in lines:
                renderer.feed(line)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert "REPORT" in output
        assert "files_read" in output
        assert "no files modified" in output
        assert "Makefile" in output

    def test_no_report_for_normal_lines(self):
        renderer = _StreamRenderer("compact")
        import io, sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            renderer.feed("[ORCHESTRATOR] # Multi-Agent Execution Report\n")
        finally:
            sys.stdout = old_stdout
        # The "# Multi-Agent..." header should be suppressed in compact
        assert "Multi-Agent Execution Report" not in captured.getvalue()


# ---------------------------------------------------------------------------
# Trace logger final_report event
# ---------------------------------------------------------------------------

class TestTraceLoggerFinalReport:
    def test_log_final_report_event(self, tmp_path):
        logger = TraceLogger("test_report_task")
        logger.path = str(tmp_path / "test.jsonl")
        logger.log_final_report(
            report="Task completed with no changes.",
            files_read=["/repo/Makefile", "/repo/README.md"],
            files_modified=[],
            status="completed",
        )
        assert len(logger.events) == 1
        event = logger.events[0]
        assert event.event_type == "final_report"
        assert event.status == "completed"
        assert "/repo/Makefile" in event.args["files_read"]
        assert event.args["files_modified"] == []
        assert "Task completed" in event.output_preview

    def test_final_report_no_write_status(self, tmp_path):
        logger = TraceLogger("test_no_write")
        logger.path = str(tmp_path / "test.jsonl")
        logger.log_final_report(
            report="Report only.",
            files_read=["/repo/README.md"],
            files_modified=[],
            status="completed",
        )
        assert logger.events[0].args["files_modified"] == []
