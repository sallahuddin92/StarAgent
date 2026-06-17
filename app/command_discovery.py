"""
StarAgent Command Discovery — parse repo files for build/test/dev commands.

Extracts install, dev, build, test, and smoke commands from:
  - Makefile
  - package.json
  - pyproject.toml
  - requirements.txt / requirements-dev.txt
  - README.md (fenced code blocks)
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def discover_commands(project_root: str) -> Dict[str, List[str]]:
    """Discover commands from repo config files.

    Returns dict with keys: install, dev, build, test, smoke.
    Each value is a deduplicated list of command strings.
    """
    result: Dict[str, List[str]] = {
        "install": [],
        "dev": [],
        "build": [],
        "test": [],
        "smoke": [],
    }
    if not project_root or not os.path.isdir(project_root):
        return result

    # Parse each source
    _parse_makefile(project_root, result)
    _parse_package_json(project_root, result)
    _parse_pyproject_toml(project_root, result)
    _parse_requirements(project_root, result)
    _parse_readme(project_root, result)

    # Deduplicate
    for key in result:
        result[key] = list(dict.fromkeys(result[key]))

    return result


# ---------------------------------------------------------------------------
# Makefile parser
# ---------------------------------------------------------------------------

# Classify Makefile targets by name
_MAKEFILE_CATEGORY_MAP = {
    "install": ["install", "setup", "deps", "dependencies", "init", "bootstrap"],
    "dev": ["dev", "serve", "run", "start", "up", "watch", "develop"],
    "build": ["build", "compile", "dist", "package", "release"],
    "test": ["test", "tests", "check", "lint", "format", "verify", "ci"],
    "smoke": ["smoke", "health", "ping", "status", "quick-test"],
}


def _parse_makefile(project_root: str, result: Dict[str, List[str]]) -> None:
    """Parse Makefile targets into command categories."""
    for name in ("Makefile", "makefile", "GNUmakefile"):
        path = os.path.join(project_root, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue

        # Extract target names (lines matching ^target_name:)
        targets = re.findall(r'^([a-zA-Z_][\w-]*)\s*:', content, re.MULTILINE)
        for target in targets:
            if target.startswith("_") or target in {"all", "default", ".PHONY", "help"}:
                continue
            cmd = f"make {target}"
            categorized = False
            for category, keywords in _MAKEFILE_CATEGORY_MAP.items():
                if any(kw in target.lower() for kw in keywords):
                    result[category].append(cmd)
                    categorized = True
                    break
            if not categorized:
                # Unknown targets go to dev
                result["dev"].append(cmd)
        break  # Only parse first Makefile found


# ---------------------------------------------------------------------------
# package.json parser
# ---------------------------------------------------------------------------

_NPM_CATEGORY_MAP = {
    "install": ["install", "postinstall", "preinstall", "prepare"],
    "dev": ["dev", "start", "serve", "watch", "develop"],
    "build": ["build", "compile", "dist", "bundle"],
    "test": ["test", "test:unit", "test:e2e", "test:integration", "lint", "lint:fix", "format", "check", "ci"],
    "smoke": ["smoke", "health"],
}


def _parse_package_json(project_root: str, result: Dict[str, List[str]]) -> None:
    """Parse package.json scripts into command categories."""
    # Search in root, frontend, apps/frontend
    search_dirs = [
        project_root,
        os.path.join(project_root, "frontend"),
        os.path.join(project_root, "apps", "frontend"),
    ]
    for d in search_dirs:
        path = os.path.join(d, "package.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        scripts = data.get("scripts", {})
        if not isinstance(scripts, dict):
            continue

        # Always add npm install if package.json exists
        result["install"].append("npm install")

        for script_name in scripts:
            cmd = f"npm run {script_name}"
            categorized = False
            for category, keywords in _NPM_CATEGORY_MAP.items():
                if any(kw == script_name.lower() for kw in keywords):
                    result[category].append(cmd)
                    categorized = True
                    break
            if not categorized:
                result["dev"].append(cmd)


# ---------------------------------------------------------------------------
# pyproject.toml parser
# ---------------------------------------------------------------------------

def _parse_pyproject_toml(project_root: str, result: Dict[str, List[str]]) -> None:
    """Parse pyproject.toml for test/build commands."""
    path = os.path.join(project_root, "pyproject.toml")
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return

    # Check for pytest config
    if "[tool.pytest" in content or "pytest" in content.lower():
        result["test"].append("pytest")
        result["test"].append("python3 -m pytest")

    # Check for build system
    if "[build-system]" in content:
        result["build"].append("python3 -m build")

    # Check for scripts
    if "[project.scripts]" in content:
        for m in re.finditer(r'^(\w[\w-]*)\s*=', content, re.MULTILINE):
            script = m.group(1)
            if script not in {"python", "pip"}:
                result["dev"].append(script)


# ---------------------------------------------------------------------------
# requirements.txt parser
# ---------------------------------------------------------------------------

def _parse_requirements(project_root: str, result: Dict[str, List[str]]) -> None:
    """Detect requirements files and generate install commands."""
    req_patterns = [
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "apps/backend/requirements.txt",
        "apps/backend/requirements-dev.txt",
        "backend/requirements.txt",
    ]
    for rel in req_patterns:
        path = os.path.join(project_root, rel)
        if os.path.isfile(path):
            result["install"].append(f"pip install -r {rel}")


# ---------------------------------------------------------------------------
# README parser
# ---------------------------------------------------------------------------

def _parse_readme(project_root: str, result: Dict[str, List[str]]) -> None:
    """Extract shell commands from README fenced code blocks."""
    for name in ("README.md", "readme.md", "README.rst", "README"):
        path = os.path.join(project_root, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue

        # Find fenced code blocks with bash/shell/sh language
        blocks = re.findall(
            r'```(?:bash|shell|sh|console)\s*\n(.*?)```',
            content, re.DOTALL | re.IGNORECASE,
        )
        for block in blocks:
            for line in block.strip().splitlines():
                line = line.strip()
                if line.startswith("$"):
                    line = line[1:].strip()
                if line.startswith("#") or not line:
                    continue
                # Classify
                low = line.lower()
                if any(kw in low for kw in ["install", "pip ", "npm i", "yarn add"]):
                    result["install"].append(line)
                elif any(kw in low for kw in ["test", "pytest", "jest"]):
                    result["test"].append(line)
                elif any(kw in low for kw in ["build", "compile"]):
                    result["build"].append(line)
                elif any(kw in low for kw in ["dev", "start", "serve", "run"]):
                    result["dev"].append(line)
        break  # Only parse first README


def format_discovered_commands(commands: Dict[str, List[str]]) -> str:
    """Format discovered commands as a readable string for reports."""
    lines = []
    for category in ("install", "dev", "build", "test", "smoke"):
        cmds = commands.get(category, [])
        if cmds:
            lines.append(f"  {category}:")
            for cmd in cmds:
                lines.append(f"    - {cmd}")
    return "\n".join(lines) if lines else "  (none discovered)"
