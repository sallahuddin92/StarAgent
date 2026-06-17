from __future__ import annotations

import re


def detect_agent_status(output: str) -> str:
    """
    Parse stream output for final x_agent_status.
    Failed status takes precedence over completed if both appear.
    """
    text = output or ""
    
    # Extract all occurrences of [x_agent_status] and return the status (failed taking precedence)
    markers = re.findall(r"\[x_agent_status\]\s*([a-zA-Z0-9_\-]+)", text, re.I)
    if markers:
        markers_lower = [m.lower() for m in markers]
        if "failed" in markers_lower:
            return "failed"
        if "completed" in markers_lower:
            return "completed"
        return markers_lower[-1]

    # Canonical markers
    if re.search(r"\[x_agent_status\]\s*failed", text, re.I):
        return "failed"
    if re.search(r"\[x_agent_status\]\s*completed", text, re.I):
        return "completed"
    
    # Compact stream / Phased workflow markers
    if re.search(r"\[ORCHESTRATOR\]\s*completed\s*✅", text):
        return "completed"
    if re.search(r"\[ORCHESTRATOR\]\s*failed\s*❌", text):
        return "failed"
    if re.search(r"\[REPORT\]\s*=== End Report ===", text):
        return "completed"

    # Additional error patterns to handle cases where x_agent_status is not output
    if "[WORKFLOW] [ERROR]" in text or "[ERROR]" in text or "Traceback (most recent call last):" in text:
        return "failed"
    
    return "unknown"


def has_honest_stress_diagnostics(output: str) -> bool:
    """
    Stress failures are acceptable only when precise missing artifact/command
    diagnostics are present.
    """
    text = (output or "").lower()
    patterns = [
        r"required files missing",
        r"missing file",
        r"missing files",
        r"required command not verified",
        r"command failed",
        r"semantic check failed",
        r"frontend build directory missing",
        r"missing endpoint",
        r"failures:\s*\n\s*-\s",
    ]
    return any(re.search(p, text) for p in patterns)

