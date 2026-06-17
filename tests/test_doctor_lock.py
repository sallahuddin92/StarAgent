import os
import sys
import unittest
import shutil
from unittest.mock import MagicMock, patch
from io import StringIO
from pathlib import Path
import tempfile
import fcntl
import json
import time

from cli.macagent import _run_doctor, _acquire_lock, _release_lock

class TestDoctorLock(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.tmp_dir.name)
        
        # Mock client health & routes
        self.client_mock = MagicMock()
        self.client_mock.health.return_value = {
            "ok": True,
            "service": "macagent-proxy",
            "version": "2.0.0",
        }
        self.client_mock.root_base_url = "http://127.0.0.1:8095"
        self.client_mock.v1_base_url = "http://127.0.0.1:8095/v1"
        self.client_mock._http.get.return_value.json.return_value = {
            "paths": {
                "/v1/docs/ingest": {},
                "/v1/docs/search": {},
                "/v1/docs/ask": {}
            }
        }

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch("cli.macagent.os.path.abspath")
    @patch("cli.macagent.os.path.dirname")
    @patch("cli.macagent.subprocess.run")
    def test_doctor_command_and_logging_and_cleanup(self, mock_run, mock_dirname, mock_abspath):
        # Setup path mocks to point to our temp directory as the project root
        mock_abspath.side_effect = lambda path: str(self.root_dir / "cli" / "macagent.py") if "macagent.py" in path else path
        mock_dirname.side_effect = lambda path: str(self.root_dir / "cli") if "macagent.py" in path else str(self.root_dir)

        # Ensure .runtime is created
        lock_dir = self.root_dir / ".runtime"
        lock_dir.mkdir(parents=True, exist_ok=True)

        # Mock subprocess to behave normally
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc

        # Mock the writing/running part of subprocess
        def fake_run(cmd, *args, **kwargs):
            log_file = kwargs.get('stdout')
            if log_file:
                log_file.write("ALL TESTS PASSED\n")
            return mock_proc
        
        mock_run.side_effect = fake_run

        # Execute doctor
        rc = _run_doctor(self.client_mock, {"project_id": "test"}, as_json=True)

        # Check identical command: ['./scripts/staragent', 'eval', 'baseline']
        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        self.assertEqual(called_args, ["./scripts/staragent", "eval", "baseline"])

        # Check environment contains parent lock marker and custom run_id
        called_env = mock_run.call_args[1].get("env", {})
        self.assertEqual(called_env.get("STARAGENT_LOCK_ACQUIRED_BY_PARENT"), "1")
        run_id = called_env.get("STARAGENT_EVAL_RUN_ID")
        self.assertIsNotNone(run_id)

        # Check log file was written with full baseline log
        log_path = lock_dir / "doctor_baseline_last.log"
        self.assertTrue(log_path.exists())
        self.assertEqual(log_path.read_text(encoding="utf-8"), "ALL TESTS PASSED\n")

    @patch("cli.macagent.os.path.abspath")
    @patch("cli.macagent.os.path.dirname")
    def test_doctor_lock_prevents_concurrent_baseline(self, mock_dirname, mock_abspath):
        # Setup path mocks
        mock_abspath.side_effect = lambda path: str(self.root_dir / "cli" / "macagent.py") if "macagent.py" in path else path
        mock_dirname.side_effect = lambda path: str(self.root_dir / "cli") if "macagent.py" in path else str(self.root_dir)

        lock_dir = self.root_dir / ".runtime"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "doctor.lock"

        # Manually acquire lock on the lock path to simulate concurrent run
        lock_file = open(lock_path, "w")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            # Capture output
            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                # Run doctor. It should fail to acquire lock and not run subprocess
                rc = _run_doctor(self.client_mock, {"project_id": "test"}, as_json=True)
                output_json = json.loads(sys.stdout.getvalue())
            finally:
                sys.stdout = old_stdout

            # Check that it did not succeed overall (since lock failed)
            self.assertEqual(output_json["status"], "failed")
            
            # Find the baseline check
            baseline_check = next(c for c in output_json["checks"] if c["name"] == "eval_baseline_quick_pass")
            self.assertFalse(baseline_check["ok"])
            self.assertIn("Another doctor or eval baseline process is already running", baseline_check["detail"])

        finally:
            # Release lock
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()

    @patch("cli.macagent.os.path.abspath")
    @patch("cli.macagent.os.path.dirname")
    @patch("cli.macagent.subprocess.run")
    def test_doctor_cleanup_stale_scratch_state(self, mock_run, mock_dirname, mock_abspath):
        # Setup path mocks
        mock_abspath.side_effect = lambda path: str(self.root_dir / "cli" / "macagent.py") if "macagent.py" in path else path
        mock_dirname.side_effect = lambda path: str(self.root_dir / "cli") if "macagent.py" in path else str(self.root_dir)

        # Mock subprocess
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc

        # Let's intercept the run env to get the run_id, and pre-create the scratch/trace paths
        run_id_ref = []
        def fake_run(cmd, *args, **kwargs):
            env = kwargs.get("env", {})
            run_id = env.get("STARAGENT_EVAL_RUN_ID")
            run_id_ref.append(run_id)
            
            # Pre-create scratch and trace files that should be deleted
            scratch_simple = self.root_dir / f"scratch/eval_simple_{run_id}"
            scratch_backend = self.root_dir / f"scratch/eval_backend_{run_id}"
            scratch_simple.mkdir(parents=True, exist_ok=True)
            scratch_backend.mkdir(parents=True, exist_ok=True)
            (scratch_simple / "main.py").write_text("hello", encoding="utf-8")
            (scratch_backend / "main.py").write_text("hello", encoding="utf-8")

            traces_dir = self.root_dir / ".runtime" / "traces"
            traces_dir.mkdir(parents=True, exist_ok=True)
            trace_file = traces_dir / f"eval-baseline-simple-{run_id}.jsonl"
            trace_file.write_text("{}", encoding="utf-8")
            return mock_proc

        mock_run.side_effect = fake_run

        # Run doctor
        _run_doctor(self.client_mock, {"project_id": "test"}, as_json=True)

        self.assertEqual(len(run_id_ref), 1)
        run_id = run_id_ref[0]

        # Verify cleanup deleted the files/directories
        scratch_simple = self.root_dir / f"scratch/eval_simple_{run_id}"
        scratch_backend = self.root_dir / f"scratch/eval_backend_{run_id}"
        trace_file = self.root_dir / ".runtime" / "traces" / f"eval-baseline-simple-{run_id}.jsonl"

        self.assertFalse(scratch_simple.exists())
        self.assertFalse(scratch_backend.exists())
        self.assertFalse(trace_file.exists())


if __name__ == "__main__":
    unittest.main()
