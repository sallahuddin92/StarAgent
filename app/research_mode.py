from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .llm_client import OllamaChatClient, extract_json_object

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
    ".csv",
    ".log",
}


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _read_text_file(path: Path, *, max_bytes: int = 1_500_000) -> Tuple[Optional[str], Optional[str]]:
    """
    Read a file as UTF-8 text. Returns (text, error).
    Designed for local research docs; skips large/binary files gracefully.
    """
    try:
        st = path.stat()
        if st.st_size > max_bytes:
            return None, f"skipped: too large ({st.st_size} bytes)"
        raw = path.read_bytes()
        try:
            return raw.decode("utf-8"), None
        except UnicodeDecodeError:
            # Best-effort: latin-1 decode.
            return raw.decode("latin-1"), "decoded as latin-1 (non-utf8)"
    except Exception as e:
        return None, f"read_error: {e}"


def chunk_text(text: str, *, max_chars: int = 2200, overlap: int = 200) -> List[str]:
    """
    Simple character-based chunking for small local models.
    """
    if not text:
        return []
    text = text.replace("\r\n", "\n")
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        j = min(i + max_chars, n)
        out.append(text[i:j])
        if j >= n:
            break
        i = max(0, j - overlap)
    return out


def _normalize_for_contains(s: str) -> str:
    s = (s or "").strip().lower()
    # Loosen matching a bit for small-model punctuation variance.
    s = s.replace("?", "").replace("“", '"').replace("”", '"').replace("’", "'")
    return s


def _explicit_questions_only(questions: List[str], chunk: str) -> List[str]:
    """
    Small models sometimes invent open questions even when asked not to.
    As a conservative grounding rule, keep only questions whose normalized
    text appears in the chunk text (normalized), allowing minor punctuation drift.
    """
    if not questions:
        return []
    hay = _normalize_for_contains(chunk)
    out: List[str] = []
    for q in questions:
        qn = _normalize_for_contains(q)
        if not qn:
            continue
        if qn in hay:
            out.append(str(q).strip())
            continue
        # Also allow matching without leading "what/why/how" wrappers if chunk has the key phrase.
        if len(qn) > 12 and any(qn.endswith(suf) for suf in (" prioritized", " prioritization", " priority")):
            if qn in hay:
                out.append(str(q).strip())
    return out


@dataclass
class ResearchInputs:
    root_path: str
    files: Optional[List[str]] = None
    question: Optional[str] = None
    mode: str = "research"  # summary | research | comparison


class ResearchPipeline:
    """
    Staged local document research workflow that produces artifacts under:
      .runtime/tasks/<task_id>/

    This pipeline is designed to be resumable: each stage checks existing
    artifact files and skips work that has already been completed.
    """

    def __init__(self, llm: OllamaChatClient):
        self.llm = llm

    def plan_steps(self, inputs: ResearchInputs) -> List[Dict[str, Any]]:
        # Steps are deterministic for stability on small models.
        steps: List[Dict[str, Any]] = []
        steps.append({"step_type": "discover_files", "instruction": f"Discover files under: {inputs.root_path}"})
        # File steps are filled in after discovery; we add a placeholder marker.
        steps.append({"step_type": "expand_file_steps", "instruction": "Expand per-file summarization steps"})
        steps.append({"step_type": "synthesis", "instruction": "Cross-file synthesis and research brief"})
        steps.append({"step_type": "final_report", "instruction": "Write final_report.md and open_questions.md"})
        return steps

    def artifact_dir(self, task_id: str) -> Path:
        return Path(".runtime") / "tasks" / task_id

    def _paths(self, task_id: str) -> Dict[str, Path]:
        d = self.artifact_dir(task_id)
        return {
            "dir": d,
            "file_index": d / "file_index.json",
            "chunk_summaries": d / "chunk_summaries.json",
            "file_summaries": d / "file_summaries.md",
            "research_brief": d / "research_brief.md",
            "open_questions": d / "open_questions.md",
            "final_report": d / "final_report.md",
        }

    def _discover_files(self, inputs: ResearchInputs) -> Dict[str, Any]:
        root = Path(inputs.root_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return {"ok": False, "error": f"root_path not found or not a directory: {root}"}

        selected: List[Path] = []
        skipped: List[Dict[str, Any]] = []

        if inputs.files:
            for fp in inputs.files:
                p = (root / fp).resolve() if not os.path.isabs(fp) else Path(fp).expanduser().resolve()
                try:
                    if not p.exists() or not p.is_file():
                        skipped.append({"path": str(fp), "reason": "missing_or_not_file"})
                        continue
                    if p.suffix.lower() not in ALLOWED_TEXT_EXTS:
                        skipped.append({"path": str(fp), "reason": f"unsupported_ext:{p.suffix}"})
                        continue
                    selected.append(p)
                except Exception as e:
                    skipped.append({"path": str(fp), "reason": f"error:{e}"})
        else:
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                # Skip obvious junk
                try:
                    rel_parts = p.relative_to(root).parts
                except Exception:
                    rel_parts = p.parts
                if any(part.startswith(".") for part in rel_parts):
                    continue
                if ".git" in rel_parts:
                    continue
                if p.suffix.lower() not in ALLOWED_TEXT_EXTS:
                    continue
                selected.append(p)

        files_out: List[Dict[str, Any]] = []
        for p in sorted(selected):
            try:
                raw = p.read_bytes()
                files_out.append(
                    {
                        "path": str(p.relative_to(root)) if root in p.parents else str(p),
                        "abs_path": str(p),
                        "size_bytes": len(raw),
                        "sha256": _sha256_bytes(raw),
                    }
                )
            except Exception as e:
                skipped.append({"path": str(p), "reason": f"read_error:{e}"})

        return {"ok": True, "root": str(root), "files": files_out, "skipped": skipped}

    async def _summarize_chunk(self, chunk: str, *, question: str, mode: str) -> Dict[str, Any]:
        # Keep prompt strict for small models.
        system = (
            "You are a document extraction assistant. Return ONLY valid JSON with keys:\n"
            '{ "summary": string, "key_points": [string], "open_questions": [string] }\n'
            "Rules:\n"
            "- Ground everything in the CHUNK.\n"
            "- open_questions MUST be explicitly present in the CHUNK as questions/unknowns. Do not invent.\n"
            "No extra keys. No markdown."
        )
        user = f"Mode: {mode}\nQuestion: {question}\n\nCHUNK:\n{chunk}"
        text = await self.llm.text(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
        )
        obj = extract_json_object(text)
        # Ensure stable shape.
        open_q = [str(x).strip() for x in (obj.get("open_questions") or []) if str(x).strip()]
        open_q = _explicit_questions_only(open_q, chunk)
        return {
            "summary": str(obj.get("summary") or "").strip(),
            "key_points": [str(x).strip() for x in (obj.get("key_points") or []) if str(x).strip()],
            "open_questions": open_q,
        }

    async def summarize_file(self, task_id: str, inputs: ResearchInputs, file_entry: Dict[str, Any]) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)

        abs_path = Path(file_entry["abs_path"])
        text, err = _read_text_file(abs_path)
        if text is None:
            return {"ok": False, "error": err or "read_failed", "file": file_entry.get("path")}

        # Load existing chunk summaries and resume if present.
        chunk_db: Dict[str, Any] = {}
        if paths["chunk_summaries"].exists():
            try:
                chunk_db = json.loads(paths["chunk_summaries"].read_text(encoding="utf-8"))
            except Exception:
                chunk_db = {}

        rel = file_entry.get("path") or str(abs_path)
        file_key = rel
        existing = chunk_db.get(file_key) or {}

        chunks = chunk_text(text)
        question = inputs.question or "Summarize the content."

        chunk_summaries: List[Dict[str, Any]] = existing.get("chunks") if isinstance(existing, dict) else None
        if not chunk_summaries:
            chunk_summaries = []

        start_idx = len(chunk_summaries)
        for idx in range(start_idx, len(chunks)):
            s = await self._summarize_chunk(chunks[idx], question=question, mode=inputs.mode)
            chunk_summaries.append({"chunk_index": idx, **s})
            # Persist after each chunk for resumability.
            chunk_db[file_key] = {
                "file_sha256": file_entry.get("sha256"),
                "chunks_total": len(chunks),
                "chunks": chunk_summaries,
            }
            paths["chunk_summaries"].write_text(json.dumps(chunk_db, ensure_ascii=False, indent=2), encoding="utf-8")

        # Derive a file summary (lightweight, model-assisted but small).
        points = []
        open_q = []
        for c in chunk_summaries:
            points.extend(c.get("key_points") or [])
            open_q.extend(c.get("open_questions") or [])
        points = list(dict.fromkeys([p for p in points if p]))
        open_q = list(dict.fromkeys([q for q in open_q if q]))

        # Compact file summary prompt (small models).
        system = "Write a concise file summary (5-10 bullet points) based only on the provided extracted key points."
        user = "Key points:\n" + "\n".join(f"- {p}" for p in points[:40])
        file_summary = (await self.llm.text([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.1)).strip()

        # Append/update file_summaries.md
        block = f"\n\n## {rel}\n\n{file_summary}\n"
        existing_md = paths["file_summaries"].read_text(encoding="utf-8") if paths["file_summaries"].exists() else ""
        if f"## {rel}\n" not in existing_md:
            paths["file_summaries"].write_text(existing_md + block, encoding="utf-8")

        return {"ok": True, "file": rel, "summary_written": True, "open_questions_count": len(open_q)}

    async def synthesize(self, task_id: str, inputs: ResearchInputs, file_index: Dict[str, Any]) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)

        question = inputs.question or "Synthesize the documents."

        # Load file summaries markdown (small enough), fallback to chunk summaries.
        file_summaries_md = paths["file_summaries"].read_text(encoding="utf-8") if paths["file_summaries"].exists() else ""
        if not file_summaries_md and paths["chunk_summaries"].exists():
            file_summaries_md = paths["chunk_summaries"].read_text(encoding="utf-8")[:8000]

        system = (
            "You are a research synthesizer. Produce a short research brief in Markdown with sections:\n"
            "1) Key Findings\n2) Evidence Notes\n3) Open Questions\n"
            "Be concise and grounded in the provided summaries."
        )
        user = f"Question: {question}\n\nSUMMARIES:\n{file_summaries_md[:12000]}"
        brief = (await self.llm.text([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.1)).strip()
        paths["research_brief"].write_text(brief + "\n", encoding="utf-8")
        return {"ok": True, "research_brief": str(paths['research_brief'])}

    async def final_report(self, task_id: str, inputs: ResearchInputs) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        brief = paths["research_brief"].read_text(encoding="utf-8") if paths["research_brief"].exists() else ""
        file_summaries = paths["file_summaries"].read_text(encoding="utf-8") if paths["file_summaries"].exists() else ""

        system = (
            "You are a senior technical writer. Write a final research report in Markdown with sections:\n"
            "1) Executive Summary\n2) Findings\n3) Recommendations\n4) Open Questions\n"
            "Ground the report in the provided brief and file summaries."
        )
        question = inputs.question or "Summarize the documents."
        user = f"Question: {question}\n\nBRIEF:\n{brief[:8000]}\n\nFILE SUMMARIES:\n{file_summaries[:12000]}"
        report = (await self.llm.text([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.1)).strip()
        paths["final_report"].write_text(report + "\n", encoding="utf-8")

        def _extract_open_questions_from_markdown(md: str) -> List[str]:
            bullets: List[str] = []
            non_bullets: List[str] = []
            in_open = False
            for raw in (md.splitlines() if md else []):
                line = raw.strip()
                if not line:
                    continue
                if line.lower().startswith("#") and "open questions" in line.lower():
                    in_open = True
                    continue
                if in_open and line.startswith("#"):
                    break
                if not in_open:
                    continue
                if line.startswith("-"):
                    q = line[1:].lstrip()
                    if q:
                        bullets.append(q)
                elif line.startswith("*"):
                    q = line[1:].lstrip()
                    if q:
                        bullets.append(q)
                else:
                    # Some model outputs use a plain sentence instead of bullets.
                    non_bullets.append(line)

            # Prefer bullets if present; otherwise return the (often single-line) open question text.
            return bullets if bullets else non_bullets

        # Open questions: best-effort extraction from final report (most structured), then brief, then chunk DB.
        open_q: List[str] = _extract_open_questions_from_markdown(report)
        if not open_q:
            open_q = _extract_open_questions_from_markdown(brief)

        # Always include explicitly stated questions from chunk_summaries.json (if any),
        # since those are the most grounded.
        chunk_open_q: List[str] = []
        if paths["chunk_summaries"].exists():
            try:
                chunk_db = json.loads(paths["chunk_summaries"].read_text(encoding="utf-8"))
                for _file_key, v in (chunk_db or {}).items():
                    for c in (v.get("chunks") or []) if isinstance(v, dict) else []:
                        for q in c.get("open_questions") or []:
                            qs = str(q).strip()
                            if qs:
                                chunk_open_q.append(qs)
            except Exception:
                chunk_open_q = []

        if chunk_open_q:
            open_q.extend(chunk_open_q)

        # De-duplicate while preserving order.
        if open_q:
            open_q = list(dict.fromkeys(open_q))[:50]
            paths["open_questions"].write_text("\n".join(f"- {q}" for q in open_q) + "\n", encoding="utf-8")
        else:
            paths["open_questions"].write_text("- (none captured)\n", encoding="utf-8")

        return {"ok": True, "final_report": str(paths['final_report']), "open_questions": str(paths['open_questions'])}

    async def run_step(self, task_id: str, step: Dict[str, Any], inputs: ResearchInputs) -> Dict[str, Any]:
        """
        Execute a single research step. Returns a dict with ok/error and optional
        artifact metadata.
        """
        step_type = step.get("step_type") or "generic"
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)

        if step_type == "discover_files":
            idx = self._discover_files(inputs)
            if not idx.get("ok"):
                return {"ok": False, "error": idx.get("error", "discover_failed")}
            paths["file_index"].write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"ok": True, "artifact_path": str(paths["file_index"]), "files_count": len(idx.get("files") or [])}

        if step_type == "expand_file_steps":
            # This step is handled by the TaskEngine (it expands steps after discovery).
            return {"ok": True, "note": "expanded_by_engine"}

        if step_type.startswith("summarize_file:"):
            # Ensure file_index exists.
            if not paths["file_index"].exists():
                return {"ok": False, "error": "missing file_index.json (run discover_files first)"}
            idx = json.loads(paths["file_index"].read_text(encoding="utf-8"))
            target = step_type[len("summarize_file:") :].strip()
            entry = None
            for f in idx.get("files") or []:
                if f.get("path") == target:
                    entry = f
                    break
            if not entry:
                return {"ok": False, "error": f"file not found in index: {target}"}
            return await self.summarize_file(task_id, inputs, entry)

        if step_type == "synthesis":
            if not paths["file_index"].exists():
                return {"ok": False, "error": "missing file_index.json (run discover_files first)"}
            idx = json.loads(paths["file_index"].read_text(encoding="utf-8"))
            return await self.synthesize(task_id, inputs, idx)

        if step_type == "final_report":
            return await self.final_report(task_id, inputs)

        return {"ok": True, "note": f"noop:{step_type}"}
