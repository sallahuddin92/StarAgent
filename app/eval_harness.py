"""
StarAgent Eval Harness — strict verifier gate logic.

Provides the EvalVerifier that enforces hard rules before
any task can be marked as 'completed'.
"""

import os
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class VerifierResult:
    passed: bool
    checks: Dict[str, bool] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)

    def summary(self) -> str:
        total = len(self.checks)
        passed = sum(self.checks.values())
        status = "PASS ✅" if self.passed else "FAIL ❌"
        lines = [f"Verifier Gate: {status} ({passed}/{total} checks)"]
        for name, ok in self.checks.items():
            icon = "✅" if ok else "❌"
            lines.append(f"  {icon} {name}")
        if self.failures:
            lines.append("Failures:")
            for f in self.failures:
                lines.append(f"  - {f}")
        return "\n".join(lines)


class EvalVerifier:
    """
    Strict verifier gate. No task returns 'completed' unless all gates pass.
    """

    def verify_task(
        self,
        *,
        required_files: Optional[List[str]] = None,
        required_commands_ran: Optional[List[str]] = None,
        required_output_keywords: Optional[List[str]] = None,
        required_semantics: Optional[List[Dict[str, Any]]] = None,
        test_results: Optional[List[Dict[str, Any]]] = None,
        final_report: str = "",
        tool_outputs: Optional[List[str]] = None,
    ) -> VerifierResult:
        checks = {}
        failures = []

        # Gate 1: Required files exist
        if required_files:
            missing = []
            for fp in required_files:
                key = f"file_exists:{fp}"
                exists = os.path.exists(fp)
                checks[key] = exists
                if not exists:
                    missing.append(fp)
            if missing:
                failures.append(f"Required files missing: {', '.join(missing)}")

        # Gate 2: Required commands were executed and succeeded
        if required_commands_ran:
            for cmd in required_commands_ran:
                key = f"command_ran:{cmd[:60]}"
                found_success = False
                if tool_outputs:
                    for out in tool_outputs:
                        # Command is verified if the name matches and it has [Success]
                        if cmd in out and "[Success]" in out:
                            found_success = True
                            break
                        # Handle case where only tool name (e.g. pytest) is in output
                        if cmd.split()[0] in out and "[Success]" in out:
                            found_success = True
                            break
                checks[key] = found_success
                if not found_success:
                    failures.append(f"Required command not verified as successful: {cmd}")

        # Gate 3: Output contains required keywords (from actual execution)
        if required_output_keywords:
            for kw in required_output_keywords:
                key = f"output_contains:{kw}"
                found = False
                if tool_outputs:
                    for out in tool_outputs:
                        # Only look for keywords in execution results
                        if "[Success]" in out and "Output:" in out:
                            # Extract the part after "Output:" to avoid matching the command string itself
                            execution_output = out.split("Output:", 1)[1]
                            if kw.lower() in execution_output.lower():
                                found = True
                                break
                checks[key] = found
                if not found:
                    failures.append(f"Required output keyword missing in execution results: {kw}")

        # Gate 4: Semantic checks (regex matching in specific files)
        if required_semantics:
            for sem in required_semantics:
                pattern = sem.get("pattern")
                target_file = sem.get("file")
                label = sem.get("label", pattern)
                negative = sem.get("negative", False)
                
                key = f"semantic:{label}"
                found = False
                
                if target_file and os.path.exists(target_file):
                    try:
                        with open(target_file, "r") as f:
                            content = f.read()
                            if re.search(pattern, content, re.I | re.M):
                                found = True
                    except Exception as e:
                        logger.error(f"Error reading {target_file} for semantic check: {e}")
                
                if negative:
                    checks[key] = not found
                    if found:
                        failures.append(f"Negative semantic check failed: Found forbidden pattern '{label}' in {target_file}")
                else:
                    checks[key] = found
                    if not found:
                        if not target_file or not os.path.exists(target_file):
                            failures.append(f"Semantic check failed: Target file {target_file} missing for '{label}'")
                        else:
                            failures.append(f"Semantic check failed: Missing '{label}' in {target_file}")

        # Gate 5: Tests/build passed (look for failure indicators)
        if test_results:
            # We use a dict to track success for each unique command
            command_success = {}
            for tr in test_results:
                output = str(tr.get("output", ""))
                cmd = str(tr.get("command", "unknown"))
                
                passed = (
                    "passed" in output.lower()
                    and "failed" not in output.lower()
                    and "error" not in output.lower()
                ) or (
                    "[Success]" in output
                )
                
                # If it ever passed, it stays passed
                command_success[cmd] = command_success.get(cmd, False) or passed

            for cmd, passed in command_success.items():
                key = f"test_pass:{cmd[:60]}"
                checks[key] = passed
                if not passed:
                    failures.append(f"Test/build failed: {cmd}")

        # Gate 4: Final report contains evidence (not generic)
        if final_report:
            key = "report_has_evidence"
            # Generic phrases that indicate lack of evidence
            generic_phrases = [
                "task complete. the objective has been addressed",
                "task complete (no synthesis generated)",
                "no clear steps identified",
            ]
            is_generic = any(g in final_report.lower() for g in generic_phrases)

            # If tool outputs exist, report must reference at least some content
            has_evidence = False
            if tool_outputs:
                for out in tool_outputs:
                    # Check if the report references specific content from outputs
                    snippets = [s.strip() for s in out.split("\n") if len(s.strip()) > 10][:5]
                    for snippet in snippets:
                        clean = re.sub(r'[^a-zA-Z0-9]', '', snippet).lower()
                        clean_report = re.sub(r'[^a-zA-Z0-9]', '', final_report).lower()
                        if clean and clean in clean_report:
                            has_evidence = True
                            break
                    if has_evidence:
                        break
                checks[key] = has_evidence and not is_generic
                if not has_evidence:
                    failures.append("Final report lacks evidence from tool outputs")
                if is_generic:
                    failures.append("Final report uses generic template language")
            else:
                # No tool outputs to check against
                checks[key] = not is_generic
                if is_generic:
                    failures.append("Final report uses generic template language")

        all_passed = all(checks.values()) if checks else True
        return VerifierResult(passed=all_passed, checks=checks, failures=failures)


def verify_no_false_completion(
    status: str,
    required_files: Optional[List[str]] = None,
    final_report: str = "",
) -> bool:
    """
    Quick check: can this task legitimately claim 'completed'?
    Returns False if status is 'completed' but critical conditions are unmet.
    """
    if status != "completed":
        return True  # not claiming completion

    if required_files:
        for fp in required_files:
            if not os.path.exists(fp):
                logger.warning(f"False completion prevented: {fp} does not exist")
                return False

    generic_phrases = [
        "task complete. the objective has been addressed",
        "task complete (no synthesis generated)",
    ]
    if any(g in final_report.lower() for g in generic_phrases):
        if required_files:  # Only reject generic if there were actual requirements
            logger.warning("False completion prevented: generic report with real requirements")
            return False

    return True
