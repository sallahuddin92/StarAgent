"""Tests for cli/status_dashboard.py (v0.8.7 TUI Overhaul)."""

import json
from unittest.mock import patch, MagicMock
from pathlib import Path
from dataclasses import asdict

from cli.status_dashboard import (
    DashboardReport,
    SectionStatus,
    check_proxy_health,
    check_ollama,
    check_prompt_mode,
    check_memory_authority,
    read_runtime_eval_report,
    read_prompt_audit_file,
    run_doctor_checks,
    build_dashboard,
    print_dashboard,
    cmd_status,
    cmd_eval_status,
    REPORT_PATH,
    AUDIT_PATH,
)


# ---------------------------------------------------------------------------
# Test DashboardReport serialization
# ---------------------------------------------------------------------------

class TestDashboardReport:
    def test_report_serializable(self):
        r = DashboardReport(
            timestamp="2026-01-01T00:00:00Z",
            proxy={"status": "ok", "service": "test", "version": "1.0", "detail": "test"},
            ollama={"status": "ok", "detail": "Available", "models": [], "model_count": 0},
            prompt_mode={"status": "ok", "mode": "compact", "max_memory_tokens": 1024,
                         "include_historical": False, "detail": "mode=compact"},
            memory_authority={"status": "ok", "counts": {"active": 3, "stale": 0, "superseded": 0,
                               "rejected": 1, "unknown": 0}, "total": 4, "detail": "4 items"},
            prompt_audit={"status": "info", "detail": "No audit file", "audit": {}},
            runtime_eval={"status": "ok", "detail": "PASS=5 FAIL=0", "report": {}, "summary": {}},
            doctor={"status": "ok", "passed": 4, "failed": 0, "total": 4, "verdict": "PASS",
                    "checks": [], "detail": "PASS (4/4)"},
            suggested_action="All good",
        )
        d = asdict(r)
        assert d["proxy"]["status"] == "ok"
        assert d["doctor"]["passed"] == 4
        # Must be JSON-serializable
        json.dumps(d)


class TestSectionStatus:
    def test_section_creation(self):
        s = SectionStatus("Test", "ok", ["line1"], ["detail1"])
        assert s.title == "Test"
        assert s.status == "ok"


# ---------------------------------------------------------------------------
# Test proxy health check
# ---------------------------------------------------------------------------

class TestCheckProxyHealth:
    @patch("cli.status_dashboard.httpx.Client")
    def test_healthy(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = MagicMock()
        mock_response.json.return_value = {"service": "macagent-proxy", "version": "2.0.0"}
        mock_client.get.return_value = mock_response
        result = check_proxy_health()
        assert result["status"] == "ok"
        assert "macagent-proxy" in result["detail"]

    @patch("cli.status_dashboard.httpx.Client")
    def test_unreachable(self, mock_client_class):
        from httpx import ConnectError
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = ConnectError("Connection refused")
        result = check_proxy_health()
        assert result["status"] == "error"
        assert "unreachable" in result["detail"].lower()


# ---------------------------------------------------------------------------
# Test Ollama check
# ---------------------------------------------------------------------------

class TestCheckOllama:
    @patch("cli.status_dashboard.httpx.get")
    def test_available(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"models": [{"name": "llama3"}, {"name": "mistral"}]},
        )
        result = check_ollama()
        assert result["status"] == "ok"
        assert result["model_count"] == 2

    @patch("cli.status_dashboard.httpx.get")
    def test_unavailable(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        result = check_ollama()
        assert result["status"] == "skip"

    @patch("cli.status_dashboard.httpx.get")
    def test_bad_status(self, mock_get):
        mock_get.return_value = MagicMock(status_code=502)
        result = check_ollama()
        assert result["status"] == "skip"

    @patch("cli.status_dashboard.httpx.get")
    def test_empty_models(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"models": []},
        )
        result = check_ollama()
        assert result["status"] == "ok"
        assert result["model_count"] == 0


# ---------------------------------------------------------------------------
# Test file readers for missing files
# ---------------------------------------------------------------------------

class TestReadRuntimeEvalReport:
    def test_missing_file(self, tmp_path: Path):
        # Temporarily override REPORT_PATH
        import cli.status_dashboard as sd
        original = sd.REPORT_PATH
        sd.REPORT_PATH = tmp_path / "nonexistent.json"
        try:
            result = read_runtime_eval_report()
            assert result["status"] == "info"
            assert "No eval report" in result["detail"]
        finally:
            sd.REPORT_PATH = original

    def test_valid_file(self, tmp_path: Path):
        report_path = tmp_path / "runtime_eval_report.json"
        report_path.write_text(json.dumps({
            "timestamp": "2026-01-01T00:00:00Z",
            "total_scenarios": 6, "passed": 5, "failed": 0,
            "skipped": 1, "blocked": 0, "errors": 0, "score": "5/5",
            "results": [],
        }))
        import cli.status_dashboard as sd
        original = sd.REPORT_PATH
        sd.REPORT_PATH = report_path
        try:
            result = read_runtime_eval_report()
            assert result["status"] == "ok"
            assert result["summary"]["passed"] == 5
        finally:
            sd.REPORT_PATH = original


class TestReadPromptAuditFile:
    def test_missing_file(self, tmp_path: Path):
        import cli.status_dashboard as sd
        original = sd.AUDIT_PATH
        sd.AUDIT_PATH = tmp_path / "nonexistent.json"
        try:
            result = read_prompt_audit_file()
            assert result["status"] == "info"
        finally:
            sd.AUDIT_PATH = original

    def test_valid_file(self, tmp_path: Path):
        audit_path = tmp_path / "last_prompt_audit.json"
        audit_path.write_text(json.dumps({
            "audit": {"mode": "compact", "estimated_tokens_total": 250, "sections_kept": ["active"]},
        }))
        import cli.status_dashboard as sd
        original = sd.AUDIT_PATH
        sd.AUDIT_PATH = audit_path
        try:
            result = read_prompt_audit_file()
            assert result["status"] == "ok"
            assert "compact" in result["detail"]
        finally:
            sd.AUDIT_PATH = original

    def test_corrupt_file(self, tmp_path: Path):
        audit_path = tmp_path / "last_prompt_audit.json"
        audit_path.write_text("not json")
        import cli.status_dashboard as sd
        original = sd.AUDIT_PATH
        sd.AUDIT_PATH = audit_path
        try:
            result = read_prompt_audit_file()
            assert result["status"] == "error"
        finally:
            sd.AUDIT_PATH = original


# ---------------------------------------------------------------------------
# Test doctor checks
# ---------------------------------------------------------------------------

class TestRunDoctorChecks:
    @patch("cli.status_dashboard.check_proxy_health")
    @patch("cli.status_dashboard.check_ollama")
    @patch("cli.status_dashboard.check_prompt_mode")
    @patch("cli.status_dashboard.read_runtime_eval_report")
    def test_all_pass(self, mock_eval, mock_pm, mock_ollama, mock_proxy):
        mock_proxy.return_value = {"status": "ok", "service": "test", "version": "1.0", "detail": "ok"}
        mock_ollama.return_value = {"status": "ok", "detail": "Available", "models": [], "model_count": 1}
        mock_pm.return_value = {"status": "ok", "mode": "compact", "max_memory_tokens": 1024,
                                "include_historical": False, "detail": "ok"}
        mock_eval.return_value = {"status": "ok", "detail": "PASS=5", "report": {}, "summary": {}}
        result = run_doctor_checks()
        assert result["verdict"] == "PASS"
        assert result["passed"] == 4
        assert result["failed"] == 0

    @patch("cli.status_dashboard.check_proxy_health")
    @patch("cli.status_dashboard.check_ollama")
    @patch("cli.status_dashboard.check_prompt_mode")
    @patch("cli.status_dashboard.read_runtime_eval_report")
    def test_skip_ollama_not_fail(self, mock_eval, mock_pm, mock_ollama, mock_proxy):
        """Ollama unavailable is a SKIP, not a FAIL."""
        mock_proxy.return_value = {"status": "ok", "service": "test", "version": "1.0", "detail": "ok"}
        mock_ollama.return_value = {"status": "skip", "detail": "Refused", "models": [], "model_count": 0}
        mock_pm.return_value = {"status": "ok", "mode": "compact", "max_memory_tokens": 1024,
                                "include_historical": False, "detail": "ok"}
        mock_eval.return_value = {"status": "ok", "detail": "PASS=5", "report": {}, "summary": {}}
        result = run_doctor_checks()
        assert result["verdict"] == "PASS"
        assert result["passed"] == 3
        assert result["failed"] == 0


# ---------------------------------------------------------------------------
# Test build_dashboard and cmd_status
# ---------------------------------------------------------------------------

class TestBuildDashboard:
    @patch("cli.status_dashboard.check_proxy_health")
    @patch("cli.status_dashboard.check_ollama")
    @patch("cli.status_dashboard.check_prompt_mode")
    @patch("cli.status_dashboard.check_memory_authority")
    @patch("cli.status_dashboard.read_runtime_eval_report")
    @patch("cli.status_dashboard.read_prompt_audit_file")
    def test_dashboard_healthy(self, mock_audit, mock_eval, mock_mem,
                                mock_pm, mock_ollama, mock_proxy):
        mock_proxy.return_value = {"status": "ok", "service": "test", "version": "1.0", "detail": "test v1.0"}
        mock_ollama.return_value = {"status": "ok", "detail": "Available", "models": [], "model_count": 2}
        mock_pm.return_value = {"status": "ok", "mode": "compact", "max_memory_tokens": 1024,
                                "include_historical": False, "detail": "mode=compact"}
        mock_mem.return_value = {"status": "ok", "counts": {"active": 5, "stale": 0, "superseded": 0,
                                  "rejected": 0, "unknown": 0}, "total": 5, "detail": "5 items"}
        mock_eval.return_value = {"status": "ok", "detail": "PASS=5 FAIL=0", "report": {}, "summary": {}}
        mock_audit.return_value = {"status": "info", "detail": "No audit file", "audit": {}}

        report = build_dashboard()
        assert report.proxy["status"] == "ok"
        assert report.ollama["status"] == "ok"
        assert "PASS" in report.doctor["verdict"]

    @patch("cli.status_dashboard.check_proxy_health")
    def test_proxy_unreachable_skips_dependent(self, mock_proxy):
        mock_proxy.return_value = {"status": "error", "service": "", "version": "", "detail": "Server unreachable"}
        report = build_dashboard()
        assert report.proxy["status"] == "error"
        assert report.ollama["status"] == "skip"
        assert report.prompt_mode["status"] == "skip"
        assert report.memory_authority["status"] == "skip"


class TestCmdStatus:
    def test_exit_zero_on_skip(self):
        """Status returns 0 when only optional deps missing."""
        class Args:
            verbose = False
            as_json = False

        with patch("cli.status_dashboard.check_proxy_health") as mock_proxy:
            mock_proxy.return_value = {"status": "ok", "service": "test", "version": "1.0", "detail": "ok"}
            with patch("cli.status_dashboard.check_ollama") as mock_ollama:
                mock_ollama.return_value = {"status": "skip", "detail": "Refused", "models": [], "model_count": 0}
                with patch("cli.status_dashboard.check_prompt_mode") as mock_pm:
                    mock_pm.return_value = {"status": "ok", "mode": "compact", "max_memory_tokens": 1024,
                                            "include_historical": False, "detail": "ok"}
                    with patch("cli.status_dashboard.read_runtime_eval_report") as mock_eval:
                        mock_eval.return_value = {"status": "ok", "detail": "PASS=5", "report": {}, "summary": {}}
                        rc = cmd_status(Args())
                        assert rc == 0

    def test_exit_one_on_failure(self):
        """Status returns 1 when doctor has real failures."""
        class Args:
            verbose = False
            as_json = False

        with patch("cli.status_dashboard.check_proxy_health") as mock_proxy:
            mock_proxy.return_value = {"status": "ok", "service": "test", "version": "1.0", "detail": "ok"}
            with patch("cli.status_dashboard.check_ollama") as mock_ollama:
                mock_ollama.return_value = {"status": "ok", "detail": "Available", "models": [], "model_count": 1}
                with patch("cli.status_dashboard.check_prompt_mode") as mock_pm:
                    mock_pm.return_value = {"status": "fail", "mode": "", "max_memory_tokens": 0,
                                            "include_historical": False, "detail": "error"}
                    with patch("cli.status_dashboard.read_runtime_eval_report") as mock_eval:
                        mock_eval.return_value = {"status": "ok", "detail": "PASS=5", "report": {}, "summary": {}}
                        rc = cmd_status(Args())
                        assert rc == 1

    def test_exit_two_on_server_unavailable(self):
        """Status returns 2 when proxy unreachable."""
        class Args:
            verbose = False
            as_json = False

        with patch("cli.status_dashboard.check_proxy_health") as mock_proxy:
            mock_proxy.return_value = {"status": "error", "service": "", "version": "", "detail": "Server unreachable"}
            rc = cmd_status(Args())
            assert rc == 2

    def test_json_output_is_valid(self):
        """cmd_status with --json produces valid JSON."""
        class Args:
            verbose = False
            as_json = True

        with patch("cli.status_dashboard.check_proxy_health") as mock_proxy:
            mock_proxy.return_value = {"status": "ok", "service": "test", "version": "1.0", "detail": "ok"}
            with patch("cli.status_dashboard.check_ollama"):
                mock_ollama = MagicMock()
                with patch("cli.status_dashboard.check_prompt_mode") as mock_pm:
                    mock_pm.return_value = {"status": "ok", "mode": "compact", "max_memory_tokens": 1024,
                                            "include_historical": False, "detail": "ok"}
                    with patch("cli.status_dashboard.read_runtime_eval_report") as mock_eval:
                        mock_eval.return_value = {"status": "ok", "detail": "PASS=5", "report": {}, "summary": {}}
                        with patch("cli.status_dashboard.read_prompt_audit_file"):
                            rc = cmd_status(Args())
                            assert rc == 0

