"""
Standardized status constants for StarAgent v0.6.1.

Every task, workflow, and research run MUST use one of these three
terminal statuses to ensure consistent reporting and diagnostics.
"""

# Task / workflow completed successfully with all gates passing.
COMPLETED = "completed"

# Task completed but with documented limitations (e.g. no live sources,
# partial coverage, degraded provider). The run produced output but it
# should be consumed with caveats.
COMPLETED_WITH_LIMITATIONS = "completed_with_limitations"

# Task failed with an identifiable reason. The final_summary field
# MUST contain a human-readable explanation of what went wrong.
FAILED_WITH_REASON = "failed_with_reason"

# All standard terminal statuses a task run can have.
TERMINAL_STATUSES = {COMPLETED, COMPLETED_WITH_LIMITATIONS, FAILED_WITH_REASON}

# --- helpers ---

def is_terminal(status: str) -> bool:
    """Return True if *status* is one of the three terminal statuses."""
    return status in TERMINAL_STATUSES


def is_success(status: str) -> bool:
    """Return True if the status represents a successful (non-failed) outcome."""
    return status in (COMPLETED, COMPLETED_WITH_LIMITATIONS)
