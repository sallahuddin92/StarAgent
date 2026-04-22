from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .llm_client import OllamaChatClient

logger = logging.getLogger(__name__)


ALLOWED_TEXT_EXTS = {
    ".md",
    ".txt",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".log",
}


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _read_text_file(path: Path, *, max_bytes: int = 400_000) -> Tuple[Optional[str], Optional[str]]:
    try:
        st = path.stat()
        if st.st_size > max_bytes:
            return None, f"skipped: too large ({st.st_size} bytes)"
        raw = path.read_bytes()
        try:
            return raw.decode("utf-8"), None
        except UnicodeDecodeError:
            return raw.decode("latin-1"), "decoded as latin-1 (non-utf8)"
    except Exception as e:
        return None, f"read_error: {e}"

_LETTERHEAD_RE = re.compile(r"^\s*(?:[*_`>#-]\s*){0,3}(to|from|date|subject)\s*:\s*", re.IGNORECASE)
_YOUR_PLACEHOLDER_RE = re.compile(r"\[\s*your[^\]]{0,80}\]", re.IGNORECASE)


def _style_violations(md: str) -> List[str]:
    violations: List[str] = []
    for raw in (md or "").splitlines():
        if _LETTERHEAD_RE.match(raw):
            violations.append("letterhead_field")
            break
    if _YOUR_PLACEHOLDER_RE.search(md or ""):
        violations.append("bracket_placeholder")
    return violations


def _sanitize_style(md: str) -> str:
    # Last-resort deterministic cleanup: remove letterhead lines and [Your ...] placeholders.
    out_lines: List[str] = []
    for raw in (md or "").splitlines():
        if _LETTERHEAD_RE.match(raw):
            continue
        out_lines.append(raw)
    cleaned = "\n".join(out_lines)
    cleaned = _YOUR_PLACEHOLDER_RE.sub("", cleaned)
    return cleaned.strip()


@dataclass
class WritingInputs:
    root_path: str
    goal: str
    files: Optional[List[str]] = None


class WritingPipeline:
    """
    Bounded writing workflow that ingests notes/docs and produces:
      source_index.json
      outline.md
      draft.md
      final_output.md
    """

    def __init__(self, llm: OllamaChatClient):
        self.llm = llm

    def artifact_dir(self, task_id: str) -> Path:
        return Path(".runtime") / "tasks" / task_id

    def _paths(self, task_id: str) -> Dict[str, Path]:
        d = self.artifact_dir(task_id)
        return {
            "dir": d,
            "source_index": d / "source_index.json",
            "outline": d / "outline.md",
            "draft": d / "draft.md",
            "final_output": d / "final_output.md",
        }

    def plan_steps(self, inputs: WritingInputs) -> List[Dict[str, Any]]:
        return [
            {"step_type": "discover_sources", "instruction": f"Discover notes/docs under: {inputs.root_path}"},
            {"step_type": "extract_points", "instruction": "Extract key points grounded in sources"},
            {"step_type": "outline", "instruction": "Generate an outline aligned to the writing goal"},
            {"step_type": "draft", "instruction": "Write a first draft"},
            {"step_type": "finalize", "instruction": "Refine into final_output.md"},
        ]

    def _discover_sources(self, inputs: WritingInputs) -> Dict[str, Any]:
        root = Path(inputs.root_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return {"ok": False, "error": f"root_path not found or not a directory: {root}"}

        selected: List[Path] = []
        skipped: List[Dict[str, Any]] = []

        if inputs.files:
            for fp in inputs.files:
                p = (root / fp).resolve() if not os.path.isabs(fp) else Path(fp).expanduser().resolve()
                if not p.exists() or not p.is_file():
                    skipped.append({"path": str(fp), "reason": "missing_or_not_file"})
                    continue
                if p.suffix and p.suffix.lower() not in ALLOWED_TEXT_EXTS:
                    skipped.append({"path": str(fp), "reason": f"unsupported_ext:{p.suffix}"})
                    continue
                selected.append(p)
        else:
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                try:
                    rel_parts = p.relative_to(root).parts
                except Exception:
                    rel_parts = p.parts
                if any(part.startswith(".") for part in rel_parts):
                    continue
                if ".git" in rel_parts:
                    continue
                if p.suffix and p.suffix.lower() not in ALLOWED_TEXT_EXTS:
                    continue
                selected.append(p)
                if len(selected) >= 200:
                    skipped.append({"path": str(p), "reason": "file_limit_reached:200"})
                    break

        files_out: List[Dict[str, Any]] = []
        for p in sorted(selected)[:200]:
            try:
                raw = p.read_bytes()
                files_out.append(
                    {
                        "path": str(p.relative_to(root)),
                        "abs_path": str(p),
                        "size_bytes": len(raw),
                        "sha256": _sha256_bytes(raw),
                    }
                )
            except Exception as e:
                skipped.append({"path": str(p), "reason": f"read_error:{e}"})

        return {"ok": True, "root": str(root), "files": files_out, "skipped": skipped}

    async def _extract_points(self, inputs: WritingInputs, sources: Dict[str, Any]) -> str:
        excerpts: List[str] = []
        for f in (sources.get("files") or [])[:10]:
            text, err = _read_text_file(Path(f["abs_path"]))
            if text is None:
                continue
            head = "\n".join(text.splitlines()[:220])
            excerpts.append(f"SOURCE: {f.get('path')}\n---\n{head}\n")

        system = (
            "You are a writing assistant. Extract grounded points only.\n"
            "Output Markdown with sections:\n"
            "1) Key Points\n2) Notable Quotes (short)\n3) Gaps\n"
        )
        user = f"WRITING GOAL:\n{inputs.goal}\n\nSOURCES:\n\n" + "\n\n".join(excerpts)[:14000]
        md = (
            await self.llm.text(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
            )
        ).strip()
        return md or "# Key Points\n\n- (none)\n"

    async def _outline(self, goal: str, points_md: str) -> str:
        system = "Create a structured Markdown outline that satisfies the goal, grounded in the provided points."
        user = f"GOAL:\n{goal}\n\nPOINTS:\n{points_md[:12000]}"
        md = (
            await self.llm.text(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
            )
        ).strip()
        return md or "# Outline\n\n- (none)\n"

    async def _draft(self, goal: str, outline_md: str, points_md: str) -> str:
        system = (
            "Write a Markdown draft based on the outline.\n"
            "Rules:\n"
            "- Stay grounded in the points.\n"
            "- Use clear headings.\n"
        )
        user = f"GOAL:\n{goal}\n\nOUTLINE:\n{outline_md[:8000]}\n\nPOINTS:\n{points_md[:8000]}"
        md = (
            await self.llm.text(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.2,
            )
        ).strip()
        return md or "# Draft\n\n(insufficient source material)\n"

    async def _finalize(self, goal: str, draft_md: str) -> str:
        system = (
            "Refine the draft into a polished final Markdown output.\n"
            "Rules:\n"
            "- Keep it concise and aligned to the goal.\n"
            "- Do not invent dates, names, signatures, or recipients unless provided.\n"
            "- Do not include TO:/FROM:/DATE:/SUBJECT: lines.\n"
            "- Do not include bracket placeholders like [Your Name], [Your Team], etc.\n"
            "- If the goal mentions 'memo', write a memo-style body with headings, but without letterhead.\n"
        )
        user = f"GOAL:\n{goal}\n\nDRAFT:\n{draft_md[:14000]}"
        md = (
            await self.llm.text(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.2,
            )
        ).strip()
        md = md or draft_md

        violations = _style_violations(md)
        if violations:
            # One bounded retry: ask for a rewrite removing the forbidden patterns.
            system2 = (
                "Rewrite the content into clean Markdown.\n"
                "Hard requirements:\n"
                "- MUST NOT contain any TO:/FROM:/DATE:/SUBJECT: lines.\n"
                "- MUST NOT contain any bracket placeholders like [Your ...].\n"
                "- Keep it aligned to the GOAL.\n"
                "- Preserve useful content; do not add fabricated details.\n"
                "Return Markdown only."
            )
            user2 = f"GOAL:\n{goal}\n\nFORBIDDEN_PATTERNS_DETECTED:\n- " + "\n- ".join(violations) + f"\n\nTEXT:\n{md[:14000]}"
            md2 = (
                await self.llm.text(
                    [{"role": "system", "content": system2}, {"role": "user", "content": user2}],
                    temperature=0.2,
                )
            ).strip()
            if md2:
                md = md2

        # Final verifier + deterministic cleanup if still violating.
        if _style_violations(md):
            md = _sanitize_style(md)
        return md or draft_md

    async def run_step(self, task_id: str, step: Dict[str, Any], inputs: WritingInputs) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        st = step.get("step_type") or "generic"

        if st == "discover_sources":
            idx = self._discover_sources(inputs)
            if not idx.get("ok"):
                return {"ok": False, "error": idx.get("error", "discover_failed")}
            paths["source_index"].write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"ok": True, "artifact_path": str(paths["source_index"]), "files_count": len(idx.get("files") or [])}

        if st == "extract_points":
            if not paths["source_index"].exists():
                return {"ok": False, "error": "missing source_index.json"}
            idx = json.loads(paths["source_index"].read_text(encoding="utf-8"))
            md = await self._extract_points(inputs, idx)
            # Store extracted points in outline.md as a staging area (keeps artifacts minimal).
            paths["outline"].write_text(md + "\n", encoding="utf-8")
            return {"ok": True, "artifact_path": str(paths["outline"])}

        if st == "outline":
            points = paths["outline"].read_text(encoding="utf-8") if paths["outline"].exists() else ""
            md = await self._outline(inputs.goal, points)
            paths["outline"].write_text(md + "\n", encoding="utf-8")
            return {"ok": True, "artifact_path": str(paths["outline"])}

        if st == "draft":
            outline = paths["outline"].read_text(encoding="utf-8") if paths["outline"].exists() else ""
            points = outline
            md = await self._draft(inputs.goal, outline, points)
            paths["draft"].write_text(md + "\n", encoding="utf-8")
            return {"ok": True, "artifact_path": str(paths["draft"])}

        if st == "finalize":
            draft = paths["draft"].read_text(encoding="utf-8") if paths["draft"].exists() else ""
            md = await self._finalize(inputs.goal, draft)
            paths["final_output"].write_text(md + "\n", encoding="utf-8")
            return {"ok": True, "artifact_path": str(paths["final_output"])}

        return {"ok": True, "note": f"noop:{st}"}
