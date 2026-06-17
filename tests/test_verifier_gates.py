"""
Regression tests for StarAgent verifier gates.

Run with: python3 -m pytest tests/test_verifier_gates.py -v
"""

import os
import sys
import tempfile

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.eval_harness import EvalVerifier, verify_no_false_completion


class TestVerifierGateFiles:
    """Gate 1: Required files must exist."""

    def test_missing_folder_cannot_be_completed(self):
        v = EvalVerifier()
        result = v.verify_task(required_files=["scratch/nonexistent_folder_abc123/main.py"])
        assert not result.passed, "Missing folder should fail verification"
        assert any("missing" in f.lower() for f in result.failures)

    def test_existing_file_passes(self):
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"print('hello')")
            path = f.name
        try:
            v = EvalVerifier()
            result = v.verify_task(required_files=[path])
            assert result.passed, f"Existing file should pass: {result.failures}"
        finally:
            os.unlink(path)

    def test_multiple_files_one_missing(self):
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"ok")
            existing = f.name
        try:
            v = EvalVerifier()
            result = v.verify_task(required_files=[existing, "scratch/ghost_file_xyz.py"])
            assert not result.passed, "Should fail if any file is missing"
        finally:
            os.unlink(existing)


class TestVerifierGateTests:
    """Gate 3: Tests/build must pass."""

    def test_failed_pytest_cannot_be_completed(self):
        v = EvalVerifier()
        result = v.verify_task(
            test_results=[{"command": "pytest", "output": "FAILED test_main.py::test_health"}]
        )
        assert not result.passed, "Failed pytest should fail verification"

    def test_failed_npm_build_cannot_be_completed(self):
        v = EvalVerifier()
        result = v.verify_task(
            test_results=[{"command": "npm run build", "output": "ERROR in ./src/App.jsx\nModule build failed"}]
        )
        assert not result.passed, "Failed npm build should fail verification"

    def test_passing_pytest_succeeds(self):
        v = EvalVerifier()
        result = v.verify_task(
            test_results=[{"command": "pytest", "output": "4 passed in 0.20s"}]
        )
        assert result.passed, f"Passing pytest should pass: {result.failures}"


class TestVerifierGateReport:
    """Gate 4: Generic report must be rejected when evidence exists."""

    def test_generic_report_rejected_when_evidence_exists(self):
        v = EvalVerifier()
        result = v.verify_task(
            final_report="Task complete. The objective has been addressed based on execution history.",
            tool_outputs=["from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')"],
            required_files=[],  # skip file check
        )
        assert not result.passed, "Generic report should be rejected when tool outputs exist"

    def test_evidence_grounded_report_passes(self):
        v = EvalVerifier()
        result = v.verify_task(
            final_report="The FastAPI app uses `app = FastAPI()` and defines a `/health` endpoint.",
            tool_outputs=["from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')"],
        )
        assert result.passed, f"Evidence-grounded report should pass: {result.failures}"


class TestFalseCompletionPrevention:
    """Verify that false completion is detected and prevented."""

    def test_completed_with_missing_files_is_false(self):
        ok = verify_no_false_completion(
            status="completed",
            required_files=["scratch/definitely_does_not_exist_abc.py"],
            final_report="All done!",
        )
        assert not ok, "Should prevent false completion when files are missing"

    def test_completed_with_existing_files_is_ok(self):
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"ok")
            path = f.name
        try:
            ok = verify_no_false_completion(
                status="completed",
                required_files=[path],
                final_report="Created file and verified.",
            )
            assert ok, "Should allow completion when files exist"
        finally:
            os.unlink(path)

    def test_failed_status_always_passes_check(self):
        ok = verify_no_false_completion(
            status="failed",
            required_files=["scratch/missing.py"],
            final_report="Task failed.",
        )
        assert ok, "Failed status should not be flagged as false completion"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
