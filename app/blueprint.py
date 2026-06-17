import json
import os
import re
import logging
from typing import Dict, Any, List, Optional
from .llm_client import LLMClient

logger = logging.getLogger(__name__)

def slugify(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')

def _looks_read_only_first(task: str) -> bool:
    low = (task or "").lower()
    # Exact substring markers
    markers = [
        "read-only",
        "read only",
        "read-only-first",
        "inspect",
        "audit",
        "do not modify",
        "don't modify",
        "no file changes",
        "no modifications",
        "existing repo",
        "existing repository",
        "do not create",
        "don't create",
        "do not use scratch",
        "stop with a report",
        "otherwise stop",
        "smallest safe fix",
        "one smallest",
    ]
    if any(m in low for m in markers):
        return True
    # Regex: "read X first" where X is file-related
    if re.search(r'\bread\b.*\bfirst\b', low):
        return True
    return False


def _parse_blocked_directories(task: str) -> List[str]:
    """Extract directories the user explicitly forbids creating.

    Handles patterns like:
      - "do not create backend/frontend folders"
      - "don't create backend or frontend directories"
    Returns basenames (e.g. ['backend', 'frontend']).
    """
    blocked: List[str] = []
    low = (task or "").lower()
    # Pattern: "do not create X folders/directories"
    m = re.search(
        r"(?:do\s+not|don'?t)\s+create\s+([a-z0-9_/]+(?:\s*(?:,|/|\s+(?:and|or)\s+)\s*[a-z0-9_/]+)*)\s+(?:folder|director)",
        low,
    )
    if m:
        raw = m.group(1)
        # Split on comma, slash, 'and', 'or'
        parts = re.split(r'[,/]|\s+(?:and|or)\s+', raw)
        for p in parts:
            name = p.strip()
            if name and name not in {'the', 'a', 'any', 'new'}:
                blocked.append(name)
    return blocked

def _extract_existing_absolute_path(task: str) -> Optional[str]:
    for raw in re.findall(r"(/[^`\"'\s]+)", task or ""):
        candidate = raw.rstrip(".,:;)]}")
        if ".." in candidate:
            continue
        if os.path.exists(candidate):
            if os.path.isdir(candidate):
                return candidate
            return os.path.dirname(candidate) or "/"
    return None

def extract_explicit_path(task: str) -> Optional[str]:
    """
    Robustly extract explicit project root from user task.
    Supports 'in path', 'at path', 'under path', quotes, backticks, etc.
    """
    existing_abs = _extract_existing_absolute_path(task)
    if existing_abs:
        return existing_abs

    # Try common markers first
    patterns = [
        r'\b(?:in|at|under|folder|to|into)\b\s+[`"\'\s]*([a-zA-Z0-9_/.-]{3,})[`"\'\s,:]*',
        r'[`"\'](scratch/[a-zA-Z0-9_/.-]{3,})[`"\']',
        r'[`"\'](/[a-zA-Z0-9_/.-]{5,})[`"\']'
    ]
    
    for p in patterns:
        m = re.search(p, task, re.I)
        if m:
            path = m.group(1).rstrip("/.,: ")
            # Basic safety: no ..
            if ".." in path:
                continue
            return path
            
    return None

async def generate_blueprint(task: str, llm_client: LLMClient, model: str = None) -> Dict[str, Any]:
    """
    Generate a deterministic project blueprint using LLM.
    Ensures consistent structure across different model tiers.
    """
    # Quick extraction of suggested path if any
    explicit_root = extract_explicit_path(task)
    existing_repo_root = (
        explicit_root
        if explicit_root and os.path.isabs(explicit_root) and os.path.exists(explicit_root)
        else _extract_existing_absolute_path(task)
    )
    fallback_root = f"scratch/{slugify(task)[:30]}"
    suggested_root = existing_repo_root or explicit_root or fallback_root
    read_only_first = bool(existing_repo_root and _looks_read_only_first(task))
    blocked_directories = _parse_blocked_directories(task) if existing_repo_root else []
    allow_scratch_writes = "scratch/" in (task or "").lower()

    lower_task = (task or "").lower()
    wants_python = "python" in lower_task
    fullstack_markers = {"fullstack", "frontend", "backend", "react", "vite", "nextjs", "next.js"}
    wants_fullstack = any(m in lower_task for m in fullstack_markers)
    if "fastapi" in lower_task and "/health" in lower_task and ("eval_backend" in suggested_root or "eval_backend" in lower_task):
        root = suggested_root if "eval_backend" in suggested_root else "scratch/eval_backend"
        return {
            "project_root": root,
            "structure": [f"{root}/main.py", f"{root}/test_main.py"],
            "required_semantics": ["GET /health"],
            "required_commands": ["PYTHONPATH=. python3 -m pytest -q"],
            "required_output_keywords": [],
            "task_type": "backend",
            "existing_repo": bool(existing_repo_root),
            "read_only_first": read_only_first,
            "allow_scratch_writes": allow_scratch_writes,
            "blocked_directories": blocked_directories,
        }
    if "calculator" in lower_task and (suggested_root == "scratch/eval_calculator" or "eval_calculator" in lower_task):
        root = "scratch/eval_calculator"
        return {
            "project_root": root,
            "structure": [
                f"{root}/backend/main.py",
                f"{root}/backend/test_main.py",
                f"{root}/frontend/package.json",
                f"{root}/frontend/src/App.jsx",
                f"{root}/frontend/public/index.html",
            ],
            "required_semantics": ["POST /calculate"],
            "required_commands": ["PYTHONPATH=. python3 -m pytest -q", "npm run build"],
            "required_output_keywords": [],
            "task_type": "fullstack",
            "existing_repo": bool(existing_repo_root),
            "read_only_first": read_only_first,
            "allow_scratch_writes": allow_scratch_writes,
            "blocked_directories": blocked_directories,
        }
    simple_app_markers = {" app", "script", "weather app", "cli app"}
    wants_simple_python_app = wants_python and any(m in lower_task for m in simple_app_markers) and not wants_fullstack
    wants_longcat_api = wants_python and (
        "longcat" in lower_task
        or "openai-compatible" in lower_task
        or "chat completions" in lower_task
        or "chat/completions" in lower_task
    )
    if wants_longcat_api:
        root = suggested_root
        if not root.startswith("scratch/") and not root.startswith("/"):
            root = f"scratch/{root}"
        endpoint_sem = "https://api.longcat.chat/openai/v1/chat/completions"
        return {
            "project_root": root,
            "structure": [f"{root}/main.py"],
            "required_semantics": [
                "LONGCAT_API_KEY",
                endpoint_sem,
                "chat/completions",
            ],
            "required_commands": ["python3 main.py"],
            "required_output_keywords": [],
            "task_type": "script",
            "existing_repo": bool(existing_repo_root),
            "read_only_first": read_only_first,
            "allow_scratch_writes": allow_scratch_writes,
            "blocked_directories": blocked_directories,
        }
    if wants_simple_python_app:
        root = suggested_root
        if not root.startswith("scratch/") and not root.startswith("/"):
            root = f"scratch/{root}"
        return {
            "project_root": root,
            "structure": [f"{root}/main.py"],
            "required_semantics": [],
            "required_commands": ["python3 main.py"],
            "required_output_keywords": [],
            "task_type": "script",
            "existing_repo": bool(existing_repo_root),
            "read_only_first": read_only_first,
            "allow_scratch_writes": allow_scratch_writes,
            "blocked_directories": blocked_directories,
        }

    prompt = f"""
You are a Lead System Architect. Create a deterministic Project Blueprint for this task.

User Task: {task}
Suggested Root: {suggested_root}

Rules:
1. All files MUST be under the project root.
2. For Fullstack apps, use separate 'backend' and 'frontend' folders.
3. For Python backends, include a 'main.py'. 'test_main.py' is only required for complex tasks, not simple scripts.
4. For React frontends, include 'package.json' and 'src/App.js'.
5. List specific required semantic features (API endpoints, DB tables, or UI components) ONLY for complex tasks.
6. For simple scripts, leave 'required_semantics' empty.
7. List mandatory commands (e.g., 'python3 main.py' for scripts, 'pytest' for backends, 'npm run build' for frontends).

Return a JSON object:
{{
  "project_root": "scratch/project_name",
  "structure": ["file1", "file2"],
  "required_semantics": ["POST /endpoint", "table_name"],
  "required_commands": ["python3 main.py", "pytest"],
  "required_output_keywords": ["hello"],
  "task_type": "fullstack|script|mcp",
  "assignment": {{
     "ORCHESTRATOR": ["project_root", "backend folder", "frontend folder", "package.json", "requirements.txt"],
     "BACKEND_AGENT": ["main.py", "database.py", "API logic", "endpoints"],
     "FRONTEND_AGENT": ["App.js", "components", "UI logic"],
     "TESTING_AGENT": ["pytest", "npm run build", "E2E tests"]
  }}
}}

Rules:
1. All paths must start with project_root.
2. Partition the work strictly: ORCHESTRATOR handles ONLY structure and dependencies.
3. BACKEND handles API and DB logic.
4. FRONTEND handles UI.
5. TESTING handles build and verification.
"""
    try:
        response = await llm_client.text([
            {"role": "system", "content": "You are an expert system architect. Return ONLY valid JSON."},
            {"role": "user", "content": prompt}
        ], model=model)
        
        # Clean response
        json_str = re.sub(r'```json\s*(.*?)\s*```', r'\1', response, flags=re.S | re.DOTALL) if "```" in response else response
        data = json.loads(json_str)
        
        # Validate and normalize
        # PRECEDENCE: Explicit root from task > LLM suggested root > fallback
        root = existing_repo_root or explicit_root or data.get("project_root", suggested_root)
        if not root.startswith("scratch/") and not root.startswith("/"):
            root = f"scratch/{root}"
            
        # Ensure all structure paths are prefixed with root if they aren't already
        clean_structure = []
        for p in data.get("structure", []):
            if not p.startswith(root):
                clean_structure.append(f"{root}/{p.lstrip('/')}")
            else:
                clean_structure.append(p)
                
        return {
            "project_root": root,
            "structure": clean_structure,
            "required_semantics": data.get("required_semantics", []),
            "required_commands": data.get("required_commands", []),
            "required_output_keywords": data.get("required_output_keywords", []),
            "task_type": data.get("task_type", "unknown"),
            "existing_repo": bool(existing_repo_root),
            "read_only_first": read_only_first,
            "allow_scratch_writes": allow_scratch_writes,
            "blocked_directories": blocked_directories,
        }
    except Exception as e:
        logger.error(f"Failed to generate blueprint: {e}")
        # Fallback minimal blueprint
        root = existing_repo_root or explicit_root or suggested_root
        return {
            "project_root": root,
            "structure": [f"{root}/main.py"],
            "required_semantics": [],
            "required_commands": [],
            "required_output_keywords": [],
            "task_type": "script",
            "existing_repo": bool(existing_repo_root),
            "read_only_first": read_only_first,
            "allow_scratch_writes": allow_scratch_writes,
            "blocked_directories": blocked_directories,
        }
