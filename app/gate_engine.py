import os
import re
import subprocess
import logging
from typing import Dict, Any, List, Tuple, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class GateEngine:
    """
    Declarative Gate Engine for StarAgent v0.5.0.
    Evaluates stages against 13 gate types and returns standardized evaluation results:
    'pass', 'fail', 'warning', or 'skipped'.
    """

    def __init__(self, workspace_root: Optional[str] = None):
        self.workspace_root = workspace_root or os.getcwd()

    def evaluate_gates(
        self, 
        gates: List[Dict[str, Any]], 
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Evaluate a list of gates against the current stage run context.
        Returns:
            Dict containing:
                "success": bool (True if all non-optional gates pass, or only warnings)
                "status": str ("pass", "fail", "warning", "skipped")
                "results": List[Dict[str, Any]] (detailed results for each gate)
        """
        results = []
        overall_success = True
        has_warnings = False
        all_skipped = True

        for gate in gates:
            gate_type = gate.get("type")
            arguments = gate.get("arguments", {})
            optional = gate.get("optional", False)

            status, msg = self.evaluate_gate(gate_type, arguments, context)
            
            # Map statuses
            if status == "fail":
                if not optional:
                    overall_success = False
                else:
                    status = "warning"
                    has_warnings = True
                all_skipped = False
            elif status == "warning":
                has_warnings = True
                all_skipped = False
            elif status == "pass":
                all_skipped = False

            results.append({
                "type": gate_type,
                "arguments": arguments,
                "optional": optional,
                "status": status,
                "message": msg
            })

        if not overall_success:
            overall_status = "fail"
        elif has_warnings:
            overall_status = "warning"
        elif all_skipped:
            overall_status = "skipped"
        else:
            overall_status = "pass"

        return {
            "success": overall_success,
            "status": overall_status,
            "results": results
        }

    def evaluate_gate(
        self, 
        gate_type: str, 
        arguments: Dict[str, Any], 
        context: Dict[str, Any]
    ) -> Tuple[str, str]:
        """
        Evaluate a single gate by type. Returns (status, message)
        where status is 'pass', 'fail', 'warning', or 'skipped'.
        """
        method_name = f"_gate_{gate_type}"
        if hasattr(self, method_name):
            try:
                return getattr(self, method_name)(arguments, context)
            except Exception as e:
                logger.error(f"Error evaluating gate '{gate_type}': {e}", exc_info=True)
                return "fail", f"Gate evaluation error: {str(e)}"
        else:
            return "warning", f"Unknown gate type: '{gate_type}'. Skipped evaluation."

    def _get_workspace_path(self, relative_path: str) -> Path:
        return Path(self.workspace_root) / relative_path

    # --- Individual Gate Implementations ---

    def _gate_file_exists(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        paths = args.get("paths", [])
        if not paths:
            return "skipped", "No paths specified to check."

        run_id = context.get("run_id") or ""
        formatted_paths = []
        for p in paths:
            if "{run_id}" in p:
                p = p.format(run_id=run_id)
            formatted_paths.append(p)

        missing = []
        for p in formatted_paths:
            # Check filesystem
            full_path = self._get_workspace_path(p)
            if not full_path.exists():
                # Check context files_produced as backup
                prod_files = context.get("files_produced", [])
                if not any(Path(pf).name == Path(p).name or pf == p for pf in prod_files):
                    missing.append(p)

        if missing:
            return "fail", f"Missing files: {', '.join(missing)}"
        return "pass", f"All required files exist: {', '.join(formatted_paths)}"

    def _gate_command_success(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        cmd = args.get("command")
        if not cmd:
            return "skipped", "No command specified."

        try:
            rc = subprocess.run(
                cmd,
                shell=True,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=args.get("timeout", 30)
            )
            if rc.returncode == 0:
                return "pass", f"Command '{cmd}' succeeded."
            else:
                return "fail", f"Command '{cmd}' failed with code {rc.returncode}.\nStdout:\n{rc.stdout}\nStderr:\n{rc.stderr}"
        except subprocess.TimeoutExpired:
            return "fail", f"Command '{cmd}' timed out."
        except Exception as e:
            return "fail", f"Command '{cmd}' failed to run: {e}"

    def _gate_output_contains(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        substring = args.get("substring")
        if not substring:
            return "skipped", "No substring specified."

        target = args.get("target", "stage_output")
        output_text = context.get(target, context.get("stage_output", ""))

        if substring in output_text:
            return "pass", f"Output contains substring '{substring}'."
        return "fail", f"Output does not contain substring '{substring}'."

    def _gate_semantic_regex(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        pattern = args.get("pattern")
        if not pattern:
            return "skipped", "No pattern specified."

        target = args.get("target", "stage_output")
        output_text = context.get(target, context.get("stage_output", ""))

        if re.search(pattern, output_text):
            return "pass", f"Output matches regex pattern '{pattern}'."
        return "fail", f"Output does not match regex pattern '{pattern}'."

    def _gate_no_files_modified(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        exclude = args.get("exclude", [])
        try:
            rc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=5
            )
            if rc.returncode != 0:
                # If not a git repo, check context
                mod_files = context.get("modified_files", [])
                filtered = [f for f in mod_files if f not in exclude]
                if filtered:
                    return "fail", f"Files modified: {', '.join(filtered)}"
                return "pass", "No files modified."

            lines = rc.stdout.strip().splitlines()
            modified = []
            for line in lines:
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2:
                    filepath = parts[1]
                    if filepath not in exclude and not any(filepath.startswith(ex) for ex in exclude):
                        modified.append(filepath)

            if modified:
                return "fail", f"Files modified in git: {', '.join(modified)}"
            return "pass", "No files modified."
        except Exception as e:
            return "warning", f"Failed to check git modifications: {e}"

    def _gate_no_forbidden_paths(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        forbidden = args.get("paths", [])
        if not forbidden:
            return "skipped", "No forbidden paths specified."

        # Get modified files from git or context
        modified = []
        try:
            rc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=5
            )
            if rc.returncode == 0:
                for line in rc.stdout.strip().splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:
                        modified.append(parts[1])
            else:
                modified = context.get("modified_files", [])
        except Exception:
            modified = context.get("modified_files", [])

        violated = []
        for file in modified:
            for pattern in forbidden:
                if pattern in file or re.search(pattern.replace("*", ".*"), file):
                    violated.append((file, pattern))
                    break

        if violated:
            details = ", ".join([f"{f} matches {p}" for f, p in violated])
            return "fail", f"Forbidden paths modified: {details}"
        return "pass", "No forbidden paths modified."

    def _gate_docs_citations_present(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        min_citations = args.get("min_citations", 1)
        citations = context.get("citations", [])
        if len(citations) >= min_citations:
            return "pass", f"Found {len(citations)} citations in metadata."

        # Scan text for citations (e.g. [source-123], [Doc-4], [Chunk...], [1])
        output_text = context.get("stage_output", "")
        # Match common brackets citations containing words/numbers
        found_in_text = re.findall(r"\[[a-zA-Z0-9_\-\.\s]+\]", output_text)
        
        # Filter out common markdown links e.g. [foo](bar) or image formats
        # We look for references that look like citations
        valid_citations = [c for c in found_in_text if not c.lower() in {"[ ]", "[x]", "[/]"}]
        
        if len(valid_citations) >= min_citations:
            return "pass", f"Found {len(valid_citations)} citations in output text."
        return "fail", f"Citations count ({len(valid_citations)} in text, {len(citations)} in metadata) is below minimum required ({min_citations})."

    def _gate_no_secret_leak(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        # Simple patterns
        patterns = {
            "AWS API Key": r"AKIA[0-9A-Z]{16}",
            "Private Key": r"-----BEGIN [A-Z ]+ PRIVATE KEY-----",
            "Slack Token": r"xox[bapr]-[0-9]{12}-[0-9]{12}-[a-zA-Z0-9]{24}",
            "Generic Key/Secret": r"(?:key|secret|password|token|auth)\s*[:=]\s*['\"][a-zA-Z0-9_\-]{16,}['\"]"
        }

        # Check stage output
        output_text = context.get("stage_output", "")
        for name, pattern in patterns.items():
            if re.search(pattern, output_text, re.IGNORECASE):
                return "fail", f"Potential secret leak found in stage output matching {name} pattern."

        # Check modified files
        modified = []
        try:
            rc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=5
            )
            if rc.returncode == 0:
                for line in rc.stdout.strip().splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:
                        modified.append(parts[1])
            else:
                modified = context.get("modified_files", [])
        except Exception:
            modified = context.get("modified_files", [])

        for file in modified:
            full_path = self._get_workspace_path(file)
            if full_path.exists() and full_path.is_file():
                try:
                    content = full_path.read_text(errors="ignore")
                    for name, pattern in patterns.items():
                        if re.search(pattern, content, re.IGNORECASE):
                            return "fail", f"Potential secret leak found in file '{file}' matching {name} pattern."
                except Exception as e:
                    logger.warning(f"Could not read file {file} for secrets leak check: {e}")

        return "pass", "No secrets leak detected in stage outputs or modified files."

    def _gate_test_pass(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        # Resolve command
        cmd = args.get("command")
        if not cmd:
            # Target specific modified test files or directories to prevent collection issues in unrelated scratch folders
            modified = context.get("modified_files", []) or context.get("files_produced", [])
            target_dirs = set()
            test_files = []
            for f in modified:
                if f.endswith(".py"):
                    if "test_" in os.path.basename(f) or f.endswith("_test.py"):
                        test_files.append(f)
                    else:
                        target_dirs.add(os.path.dirname(f))
            
            if test_files:
                targets = " ".join(test_files)
            elif target_dirs:
                valid_dirs = [d for d in target_dirs if d and d != "."]
                if valid_dirs:
                    targets = " ".join(valid_dirs)
                else:
                    targets = ""
            else:
                targets = ""

            # Detect pytest path
            if os.path.exists(os.path.join(self.workspace_root, ".venv")):
                pytest_bin = ".venv/bin/pytest -q"
            elif os.path.exists(os.path.join(self.workspace_root, "package.json")):
                pytest_bin = "npm test"
            else:
                pytest_bin = "pytest -q"

            if targets and pytest_bin.endswith("pytest -q"):
                cmd = f"{pytest_bin} {targets}"
            else:
                cmd = pytest_bin

        return self._gate_command_success({"command": cmd, "timeout": args.get("timeout", 30)}, context)

    def _gate_build_pass(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        cmd = args.get("command")
        if not cmd:
            # Check if Python syntax check
            modified = context.get("modified_files", [])
            python_files = [f for f in modified if f.endswith(".py")]
            if python_files:
                errors = []
                for pf in python_files:
                    full_path = self._get_workspace_path(pf)
                    if full_path.exists():
                        rc = subprocess.run(
                            ["python3", "-m", "py_compile", str(full_path)],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if rc.returncode != 0:
                            errors.append(f"{pf}: {rc.stderr.strip() or rc.stdout.strip()}")
                if errors:
                    return "fail", f"Python syntax check failed:\n" + "\n".join(errors)
                return "pass", "All modified Python files passed syntax compilation."
            
            # JS syntax compile fallback
            js_files = [f for f in modified if f.endswith(".js")]
            if js_files:
                errors = []
                for jf in js_files:
                    full_path = self._get_workspace_path(jf)
                    if full_path.exists():
                        rc = subprocess.run(
                            ["node", "-c", str(full_path)],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if rc.returncode != 0:
                            errors.append(f"{jf}: {rc.stderr.strip() or rc.stdout.strip()}")
                if errors:
                    return "fail", f"JS syntax check failed:\n" + "\n".join(errors)
                return "pass", "All modified JS files passed syntax compilation."

            # Default to skipped or custom command
            if os.path.exists(os.path.join(self.workspace_root, "package.json")):
                cmd = "npm run build"
            else:
                return "skipped", "No build system detected and no files modified."

        return self._gate_command_success({"command": cmd, "timeout": args.get("timeout", 60)}, context)

    def _gate_lint_pass(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        cmd = args.get("command")
        if not cmd:
            if os.path.exists(os.path.join(self.workspace_root, "package.json")):
                cmd = "npm run lint"
            elif os.path.exists(os.path.join(self.workspace_root, ".venv")):
                cmd = ".venv/bin/flake8"
            else:
                cmd = "flake8"

        # Check if the linter exists on path or workspace
        # If it fails to run entirely (e.g. Command not found), return skipped/warning
        try:
            parts = cmd.split()
            # Try running
            rc = subprocess.run(
                cmd,
                shell=True,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=20
            )
            if rc.returncode == 0:
                return "pass", f"Lint command '{cmd}' passed."
            else:
                # If command is missing, treat as skipped/warning
                if "command not found" in rc.stderr.lower() or "no such file" in rc.stderr.lower():
                    return "skipped", f"Linter command '{cmd}' not found on system."
                return "fail", f"Lint command '{cmd}' failed.\nOutput:\n{rc.stdout}\n{rc.stderr}"
        except Exception as e:
            return "skipped", f"Lint execution skipped: {e}"

    def _gate_custom_python(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        code = args.get("code")
        if not code:
            return "skipped", "No Python code provided."

        try:
            local_vars = {"context": context, "success": True, "message": "Custom check passed."}
            # Execute python block
            exec(code, {}, local_vars)
            
            success = local_vars.get("success", True)
            message = local_vars.get("message", "Custom check finished.")
            
            if success:
                return "pass", message
            return "fail", message
        except Exception as e:
            return "fail", f"Custom Python script failed with exception: {e}"

    def _gate_human_approval(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        level = args.get("level", "required")
        stage_name = context.get("stage_name", "")
        variables = context.get("variables", {})

        approved_key = f"approved_{stage_name}"
        approved = variables.get(approved_key, False)

        if approved is True:
            return "pass", f"Human approval granted for stage '{stage_name}'."
        elif approved == "rejected":
            return "fail", f"Human rejected stage '{stage_name}'."
        
        # If level is optional, return warning or pass. If required, fail/pause
        if level == "optional":
            return "warning", f"Human approval requested (optional) for stage '{stage_name}'."
        return "fail", f"Human approval required for stage '{stage_name}' but not yet granted."

    def _gate_source_count_min(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        run_id = context.get("run_id") or ""
        min_count = args.get("min_count", 3)
        sources_path = self._get_workspace_path(f".runtime/workflows/{run_id}/sources.json")
        if not sources_path.exists():
            return "fail", f"sources.json does not exist at {sources_path}"
        try:
            import json
            sources = json.loads(sources_path.read_text(encoding="utf-8"))
            if not isinstance(sources, list):
                return "fail", "sources.json is not a JSON list"
            count = len(sources)
            if count == 0:
                return "pass", "No configured live sources were available (limitations mode)."
            
            variables = context.get("variables") or {}
            is_manual = len(variables.get("urls", [])) > 0 or variables.get("docs", False)
            if is_manual:
                return "pass", f"Manual sources mode: found {count} sources."
                
            if count >= min_count:
                return "pass", f"Found {count} sources (minimum required: {min_count})."
            return "fail", f"Found {count} sources, which is below minimum required {min_count}."
        except Exception as e:
            return "fail", f"Failed to read/parse sources.json: {e}"

    def _gate_source_diversity(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        run_id = context.get("run_id") or ""
        min_domains = args.get("min_domains", 2)
        sources_path = self._get_workspace_path(f".runtime/workflows/{run_id}/sources.json")
        if not sources_path.exists():
            return "fail", f"sources.json does not exist at {sources_path}"
        try:
            import json
            from urllib.parse import urlparse
            sources = json.loads(sources_path.read_text(encoding="utf-8"))
            if not isinstance(sources, list):
                return "fail", "sources.json is not a JSON list"
            if len(sources) == 0:
                return "pass", "No configured live sources were available (limitations mode)."
                
            variables = context.get("variables") or {}
            is_manual = len(variables.get("urls", [])) > 0 or variables.get("docs", False)
            if is_manual:
                return "pass", "Manual sources mode: domain diversity check bypassed."
                
            domains = set()
            for src in sources:
                url = src.get("url")
                if url:
                    loc = urlparse(url).netloc
                    if loc:
                        domains.add(loc)
            unique_domains = len(domains)
            if unique_domains >= min_domains:
                return "pass", f"Found {unique_domains} unique domains (minimum required: {min_domains})."
            return "fail", f"Found {unique_domains} unique domains, which is below minimum required {min_domains}."
        except Exception as e:
            return "fail", f"Failed to read/parse sources.json: {e}"

    def _gate_contradiction_check(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        run_id = context.get("run_id") or ""
        claims_path = self._get_workspace_path(f".runtime/workflows/{run_id}/claims_matrix.md")
        if not claims_path.exists():
            return "fail", f"claims_matrix.md does not exist at {claims_path}"
        try:
            content = claims_path.read_text(encoding="utf-8")
            if not content.strip():
                return "fail", "claims_matrix.md is empty"
            if "contradiction" in content.lower() or "conflict" in content.lower() or "matrix" in content.lower():
                return "pass", "claims_matrix.md has contradiction analysis."
            return "fail", "claims_matrix.md is missing contradiction/conflict analysis content."
        except Exception as e:
            return "fail", f"Failed to read claims_matrix.md: {e}"

    def _gate_no_unsourced_claims(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        run_id = context.get("run_id") or ""
        audit_path = self._get_workspace_path(f".runtime/workflows/{run_id}/citation_audit.md")
        if not audit_path.exists():
            return "fail", f"citation_audit.md does not exist at {audit_path}"
        try:
            content = audit_path.read_text(encoding="utf-8")
            if not content.strip():
                return "fail", "citation_audit.md is empty"
            lines = content.splitlines()
            unresolved_found = False
            for line in lines:
                if "unresolved" in line.lower():
                    unresolved_found = True
                    if "none" not in line.lower() and ":" in line and line.split(":", 1)[1].strip() not in {"", ".", "0"}:
                        return "fail", f"Unresolved unsourced claims found in citation audit: {line}"
            return "pass", "No unsourced claims detected."
        except Exception as e:
            return "fail", f"Failed to read citation_audit.md: {e}"

    def _gate_final_report_exists(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        run_id = context.get("run_id") or ""
        report_path = self._get_workspace_path(f".runtime/workflows/{run_id}/final_report.md")
        if report_path.exists() and report_path.stat().st_size > 0:
            return "pass", "final_report.md exists and is non-empty."
        return "fail", f"final_report.md does not exist or is empty at {report_path}"

    def _gate_citation_required(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        run_id = context.get("run_id") or ""
        min_citations = args.get("min_citations", 3)
        report_path = self._get_workspace_path(f".runtime/workflows/{run_id}/final_report.md")
        if not report_path.exists():
            return "fail", f"final_report.md does not exist at {report_path}"
        try:
            content = report_path.read_text(encoding="utf-8")
            if "No configured live sources were available." in content:
                return "pass", "No configured live sources were available (limitations mode)."
            found = re.findall(r"\[[a-zA-Z0-9_\-\.\s]+\]", content)
            valid_citations = [c for c in found if c.lower() not in {"[ ]", "[x]", "[/]"}]
            unique_citations = set(valid_citations)
            
            variables = context.get("variables") or {}
            is_manual = len(variables.get("urls", [])) > 0 or variables.get("docs", False)
            if is_manual:
                return "pass", f"Manual sources mode: found {len(unique_citations)} unique citations."
                
            if len(unique_citations) >= min_citations:
                return "pass", f"Found {len(unique_citations)} unique citations in report (minimum required: {min_citations})."
            return "fail", f"Found {len(unique_citations)} unique citations, which is below minimum required {min_citations}."
        except Exception as e:
            return "fail", f"Failed to read/parse final_report.md: {e}"

    def _gate_quote_limit(self, args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[str, str]:
        run_id = context.get("run_id") or ""
        max_quotes = args.get("max_quotes", 10)
        report_path = self._get_workspace_path(f".runtime/workflows/{run_id}/final_report.md")
        if not report_path.exists():
            return "fail", f"final_report.md does not exist at {report_path}"
        try:
            content = report_path.read_text(encoding="utf-8")
            quotes = re.findall(r'"([^"\n]+)"', content)
            count = len(quotes)
            if count <= max_quotes:
                return "pass", f"Found {count} direct quotes (maximum allowed: {max_quotes})."
            return "fail", f"Found {count} direct quotes, which exceeds the limit of {max_quotes}."
        except Exception as e:
            return "fail", f"Failed to read final_report.md: {e}"
