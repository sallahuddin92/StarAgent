import os
import unittest
import tempfile

from client.macagent_client import _resolve_client_path


class ClientPathResolutionTests(unittest.TestCase):
    def test_relative_path_resolves_against_client_cwd(self):
        old = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as td:
                os.chdir(td)
                out = _resolve_client_path(".")
                # macOS temp paths may canonicalize to /private/var/...
                self.assertEqual(os.path.realpath(out), os.path.realpath(os.path.abspath(td)))
        finally:
            os.chdir(old)


if __name__ == "__main__":
    unittest.main()
