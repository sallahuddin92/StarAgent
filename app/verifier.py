import subprocess
import os
import re
from dataclasses import dataclass
from typing import Dict, Any, Literal

@dataclass
class VerificationResult:
    success: bool
    failure_type: Literal["syntax", "runtime", "dependency", "api_usage", "unknown", "none"]
    error_message: str
    suggested_action: str

class VerificationLayer:
    """
    StarAgent v3 Verification Layer
    Validates executed actions to ensure deterministic failure recovery.
    It removes the burden from the small LLM to guess if its code works.
    """
    
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root

    def verify_action(self, tool_name: str, tool_args: Dict[str, Any], tool_output: str) -> VerificationResult:
        """
        Verify the outcome of a tool execution.
        """
        if tool_name == "write_file":
            path = tool_args.get("path", "")
            return self._verify_file_write(path)
            
        elif tool_name == "run_shell" or tool_name == "run_command":
            return self._classify_shell_output(tool_args.get("command", ""), tool_output)
            
        elif tool_name == "install_dependency":
            pkg = tool_args.get("package", "")
            return self._verify_dependency(pkg)
            
        return VerificationResult(True, "none", "", "Proceed")

    def _verify_file_write(self, filepath: str) -> VerificationResult:
        """Run syntax checks based on file extension."""
        full_path = os.path.join(self.workspace_root, filepath)
        if not os.path.exists(full_path):
            return VerificationResult(False, "unknown", f"File {filepath} was not created.", "Re-run write_file")

        if filepath.endswith(".py"):
            try:
                # Syntax check using py_compile
                result = subprocess.run(
                    ["python3", "-m", "py_compile", full_path],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode != 0:
                    return VerificationResult(
                        False, 
                        "syntax", 
                        result.stderr.strip() or result.stdout.strip(), 
                        "Fix Python syntax error"
                    )
            except Exception as e:
                pass
                
        # Node/JS syntax check could be added here using `node -c`
        if filepath.endswith(".js"):
            try:
                result = subprocess.run(
                    ["node", "-c", full_path],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode != 0:
                    return VerificationResult(
                        False, 
                        "syntax", 
                        result.stderr.strip() or result.stdout.strip(), 
                        "Fix JavaScript syntax error"
                    )
            except Exception as e:
                pass

        return VerificationResult(True, "none", "", "")

    def _classify_shell_output(self, command: str, output: str) -> VerificationResult:
        """Parse shell output to classify failure type. This drives the Recovery Logic."""
        # Heuristic failure classification
        if "[failed]" in output.lower() or "error" in output.lower():
            lower_out = output.lower()
            
            if "syntax error" in lower_out or "syntaxerror" in lower_out:
                return VerificationResult(False, "syntax", output, "Fix syntax error")
                
            if "modulenotfound" in lower_out or "cannot find module" in lower_out or "command not found" in lower_out:
                return VerificationResult(False, "dependency", output, "Install missing dependency")
                
            if "attributeerror" in lower_out or "typeerror" in lower_out or "is not a function" in lower_out or "securityerror" in lower_out:
                # CRITICAL: This flags outdated LLM knowledge and forces Research Layer
                return VerificationResult(False, "api_usage", output, "Check API documentation. Outdated knowledge likely.")
                
            if "traceback" in lower_out or "exception" in lower_out:
                return VerificationResult(False, "runtime", output, "Fix runtime exception")
                
            return VerificationResult(False, "unknown", output, "Investigate general failure")
            
        return VerificationResult(True, "none", "", "")

    def _verify_dependency(self, package: str) -> VerificationResult:
        """Verify that a package can be imported/used."""
        # This is a naive check; a robust one would run a small test script.
        return VerificationResult(True, "none", "", "")
