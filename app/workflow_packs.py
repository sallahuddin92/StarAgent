import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

BUILTIN_PACKS = {
    "repo_audit_pack": {
        "skills": [
            "architecture-review",
            "security-review",
            "dependency-review",
            "documentation-review",
            "performance-review",
            "testing-review",
            "api-review",
            "code-review"
        ],
        "tools": ["list_files", "read_file", "search_files", "grep", "get_file_tree", "read_multiple_files"],
        "gates": [
            {"type": "file_exists", "arguments": {"paths": ["entry_points.md", "architecture_map.md", "audit_report.md"]}}
        ],
        "capabilities": ["local_fast", "summarization", "reasoning"],
        "context_rules": [
            "Do not modify files or run destructive commands during inspect/analyze stages.",
            "Focus on identifying code smells, security vulnerabilities, and design anomalies."
        ],
        "default_approval_policies": {
            "plan": "required"
        }
    },
    "backend_fix_pack": {
        "skills": [
            "codebase-refactor",
            "unit-testing",
            "code-review",
            "api-design",
            "backend-development"
        ],
        "tools": ["list_files", "read_file", "search_files", "grep", "write_file", "create_directory", "run_command", "patch", "git"],
        "gates": [
            {"type": "test_pass", "arguments": {}},
            {"type": "build_pass", "arguments": {}}
        ],
        "capabilities": ["backend_coding", "reasoning", "verification"],
        "context_rules": [
            "Ensure all backend code changes are covered by unit tests.",
            "Verify syntax correctness prior to exiting the execute stage."
        ],
        "default_approval_policies": {
            "plan": "required"
        }
    },
    "frontend_fix_pack": {
        "skills": [
            "frontend-development",
            "integration-testing",
            "code-review"
        ],
        "tools": ["list_files", "read_file", "search_files", "grep", "write_file", "create_directory", "run_command", "patch", "git"],
        "gates": [
            {"type": "lint_pass", "arguments": {}}
        ],
        "capabilities": ["frontend_coding", "reasoning", "local_fast"],
        "context_rules": [
            "Verify UI/UX alignment and components responsiveness.",
            "Avoid writing raw style inline; use predefined styling classes where possible."
        ],
        "default_approval_policies": {
            "plan": "required"
        }
    },
    "docs_sdk_pack": {
        "skills": [
            "sdk-design",
            "documentation-review",
            "api-review"
        ],
        "tools": ["list_files", "read_file", "search_files", "grep", "write_file", "staragent_docs_search", "staragent_docs_ask", "staragent_docs_ingest"],
        "gates": [
            {"type": "docs_citations_present", "arguments": {}},
            {"type": "file_exists", "arguments": {"paths": ["sdk_report.md"]}}
        ],
        "capabilities": ["docs_grounded", "long_context", "local_fast"],
        "context_rules": [
            "Ensure citations map directly back to doc chunk sources.",
            "Write standard Python/Javascript docstrings for all exposed SDK classes."
        ],
        "default_approval_policies": {
            "plan": "optional"
        }
    },
    "security_review_pack": {
        "skills": [
            "security-review",
            "code-review"
        ],
        "tools": ["list_files", "read_file", "search_files", "grep", "get_file_tree"],
        "gates": [
            {"type": "no_secret_leak", "arguments": {}},
            {"type": "no_files_modified", "arguments": {}}
        ],
        "capabilities": ["reasoning", "long_context"],
        "context_rules": [
            "Do not modify files or make external connection requests.",
            "Inspect codebase specifically for hardcoded API keys, secrets, or vulnerable dependencies."
        ],
        "default_approval_policies": {
            "plan": "none"
        }
    },
    "dependency_review_pack": {
        "skills": [
            "dependency-review",
            "code-review"
        ],
        "tools": ["list_files", "read_file", "search_files", "grep", "run_command"],
        "gates": [
            {"type": "command_success", "arguments": {"command": "pip audit || true"}}
        ],
        "capabilities": ["local_fast", "reasoning"],
        "context_rules": [
            "Analyze lockfiles and dependencies lists for outdated versions or vulnerabilities."
        ],
        "default_approval_policies": {
            "plan": "none"
        }
    },
    "release_pack": {
        "skills": [
            "changelog-generation",
            "deployment-check"
        ],
        "tools": ["list_files", "read_file", "search_files", "grep", "write_file", "git", "run_command"],
        "gates": [
            {"type": "file_exists", "arguments": {"paths": ["changelog.md"]}}
        ],
        "capabilities": ["summarization", "local_fast"],
        "context_rules": [
            "Ensure changelog maps commits since the last release accurately."
        ],
        "default_approval_policies": {
            "plan": "required"
        }
    }
}

def get_workflow_pack(workflow_name: str) -> Dict[str, Any]:
    """Map a workflow name to a workflow pack."""
    name = str(workflow_name).lower()
    if name == "repo_audit":
        return BUILTIN_PACKS["repo_audit_pack"]
    elif name in {"existing_repo_fix", "bug_fix"}:
        return BUILTIN_PACKS["backend_fix_pack"]
    elif name == "feature_build":
        # Can be either, default to backend_fix_pack
        return BUILTIN_PACKS["backend_fix_pack"]
    elif name == "docs_grounded_sdk":
        return BUILTIN_PACKS["docs_sdk_pack"]
    elif name == "research":
        return BUILTIN_PACKS["security_review_pack"]
    elif name == "issue_triage":
        return BUILTIN_PACKS["dependency_review_pack"]
    elif name == "release":
        return BUILTIN_PACKS["release_pack"]
    
    # Fallback default pack
    return BUILTIN_PACKS["repo_audit_pack"]
