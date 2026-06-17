"""
StarAgent Skill Ingestor — parses Claude Skills repo into the registry.

Expected repo layout:
  <domain>/
    <skill-name>/
      SKILL.md          — main skill content (YAML frontmatter + markdown)
      scripts/          — optional tool scripts
      templates/        — optional templates
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from . import skill_registry

logger = logging.getLogger(__name__)


def ingest_repo(repo_path: str, source_repo: str = "alirezarezvani/claude-skills") -> Dict[str, Any]:
    """
    Walk repo and ingest all SKILL.md files.
    Returns stats dict: {total, domains, errors}.
    """
    skill_registry.init_db()

    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(repo_path):
        return {"total": 0, "domains": {}, "errors": [f"Path not found: {repo_path}"], "skipped": 0, "skipped_reasons": {}}

    skill_files = []
    skipped_reasons: Dict[str, int] = {}  # reason -> count
    for root, dirs, files in os.walk(repo_path):
        # Skip hidden directories — .gemini/skills/ contains duplicate plugin registrations
        hidden = [d for d in dirs if d.startswith(".")]
        for h in hidden:
            skipped_reasons[f"hidden_dir:{h}"] = skipped_reasons.get(f"hidden_dir:{h}", 0) + 1
        # Skip non-skill infrastructure dirs
        infra = [d for d in dirs if d in ("node_modules", "__pycache__")]
        for i_dir in infra:
            skipped_reasons[f"infra_dir:{i_dir}"] = skipped_reasons.get(f"infra_dir:{i_dir}", 0) + 1
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
        if "SKILL.md" in files:
            skill_files.append(os.path.join(root, "SKILL.md"))

    logger.info(f"Found {len(skill_files)} SKILL.md files in {repo_path} (skipped dirs: {skipped_reasons})")

    domains: Dict[str, int] = {}
    errors: List[str] = []
    total = 0
    collapsed = 0  # skills with same name across domains (upserted/overwritten)

    for skill_file in skill_files:
        try:
            was_update = _ingest_one(skill_file, repo_path, source_repo)
            if was_update:
                collapsed += 1
            # Derive domain from path
            rel = os.path.relpath(skill_file, repo_path)
            parts = rel.split(os.sep)
            domain = _normalize_domain(parts[0]) if len(parts) >= 2 else "unknown"
            domains[domain] = domains.get(domain, 0) + 1
            total += 1
        except Exception as e:
            errors.append(f"{skill_file}: {e}")
            logger.warning(f"Ingest error: {skill_file}: {e}")

    logger.info(f"Ingested {total} skills across {len(domains)} domains ({collapsed} name collisions collapsed)")
    return {"total": total, "domains": domains, "errors": errors,
            "skipped": sum(skipped_reasons.values()), "skipped_reasons": skipped_reasons,
            "collapsed": collapsed}



def _ingest_one(skill_file: str, repo_root: str, source_repo: str) -> bool:
    """Parse and upsert a single SKILL.md into the registry."""
    rel = os.path.relpath(skill_file, repo_root)
    parts = rel.split(os.sep)
    
    if len(parts) < 2:
        return False

    domain = _normalize_domain(parts[0])
    skill_dir = os.path.dirname(skill_file)
    
    with open(skill_file, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    # Parse YAML frontmatter
    name, description, tags = _parse_frontmatter(raw, parts)

    # Remove frontmatter from content for chunk storage
    content = _strip_frontmatter(raw)

    # Detect license
    license_info = "unknown"
    license_file = os.path.join(skill_dir, "LICENSE")
    if os.path.exists(license_file):
        try:
            with open(license_file, "r") as lf:
                first_line = lf.readline().strip()
                if "MIT" in first_line:
                    license_info = "MIT"
                elif "Apache" in first_line:
                    license_info = "Apache-2.0"
                else:
                    license_info = first_line[:50]
        except Exception:
            pass

    # Upsert skill
    skill_id, was_update = skill_registry.upsert_skill(
        name=name,
        domain=domain,
        description=description,
        source_repo=source_repo,
        source_path=rel,
        skill_md_content=raw,
        tags=tags,
        license_info=license_info,
    )
    if was_update:
        logger.debug(f"Name collision: '{name}' updated from {rel}")

    # Chunk content into sections by heading
    chunks = _chunk_by_heading(content)
    for heading, chunk_content in chunks:
        skill_registry.add_chunk(skill_id, heading, chunk_content)

    # Discover tool scripts
    scripts_dir = os.path.join(skill_dir, "scripts")
    if os.path.isdir(scripts_dir):
        for script_name in os.listdir(scripts_dir):
            if script_name.startswith("."):
                continue
            script_path = os.path.join(scripts_dir, script_name)
            if os.path.isfile(script_path):
                skill_registry.add_tool(
                    skill_id=skill_id,
                    name=os.path.splitext(script_name)[0],
                    description=f"Script from {name}",
                    script_path=os.path.relpath(script_path, repo_root),
                )

    return was_update


def _parse_frontmatter(raw: str, path_parts: List[str]):
    """Extract name, description, tags from YAML frontmatter or path."""
    name = path_parts[-2] if len(path_parts) >= 2 else "unknown"
    description = ""
    tags = []

    # Check for YAML frontmatter
    fm_match = re.match(r'^---\s*\n(.*?)\n---', raw, re.DOTALL)
    if fm_match:
        fm_text = fm_match.group(1)
        # Parse name
        nm = re.search(r'^name:\s*["\']?(.+?)["\']?\s*$', fm_text, re.MULTILINE)
        if nm:
            name = nm.group(1).strip().strip('"').strip("'")
        # Parse description
        desc = re.search(r'^description:\s*(.+?)$', fm_text, re.MULTILINE)
        if desc:
            description = desc.group(1).strip().strip('"').strip("'")
        # Parse tags
        tag_match = re.search(r'^tags:\s*\[(.+?)\]', fm_text, re.MULTILINE)
        if tag_match:
            tags = [t.strip().strip('"').strip("'") for t in tag_match.group(1).split(",")]

    # Fallback description from first paragraph
    if not description:
        content = _strip_frontmatter(raw)
        for line in content.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("---"):
                description = line[:200]
                break
        if not description:
            description = f"Skill: {name}"

    return name, description, tags


def _strip_frontmatter(raw: str) -> str:
    """Remove YAML frontmatter from markdown content."""
    return re.sub(r'^---\s*\n.*?\n---\s*\n?', '', raw, count=1, flags=re.DOTALL)


def _chunk_by_heading(content: str, max_chunk_size: int = 2000) -> List[tuple]:
    """Split markdown content by headings into (heading, content) tuples."""
    lines = content.split("\n")
    chunks = []
    current_heading = ""
    current_lines = []

    for line in lines:
        if re.match(r'^#{1,3}\s+', line):
            # Save previous chunk
            if current_lines:
                text = "\n".join(current_lines).strip()
                if text:
                    # Split oversized chunks
                    if len(text) > max_chunk_size:
                        for i in range(0, len(text), max_chunk_size):
                            chunks.append((current_heading, text[i:i+max_chunk_size]))
                    else:
                        chunks.append((current_heading, text))
            current_heading = line.strip().lstrip("#").strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    # Last chunk
    if current_lines:
        text = "\n".join(current_lines).strip()
        if text:
            chunks.append((current_heading, text))

    # If no headings found, treat entire content as one chunk
    if not chunks and content.strip():
        chunks.append(("", content.strip()[:max_chunk_size]))

    return chunks


def _normalize_domain(raw_domain: str) -> str:
    """Normalize directory name to a clean domain label."""
    mapping = {
        "engineering-team": "engineering",
        "marketing-skill": "marketing",
        "business-growth": "business",
        "c-level-advisor": "c-level",
        "project-management": "project-management",
        "ra-qm-team": "compliance",
        "custom-gpt": "custom-gpt",
        "orchestration": "engineering",
        "documentation": "documentation",
        "agents": "engineering",
    }
    return mapping.get(raw_domain, raw_domain)
