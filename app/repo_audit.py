from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .llm_client import LLMClient

logger = logging.getLogger(__name__)


ALLOWED_TEXT_EXTS = {
    ".md",
    ".txt",
    ".rst",
    ".py",
    ".js",
    ".ts",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
    ".sh",
}


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _read_text_file(path: Path, *, max_bytes: int = 250_000) -> Tuple[Optional[str], Optional[str]]:
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


def _extract_section_bullets(md: str, *, header_contains: str) -> List[str]:
    """
    Extract bullet items under a markdown header that contains `header_contains`.
    Prefers bullets; falls back to lines in the section if no bullets exist.
    """
    bullets: List[str] = []
    lines: List[str] = []
    in_section = False
    want = (header_contains or "").strip().lower()
    for raw in (md or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if want and want in line.lower():
                in_section = True
                continue
            if in_section:
                break
        if not in_section:
            continue
        if line.startswith("-"):
            bullets.append(line[1:].lstrip())
        elif line.startswith("*"):
            bullets.append(line[1:].lstrip())
        else:
            lines.append(line)
    return [b for b in (bullets if bullets else lines) if b]


def _extract_between_headers(md: str, start_contains: str, stop_prefix: str = "##") -> str:
    start_contains = (start_contains or "").lower().strip()
    in_section = False
    out_lines: List[str] = []
    for raw in (md or "").splitlines():
        line = raw.rstrip()
        if line.strip().startswith(stop_prefix):
            if in_section and start_contains not in line.lower():
                break
            if start_contains and start_contains in line.lower():
                in_section = True
                out_lines.append(line)
                continue
        if in_section:
            out_lines.append(line)
    return "\n".join(out_lines).strip()


def _find_lines_with(text: str, needles: List[str], *, max_lines: int = 8) -> List[Tuple[int, str]]:
    if not text:
        return []
    needles_l = [n.lower() for n in (needles or []) if n]
    out: List[Tuple[int, str]] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        low = raw.lower()
        if any(n in low for n in needles_l):
            out.append((i, raw.rstrip()))
            if len(out) >= max_lines:
                break
    return out


def _score_entry_point(text: str) -> int:
    if not text:
        return 0
    low = text.lower()
    score = 0
    if "fastapi(" in low:
        score += 6
    if "app = fastapi" in low or "fastapi()" in low:
        score += 4
    if "@app.get" in low or "@app.post" in low or "@router.get" in low or "@router.post" in low:
        score += 4
    if "uvicorn" in low:
        score += 2
    if "app = fastapi(" in low:
        score += 4
    if "from fastapi import fastapi" in low or "import fastapi" in low:
        score += 1
    return score


def _extract_routes(text: str, *, max_routes: int = 40) -> List[str]:
    """
    Best-effort route extraction from FastAPI-style decorators.
    We keep this very simple and robust for grounding.
    """
    routes: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line.startswith("@"):
            continue
        if line.startswith("@app.") or line.startswith("@router."):
            # Example: @app.get("/health")
            if "(" in line and ")" in line:
                routes.append(line)
                if len(routes) >= max_routes:
                    break
    return routes


@dataclass
class RepoAuditInputs:
    root_path: str
    question: Optional[str] = None


class RepoAuditPipeline:
    """
    Bounded, artifact-first repository audit workflow.
    Artifacts are written under: .runtime/tasks/<task_id>/
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def artifact_dir(self, task_id: str) -> Path:
        return Path(".runtime") / "tasks" / task_id

    def _paths(self, task_id: str) -> Dict[str, Path]:
        d = self.artifact_dir(task_id)
        return {
            "dir": d,
            "file_index": d / "file_index.json",
            "architecture_map": d / "architecture_map.md",
            "entry_points": d / "entry_points.md",
            "risk_notes": d / "risk_notes.md",
            "open_questions": d / "open_questions.md",
            "audit_report": d / "audit_report.md",
        }

    def plan_steps(self, inputs: RepoAuditInputs) -> List[Dict[str, Any]]:
        steps: List[Dict[str, Any]] = []
        steps.append({"step_type": "discover_files", "instruction": f"Discover repository files under: {inputs.root_path}"})
        steps.append({"step_type": "identify_entry_points", "instruction": "Identify main entry points and how the API starts"})
        steps.append({"step_type": "architecture_map", "instruction": "Summarize architecture and request flow grounded in key modules"})
        steps.append({"step_type": "risk_notes", "instruction": "Identify risks, unknowns, and operational caveats"})
        steps.append({"step_type": "audit_report", "instruction": "Generate the final grounded audit report and open questions"})
        return steps

    def _discover_files(self, inputs: RepoAuditInputs) -> Dict[str, Any]:
        root = Path(inputs.root_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return {"ok": False, "error": f"root_path not found or not a directory: {root}"}

        selected: List[Path] = []
        skipped: List[Dict[str, Any]] = []
        max_files = 2500

        skip_top = {".runtime", ".venv", "dist", "recovery", "logs", "data", "sandbox_test", "staragent.egg-info", "macagent.egg-info"}
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                rel_parts = p.relative_to(root).parts
            except Exception:
                rel_parts = p.parts
            if rel_parts and rel_parts[0] in skip_top:
                continue
            if any(part.startswith(".") for part in rel_parts):
                continue
            if ".git" in rel_parts:
                continue
            if p.suffix and p.suffix.lower() not in ALLOWED_TEXT_EXTS:
                continue
            selected.append(p)
            if len(selected) >= max_files:
                skipped.append({"path": str(p), "reason": f"file_limit_reached:{max_files}"})
                break

        files_out: List[Dict[str, Any]] = []
        for p in sorted(selected):
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

        # Include a quick top-level directory preview for grounding.
        top_dirs = sorted({Path(f["path"]).parts[0] for f in files_out if f.get("path")})
        return {"ok": True, "root": str(root), "files": files_out, "skipped": skipped, "top_level": top_dirs[:60]}

    def _find_file_entry(self, idx: Dict[str, Any], rel_path: str) -> Optional[Dict[str, Any]]:
        for f in idx.get("files") or []:
            if f.get("path") == rel_path:
                return f
        return None

    def _rank_entry_points(self, idx: Dict[str, Any]) -> List[str]:
        # Deterministic heuristics first (small-model friendly).
        preferred = [
            "app/main.py",
            "main.py",
            "app.py",
            "server.py",
            "api.py",
            "src/main.py",
            "wsgi.py",
            "asgi.py",
        ]
        present = {f.get("path") for f in (idx.get("files") or [])}
        ranked = [p for p in preferred if p in present]
        # Add any other likely candidates (bounded).
        for p in sorted(present):
            name = os.path.basename(str(p))
            if name in {"main.py", "app.py", "server.py"} and p not in ranked:
                ranked.append(str(p))
            if len(ranked) >= 12:
                break
        return ranked

    async def _write_entry_points(self, task_id: str, inputs: RepoAuditInputs, idx: Dict[str, Any]) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)

        candidates = self._rank_entry_points(idx)
        scored: List[Tuple[int, str, List[Tuple[int, str]]]] = []
        for rel in candidates[:12]:
            ent = self._find_file_entry(idx, rel)
            if not ent:
                continue
            text, err = _read_text_file(Path(ent["abs_path"]))
            if text is None:
                continue
            score = _score_entry_point(text)
            evidence = _find_lines_with(
                text,
                ["FastAPI(", "@app.", "@router.", "uvicorn", "/v1/", "/health", "chat/completions"],
                max_lines=10,
            )
            scored.append((score, rel, evidence))

        scored.sort(key=lambda t: (-t[0], t[1]))
        top = scored[:5]

        md_lines: List[str] = []
        md_lines.append("# Entry Points\n")
        md_lines.append("## Candidates (ranked)\n")
        if not top:
            md_lines.append("- (no readable candidates found)\n")
        for score, rel, _ev in top:
            md_lines.append(f"- `{rel}` (score={score})")
        md_lines.append("")

        md_lines.append("## Evidence\n")
        for score, rel, ev in top:
            md_lines.append(f"### {rel}\n")
            if not ev:
                md_lines.append("- (no obvious entry-point signals found)\n")
                continue
            for ln, line in ev[:10]:
                md_lines.append(f"- L{ln}: `{line.strip()[:240]}`")
            md_lines.append("")

        md_lines.append("## Notes\n")
        md_lines.append("- This file ranking is heuristic and grounded in exact matched lines above.\n")
        paths["entry_points"].write_text("\n".join(md_lines).rstrip() + "\n", encoding="utf-8")
        return {"ok": True, "entry_points": str(paths["entry_points"]), "candidates": candidates, "ranked": [{"path": p, "score": s} for s, p, _ in top]}

    async def _write_architecture_map(self, task_id: str, inputs: RepoAuditInputs, idx: Dict[str, Any]) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)

        # Deterministic, grounded architecture map (small-model reliable).
        root = idx.get("root") or ""
        files = [f.get("path") for f in (idx.get("files") or []) if f.get("path")]
        top_level = idx.get("top_level") or sorted({Path(p).parts[0] for p in files})[:60]

        # Ground request flow via app/main.py if present.
        main_ent = self._find_file_entry(idx, "app/main.py") or self._find_file_entry(idx, "main.py")
        routes: List[str] = []
        main_signals: List[Tuple[int, str]] = []
        if main_ent:
            text, err = _read_text_file(Path(main_ent["abs_path"]))
            if text:
                routes = _extract_routes(text)
                main_signals = _find_lines_with(text, ["FastAPI(", "chat/completions", "/health", "determine_execution_route"], max_lines=10)

        # Key modules: app/*.py shortlist
        key_modules = [p for p in files if p.startswith("app/") and p.endswith(".py")]
        key_modules = sorted(key_modules)[:40]

        md_lines: List[str] = []
        md_lines.append("# Architecture Map\n")
        md_lines.append("## High-Level Structure\n")
        md_lines.append(f"- Root: `{root}`")
        md_lines.append("- Top-level folders/files:")
        for name in (top_level or [])[:40]:
            md_lines.append(f"  - `{name}`")
        md_lines.append("")

        md_lines.append("## Main Request Flow (grounded)\n")
        if main_ent:
            md_lines.append(f"- Likely API entry module: `{Path(main_ent['abs_path']).name}` at `{main_ent.get('path')}`")
            if main_signals:
                md_lines.append("- Key signals:")
                for ln, line in main_signals:
                    md_lines.append(f"  - L{ln}: `{line.strip()[:240]}`")
            if routes:
                md_lines.append("- Routes (decorators found):")
                for r in routes[:30]:
                    md_lines.append(f"  - `{r}`")
        else:
            md_lines.append("- (No obvious entry module found in file index.)")
        md_lines.append("")

        md_lines.append("## Key Modules (by path)\n")
        if key_modules:
            for p in key_modules[:40]:
                md_lines.append(f"- `{p}`")
        else:
            md_lines.append("- (No app/*.py files indexed.)")
        md_lines.append("")

        paths["architecture_map"].write_text("\n".join(md_lines).rstrip() + "\n", encoding="utf-8")
        return {"ok": True, "architecture_map": str(paths["architecture_map"])}

    async def _write_risk_notes(self, task_id: str, inputs: RepoAuditInputs) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        entry = paths["entry_points"].read_text(encoding="utf-8") if paths["entry_points"].exists() else ""
        arch = paths["architecture_map"].read_text(encoding="utf-8") if paths["architecture_map"].exists() else ""
        question = inputs.question or "(none)"

        system = (
            "You are a pragmatic engineering auditor.\n"
            "Write grounded risk notes in Markdown.\n"
            "Rules:\n"
            "- Do not invent dates, tables, or files.\n"
            "- Refer to concrete file paths when possible.\n"
            "Output sections:\n"
            "1) Risks\n2) Unknowns\n3) Recommended Next Checks\n"
        )
        user = f"Audit question: {question}\n\nENTRY POINTS:\n{entry[:6000]}\n\nARCH:\n{arch[:9000]}"
        md = (await self.llm.text([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.1)).strip()
        if not md:
            md = "# Risk Notes\n\n- (none)\n"
        paths["risk_notes"].write_text(md + "\n", encoding="utf-8")
        return {"ok": True, "risk_notes": str(paths["risk_notes"])}

    async def _write_audit_report(self, task_id: str, inputs: RepoAuditInputs) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        entry = paths["entry_points"].read_text(encoding="utf-8") if paths["entry_points"].exists() else ""
        arch = paths["architecture_map"].read_text(encoding="utf-8") if paths["architecture_map"].exists() else ""
        risks = paths["risk_notes"].read_text(encoding="utf-8") if paths["risk_notes"].exists() else ""
        question = inputs.question or "(none)"

        # Build a deterministic, grounded audit_report.md so small models cannot derail format/grounding.
        primary_entry = None
        for raw in entry.splitlines():
            line = raw.strip()
            if line.startswith("- `") and "`" in line[3:]:
                primary_entry = line.split("`")[1]
                break

        unknowns = _extract_section_bullets(risks, header_contains="unknowns")
        next_checks = _extract_section_bullets(risks, header_contains="recommended next checks")

        report_lines: List[str] = []
        report_lines.append("# Audit Report\n")
        report_lines.append("## 1) Summary\n")
        report_lines.append(f"- Audit question: {question}")
        if primary_entry:
            report_lines.append(f"- Primary API entry candidate: `{primary_entry}`")
        report_lines.append("")

        report_lines.append("## 2) Entry Points\n")
        report_lines.append(entry.strip() if entry.strip() else "- (missing entry_points.md)")
        report_lines.append("")

        report_lines.append("## 3) Architecture\n")
        report_lines.append(arch.strip() if arch.strip() else "- (missing architecture_map.md)")
        report_lines.append("")

        report_lines.append("## 4) Risks\n")
        report_lines.append(risks.strip() if risks.strip() else "- (missing risk_notes.md)")
        report_lines.append("")

        report_lines.append("## 5) Open Questions\n")
        if unknowns:
            for q in unknowns[:50]:
                report_lines.append(f"- {q}")
        else:
            report_lines.append("- (none captured)")
        report_lines.append("")

        report_lines.append("## 6) Next Actions\n")
        if next_checks:
            for a in next_checks[:50]:
                report_lines.append(f"- {a}")
        else:
            report_lines.append("- (none captured)")
        report_lines.append("")

        md = "\n".join(report_lines).rstrip() + "\n"
        paths["audit_report"].write_text(md, encoding="utf-8")

        # Write open_questions.md based on Unknowns and any explicit chunk questions (if present later).
        if unknowns:
            paths["open_questions"].write_text("\n".join(f"- {q}" for q in unknowns[:50]) + "\n", encoding="utf-8")
        else:
            paths["open_questions"].write_text("- (none captured)\n", encoding="utf-8")
        return {"ok": True, "audit_report": str(paths["audit_report"]), "open_questions": str(paths["open_questions"])}

    async def run_step(self, task_id: str, step: Dict[str, Any], inputs: RepoAuditInputs) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        step_type = step.get("step_type") or "generic"

        if step_type == "discover_files":
            idx = self._discover_files(inputs)
            if not idx.get("ok"):
                return {"ok": False, "error": idx.get("error", "discover_failed")}
            paths["file_index"].write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"ok": True, "artifact_path": str(paths["file_index"]), "files_count": len(idx.get("files") or [])}

        if step_type == "identify_entry_points":
            if not paths["file_index"].exists():
                return {"ok": False, "error": "missing file_index.json (run discover_files first)"}
            idx = json.loads(paths["file_index"].read_text(encoding="utf-8"))
            return await self._write_entry_points(task_id, inputs, idx)

        if step_type == "architecture_map":
            if not paths["file_index"].exists():
                return {"ok": False, "error": "missing file_index.json (run discover_files first)"}
            idx = json.loads(paths["file_index"].read_text(encoding="utf-8"))
            return await self._write_architecture_map(task_id, inputs, idx)

        if step_type == "risk_notes":
            return await self._write_risk_notes(task_id, inputs)

        if step_type == "audit_report":
            return await self._write_audit_report(task_id, inputs)

        return {"ok": True, "note": f"noop:{step_type}"}
