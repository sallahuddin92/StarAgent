from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from urllib.parse import urlparse
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
    ".jsonl",
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


_WS_BYTES = b" \t\r\n"


def _peek_first_non_ws_byte(path: Path, *, max_bytes: int = 32 * 1024) -> Optional[int]:
    try:
        with path.open("rb") as f:
            raw = f.read(max_bytes)
    except Exception:
        return None
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for b in raw:
        if b in _WS_BYTES:
            continue
        return int(b)
    return None


def _extract_top_level_json_array_elements(
    path: Path,
    *,
    max_elements: int = 50,
    max_scan_bytes: int = 12 * 1024 * 1024,
    chunk_size: int = 64 * 1024,
) -> Dict[str, Any]:
    """
    Incrementally extract the first N complete JSON elements from a top-level JSON array.

    This is robust against truncation inside strings because it does not rely on
    sampling + json.loads on an arbitrary byte slice.

    Returns:
      {
        "ok": bool,
        "elements": [Any],
        "elements_raw_bytes_total": int,
        "bytes_scanned": int,
        "error": Optional[str],
      }
    """
    elements: List[Any] = []
    bytes_scanned = 0
    raw_bytes_total = 0

    # State
    in_string = False
    escape = False
    depth = 0  # nested {} and [] depth within the current element
    collecting = False
    elem_buf = bytearray()
    saw_array_open = False

    def _finalize_element() -> Optional[str]:
        nonlocal elem_buf, raw_bytes_total
        raw = bytes(elem_buf).strip()
        elem_buf = bytearray()
        if not raw:
            return None
        raw_bytes_total += len(raw)
        try:
            txt = raw.decode("utf-8")
        except Exception:
            # If decoding fails, we can't reliably parse.
            txt = raw.decode("utf-8", errors="replace")
        try:
            elements.append(json.loads(txt))
            return None
        except Exception as e:
            return f"json_parse_error: {e}"

    try:
        with path.open("rb") as f:
            # Seek to the first '['.
            while not saw_array_open:
                chunk = f.read(chunk_size)
                if not chunk:
                    return {"ok": False, "elements": [], "elements_raw_bytes_total": 0, "bytes_scanned": bytes_scanned, "error": "empty_or_no_array"}
                bytes_scanned += len(chunk)
                if bytes_scanned > max_scan_bytes:
                    return {"ok": False, "elements": [], "elements_raw_bytes_total": 0, "bytes_scanned": bytes_scanned, "error": "scan_limit_before_array_open"}
                i = 0
                # Skip UTF-8 BOM if present at file start.
                if bytes_scanned == len(chunk) and chunk.startswith(b"\xef\xbb\xbf"):
                    i = 3
                while i < len(chunk):
                    b = chunk[i]
                    if b in _WS_BYTES:
                        i += 1
                        continue
                    if b == ord("["):
                        saw_array_open = True
                        i += 1
                        # Process remainder of this chunk as array content.
                        chunk = chunk[i:]
                        i = 0
                        break
                    return {"ok": False, "elements": [], "elements_raw_bytes_total": 0, "bytes_scanned": bytes_scanned, "error": "not_a_top_level_array"}
                if not saw_array_open:
                    continue

                # Fallthrough to process `chunk` remainder after '['.
                # We break out by setting saw_array_open and rewriting chunk.
                # Process remainder using shared loop below.
                # Use a sentinel to indicate we already have a remainder chunk to process.
                pending_chunk = chunk
                break
            else:
                pending_chunk = b""

            # Parse array elements.
            while True:
                if not pending_chunk:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    pending_chunk = chunk
                    bytes_scanned += len(pending_chunk)
                if bytes_scanned > max_scan_bytes:
                    return {"ok": True, "elements": elements, "elements_raw_bytes_total": raw_bytes_total, "bytes_scanned": bytes_scanned, "error": "scan_limit_reached"}

                i = 0
                while i < len(pending_chunk):
                    b = pending_chunk[i]

                    if not collecting:
                        # Skip whitespace and commas between elements.
                        if b in _WS_BYTES or b == ord(","):
                            i += 1
                            continue
                        if b == ord("]"):
                            return {"ok": True, "elements": elements, "elements_raw_bytes_total": raw_bytes_total, "bytes_scanned": bytes_scanned, "error": None}
                        # Start a new element.
                        collecting = True
                        in_string = False
                        escape = False
                        depth = 0
                        elem_buf = bytearray()
                        # Do not advance i; the element handler below consumes this byte.
                        continue

                    # collecting == True
                    if not in_string and depth == 0 and (b == ord(",") or b == ord("]")):
                        # End of element (delimiter not included).
                        err = _finalize_element()
                        if err:
                            return {"ok": False, "elements": elements, "elements_raw_bytes_total": raw_bytes_total, "bytes_scanned": bytes_scanned, "error": err}
                        collecting = False
                        if b == ord("]"):
                            return {"ok": True, "elements": elements, "elements_raw_bytes_total": raw_bytes_total, "bytes_scanned": bytes_scanned, "error": None}
                        i += 1
                        if len(elements) >= max_elements:
                            return {"ok": True, "elements": elements, "elements_raw_bytes_total": raw_bytes_total, "bytes_scanned": bytes_scanned, "error": None}
                        continue

                    # Regular byte: append and update state.
                    elem_buf.append(b)

                    if in_string:
                        if escape:
                            escape = False
                        elif b == ord("\\"):
                            escape = True
                        elif b == ord('"'):
                            in_string = False
                    else:
                        if b == ord('"'):
                            in_string = True
                        elif b == ord("{") or b == ord("["):
                            depth += 1
                        elif b == ord("}") or b == ord("]"):
                            # Only decrement if we are inside a nested structure. Top-level ']' delimiter is handled above.
                            if depth > 0:
                                depth -= 1

                    i += 1

                # Finished pending_chunk
                pending_chunk = b""

    except Exception as e:
        return {"ok": False, "elements": elements, "elements_raw_bytes_total": raw_bytes_total, "bytes_scanned": bytes_scanned, "error": f"extract_error: {e}"}

    if collecting:
        # File ended unexpectedly while collecting.
        return {"ok": False, "elements": elements, "elements_raw_bytes_total": raw_bytes_total, "bytes_scanned": bytes_scanned, "error": "unexpected_eof_in_element"}
    return {"ok": True, "elements": elements, "elements_raw_bytes_total": raw_bytes_total, "bytes_scanned": bytes_scanned, "error": None}


def _deterministic_dataset_batch_summary(records: List[Any]) -> Dict[str, Any]:
    """
    Conservative, non-LLM fallback summary for dataset batches.
    This must not fabricate evidence; it only computes facts from the provided records.
    """
    if not records:
        return {"summary": "No records provided.", "key_patterns": [], "anomalies": [], "open_questions": []}

    # If records are dict-like, extract common keys and a couple of safe structural facts.
    dicts = [r for r in records if isinstance(r, dict)]
    if not dicts:
        # Primitive arrays: summarize types.
        types = {}
        for r in records:
            t = type(r).__name__
            types[t] = types.get(t, 0) + 1
        parts = [f"{k}={v}" for k, v in sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))]
        return {
            "summary": "Batch contains non-object JSON values: " + ", ".join(parts[:8]) + ".",
            "key_patterns": [],
            "anomalies": [],
            "open_questions": [],
        }

    # Key presence
    key_counts: Dict[str, int] = {}
    for d in dicts:
        for k in d.keys():
            key_counts[str(k)] = key_counts.get(str(k), 0) + 1
    total = len(dicts)
    common_keys = [k for k, c in sorted(key_counts.items(), key=lambda kv: (-kv[1], kv[0])) if c >= max(1, int(total * 0.8))]
    top_keys = [k for k, _ in sorted(key_counts.items(), key=lambda kv: (-kv[1], kv[0]))][:10]

    # URL domain pattern if a url-like key exists.
    domains: Dict[str, int] = {}
    url_key = None
    for cand in ("url", "link", "href", "source_url"):
        if cand in key_counts:
            url_key = cand
            break
    if url_key:
        for d in dicts:
            v = d.get(url_key)
            if not isinstance(v, str) or not v:
                continue
            try:
                dom = urlparse(v).netloc.lower()
            except Exception:
                dom = ""
            if dom:
                domains[dom] = domains.get(dom, 0) + 1
    top_domains = [k for k, _ in sorted(domains.items(), key=lambda kv: (-kv[1], kv[0]))][:4]

    # Text length stats for likely body/content fields.
    body_key = None
    for cand in ("body", "content", "text", "description"):
        if cand in key_counts:
            body_key = cand
            break
    lengths = []
    if body_key:
        for d in dicts:
            v = d.get(body_key)
            if isinstance(v, str):
                lengths.append(len(v))
    length_note = ""
    if lengths:
        mn = min(lengths)
        mx = max(lengths)
        avg = int(sum(lengths) / max(1, len(lengths)))
        length_note = f" '{body_key}' length avg={avg}, min={mn}, max={mx} (chars)."

    patterns: List[str] = []
    if common_keys:
        patterns.append("Common keys (>=80% of records): " + ", ".join(common_keys[:10]))
    else:
        patterns.append("Top keys: " + ", ".join(top_keys[:10]))
    if top_domains:
        patterns.append("Top URL domains: " + ", ".join(top_domains))
    if length_note:
        patterns.append("Text field stats:" + length_note)

    # A couple of representative examples (title/url pairs) for grounding.
    examples: List[str] = []
    title_key = "title" if "title" in key_counts else None
    if title_key and url_key:
        for d in dicts[:6]:
            t = d.get(title_key)
            u = d.get(url_key)
            if isinstance(t, str) and isinstance(u, str) and t and u:
                examples.append(f"{t} ({u})")
            if len(examples) >= 3:
                break

    summary = "Records appear to be JSON objects."
    if common_keys:
        summary += f" Common keys include: {', '.join(common_keys[:6])}."
    else:
        summary += f" Top keys include: {', '.join(top_keys[:6])}."
    if top_domains:
        summary += f" URLs commonly point to: {', '.join(top_domains[:2])}."
    if length_note:
        summary += " " + length_note.strip()
    if examples:
        summary += " Examples: " + "; ".join(examples[:3]) + "."

    return {"summary": summary.strip(), "key_patterns": patterns[:6], "anomalies": [], "open_questions": []}


def _deterministic_dataset_final_report(
    *,
    question: str,
    dataset_facts: Dict[str, Any],
    batch_summaries_text: str,
    sample_examples: str,
    themes_obj: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Conservative fallback final report for dataset mode.
    This is used when the small local model produces an ungrounded/generic report.
    """
    common_keys = dataset_facts.get("common_keys_80pct") or dataset_facts.get("top_keys") or []
    domains = dataset_facts.get("top_url_domains") or []
    est = dataset_facts.get("estimated_total_records")
    size = dataset_facts.get("size_bytes")
    kind = dataset_facts.get("json_kind")
    confidence = dataset_facts.get("confidence")
    dup = (dataset_facts.get("duplicate_analysis") or {}) if isinstance(dataset_facts.get("duplicate_analysis"), dict) else {}
    dup_ratio = dup.get("duplicate_ratio")
    missing = (dataset_facts.get("missing_fields_sample") or {}) if isinstance(dataset_facts.get("missing_fields_sample"), dict) else {}
    years = dataset_facts.get("top_years_in_urls") or []
    coverage = (dataset_facts.get("coverage") or {}) if isinstance(dataset_facts.get("coverage"), dict) else {}
    cov_ratio = coverage.get("coverage_ratio")

    # Summarize batch summaries in a compact, readable way.
    batch_lines: List[str] = []
    try:
        bs = json.loads(batch_summaries_text) if batch_summaries_text else {}
        if isinstance(bs, dict):
            for k in sorted(bs.keys(), key=lambda x: int(x) if str(x).isdigit() else str(x)):
                v = bs.get(k) or {}
                s = str(v.get("summary") or "").strip()
                if s:
                    batch_lines.append(f"- Batch {k}: {s}")
    except Exception:
        batch_lines = []

    lines: List[str] = []
    lines.append("# Dataset Analysis Report")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append(f"- Question: {question}")
    if kind:
        lines.append(f"- Detected JSON kind: {kind}")
    if isinstance(size, int):
        lines.append(f"- File size: {size} bytes")
    if est is not None:
        lines.append(f"- Estimated total records (rough): {est}")
    if confidence:
        lines.append(f"- Confidence: {confidence}")
    if isinstance(cov_ratio, (int, float)):
        lines.append(f"- Coverage ratio (sample/estimated): {float(cov_ratio):.3f}")
    if isinstance(dup_ratio, (int, float)):
        lines.append(f"- Duplicate ratio (sample URLs): {float(dup_ratio):.3f}")
    lines.append("- This report is sample-based (first N records extracted) for responsiveness.")
    lines.append("")
    lines.append("## 2. Key Themes")
    lines.append("")
    themes = (themes_obj or {}).get("themes") if isinstance(themes_obj, dict) else None
    if isinstance(themes, list) and themes:
        for t in themes[:8]:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").strip()
            if not name:
                continue
            desc = str(t.get("description") or "").strip()
            pct = t.get("estimated_percentage")
            pct_s = f"{int(pct)}%" if isinstance(pct, (int, float)) else "0%"
            lines.append(f"- **{name}** ({pct_s}): {desc}" if desc else f"- **{name}** ({pct_s})")
            ex = t.get("examples") or []
            if ex:
                for e in ex[:3]:
                    title = str((e or {}).get("title") or "").strip()
                    url = str((e or {}).get("url") or "").strip()
                    if title and url:
                        lines.append(f"  - {title} ({url})")
    else:
        lines.append("- (themes unavailable; see dataset_theme_extraction)")
    lines.append("")
    lines.append("## 3. Dataset Structure")
    lines.append("")
    if common_keys:
        lines.append("- Schema (observed keys): " + ", ".join([str(k) for k in common_keys[:12]]))
    if batch_lines:
        lines.append("- Batch observations (sample):")
        lines.extend(batch_lines[:6])
    lines.append("")
    lines.append("## 4. Data Quality")
    lines.append("")
    if isinstance(dup_ratio, (int, float)):
        lines.append(f"- Duplicate ratio (sample URLs): {float(dup_ratio):.3f}")
    if missing:
        # Show up to 6 keys with the most missing values.
        top_missing = sorted(missing.items(), key=lambda kv: (-int(kv[1] or 0), str(kv[0])))[:6]
        parts = [f"{k}:{int(v)}" for k, v in top_missing if int(v or 0) > 0]
        if parts:
            lines.append("- Missing fields in sample (count missing/empty): " + ", ".join(parts))
    if domains:
        lines.append("- Top URL domains (sample): " + ", ".join([str(d) for d in domains[:6]]))
    lines.append("")
    lines.append("## 5. Observations")
    lines.append("")
    if domains:
        lines.append("- Many records appear to share the same source domain(s) in the sampled URLs.")
    if years:
        lines.append("- Temporal hints from URL paths (sample): " + ", ".join([str(y) for y in years[:6]]))
    lines.append("- Sampling-based analysis; full dataset may include additional fields/topics.")
    lines.append("")
    lines.append("## 6. Recommendations")
    lines.append("")
    lines.append("- Normalize text fields (trim whitespace/HTML, unify encoding) before downstream analysis.")
    lines.append("- Consider URL-based de-duplication (and optionally title/body similarity) for cleaner analytics.")
    lines.append("- If you need full-coverage analysis (not sample-based), add a full-scan pass or increase sampling/batch limits.")
    lines.append("")
    if sample_examples:
        lines.append("## Sample Examples")
        lines.append("")
        lines.append(sample_examples.strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


@dataclass
class ResearchInputs:
    root_path: str
    files: Optional[List[str]] = None
    question: Optional[str] = None
    mode: str = "research"  # summary | research | comparison
    # Intake-driven hints (from TaskEngine adaptive intake).
    input_type: Optional[str] = None  # e.g. "docs_folder" | "json_dataset"
    dataset_path: Optional[str] = None  # absolute path to dominant dataset file (if any)


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
        #
        # Adaptive routing: if intake indicates a JSON dataset, use dataset mode rather than
        # treating the file as generic docs.
        if (inputs.input_type or "").strip().lower() == "json_dataset":
            ds = inputs.dataset_path or inputs.root_path
            return [
                {"step_type": "dataset_profile", "instruction": f"Profile dataset JSON: {ds}"},
                {"step_type": "expand_dataset_steps", "instruction": "Expand per-batch dataset summarization steps"},
                {"step_type": "dataset_synthesis", "instruction": "Synthesize dataset batch summaries into dataset_brief.md"},
                {"step_type": "dataset_theme_extraction", "instruction": "Extract dominant themes from dataset batch summaries"},
                {"step_type": "final_report", "instruction": "Write final_report.md and open_questions.md"},
            ]

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
            # Dataset mode artifacts
            "dataset_profile": d / "dataset_profile.json",
            "dataset_facts": d / "dataset_facts.json",
            "sample_records": d / "sample_records.json",
            "batch_summaries": d / "batch_summaries.json",
            "dataset_brief": d / "dataset_brief.md",
            "themes_json": d / "themes.json",
            "themes_md": d / "themes.md",
            "dataset_theme_report": d / "dataset_theme_report.md",
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
                files_out.append(
                    {
                        "path": str(p.relative_to(root)) if root in p.parents else str(p),
                        "abs_path": str(p),
                        "size_bytes": int(p.stat().st_size),
                        # Avoid reading very large files during discovery; hash only small files.
                        "sha256": _sha256_bytes(p.read_bytes()) if int(p.stat().st_size) <= 5_000_000 else None,
                    }
                )
            except Exception as e:
                skipped.append({"path": str(p), "reason": f"read_error:{e}"})

        return {"ok": True, "root": str(root), "files": files_out, "skipped": skipped}

    def _dataset_file(self, inputs: ResearchInputs) -> Path:
        # Prefer intake-provided dataset_path (absolute path).
        if inputs.dataset_path:
            return Path(inputs.dataset_path).expanduser().resolve()
        # If root_path is a file, treat it as the dataset file.
        rp = Path(inputs.root_path).expanduser()
        try:
            rp = rp.resolve()
        except Exception:
            pass
        if rp.exists() and rp.is_file():
            return rp
        # Otherwise, fall back to the largest .json under root_path.
        root = rp
        if root.exists() and root.is_dir():
            largest: Optional[Path] = None
            largest_size = -1
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in (".json", ".jsonl", ".ndjson"):
                    continue
                try:
                    sz = int(p.stat().st_size)
                except Exception:
                    continue
                if sz > largest_size:
                    largest = p
                    largest_size = sz
            if largest:
                return largest.expanduser().resolve()
        return rp

    def _probe_json_kind_and_samples(self, ds_path: Path) -> Dict[str, Any]:
        """
        Profile a JSON dataset safely without full-file reads.
        Produces:
          - json_kind: object | array | ndjson | unknown
          - sample_records: list[dict|list|str|int|float|bool|None]
        """
        st = ds_path.stat()
        size = int(st.st_size)
        # For arrays, we do incremental extraction instead of parsing a truncated slice.
        first = _peek_first_non_ws_byte(ds_path)
        sample_bytes = min(1_500_000, max(128_000, int(min(size, 1_500_000))))
        text = ""
        if first is not None and first != ord("["):
            with ds_path.open("rb") as f:
                raw = f.read(sample_bytes)
            text = raw.decode("utf-8", errors="replace").lstrip()

        json_kind = "unknown"
        sample_records: List[Any] = []
        parse_error: Optional[str] = None
        bytes_scanned: Optional[int] = None
        elements_raw_bytes_total: Optional[int] = None
        estimated_total_records: Optional[int] = None

        if first == ord("["):
            json_kind = "array"
            # Scan cap: keep bounded; 8MB+ arrays should still yield early records.
            max_scan = min(max(2_000_000, size), 12 * 1024 * 1024)
            res = _extract_top_level_json_array_elements(ds_path, max_elements=60, max_scan_bytes=max_scan)
            bytes_scanned = int(res.get("bytes_scanned") or 0)
            elements_raw_bytes_total = int(res.get("elements_raw_bytes_total") or 0)
            sample_records = list(res.get("elements") or [])[:50]
            parse_error = res.get("error")
            # If we extracted at least one element, consider this successful even if we hit scan limit.
            if sample_records and parse_error in ("scan_limit_reached", None):
                parse_error = None
            # Rough record-count estimate based on average element size (best-effort).
            if sample_records and elements_raw_bytes_total and len(sample_records) > 0:
                avg = max(1, int(elements_raw_bytes_total / len(sample_records)))
                estimated_total_records = int(size / avg)

        if json_kind != "array":
            # Try strict parse of the sample; works for small JSON objects/arrays.
            try:
                obj = json.loads(text)
                if isinstance(obj, list):
                    json_kind = "array"
                    sample_records = obj[:50]
                elif isinstance(obj, dict):
                    json_kind = "object"
                    # Represent object sample as a single record with top-level keys.
                    sample_records = [obj]
                else:
                    json_kind = "unknown"
                    sample_records = [obj]
            except Exception as e:
                parse_error = str(e)
                # NDJSON probe: parse per-line JSON objects.
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                ok = 0
                recs: List[Any] = []
                for ln in lines[:150]:
                    try:
                        rec = json.loads(ln)
                        ok += 1
                        recs.append(rec)
                    except Exception:
                        continue
                    if len(recs) >= 50:
                        break
                if ok >= 10 and len(recs) >= 10:
                    json_kind = "ndjson"
                    sample_records = recs
                    parse_error = None
                else:
                    # Heuristic: if it starts with [, it's likely an array too large to parse from sample.
                    if text.startswith("["):
                        json_kind = "array"
                    elif text.startswith("{"):
                        json_kind = "object_or_ndjson"

        return {
            "abs_path": str(ds_path),
            "size_bytes": size,
            "sample_bytes": sample_bytes,
            "bytes_scanned": bytes_scanned,
            "elements_raw_bytes_total": elements_raw_bytes_total,
            "json_kind": json_kind,
            "sample_records": sample_records,
            "sample_parse_error": parse_error,
            "estimated_total_records": estimated_total_records,
        }

    async def dataset_profile(self, task_id: str, inputs: ResearchInputs) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)

        ds_path = self._dataset_file(inputs)
        if not ds_path.exists() or not ds_path.is_file():
            return {"ok": False, "error": f"dataset file not found: {ds_path}"}

        prof = self._probe_json_kind_and_samples(ds_path)
        # Plan bounded batches over the available samples (first-pass, sampling-based).
        recs = prof.get("sample_records") or []
        batch_size = 20
        max_batches = 15
        planned = []
        for i in range(0, min(len(recs), batch_size * max_batches), batch_size):
            planned.append(
                {
                    "batch_index": int(i // batch_size),
                    "start": int(i),
                    "end": int(min(i + batch_size, len(recs))),
                }
            )
        prof["planned_batches"] = planned
        prof["sampling_note"] = (
            "This dataset mode uses a bounded sample-based pass (first N records from the file) "
            "to stay responsive on small local models."
        )

        # Duplicate analysis (URL-based) over sampled records only.
        urls: List[str] = []
        for r in recs:
            if isinstance(r, dict):
                u = r.get("url")
                if isinstance(u, str) and u.strip():
                    urls.append(u.strip())
        total_urls = int(len(urls))
        unique_urls = int(len(set(urls)))
        duplicate_ratio = float(1.0 - (unique_urls / total_urls)) if total_urls > 0 else 0.0
        prof["duplicate_analysis"] = {
            "total_urls": total_urls,
            "unique_urls": unique_urls,
            "duplicate_ratio": duplicate_ratio,
        }

        # Coverage + confidence (sampling-based).
        sampled_records = int(len(recs))
        est_total = prof.get("estimated_total_records")
        coverage_ratio: Optional[float] = None
        if isinstance(est_total, int) and est_total > 0:
            coverage_ratio = float(sampled_records / float(est_total))
        prof["coverage"] = {
            "sampled_records": sampled_records,
            "estimated_total_records": est_total if isinstance(est_total, int) else None,
            "coverage_ratio": coverage_ratio,
        }
        confidence = "low"
        if coverage_ratio is not None:
            if coverage_ratio < 0.05:
                confidence = "low"
            elif coverage_ratio <= 0.20:
                confidence = "medium"
            else:
                confidence = "high"
        prof["confidence"] = confidence

        # Persist artifacts.
        paths["dataset_profile"].write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["sample_records"].write_text(json.dumps({"records": recs[:300]}, ensure_ascii=False, indent=2), encoding="utf-8")
        # A compact, operator-friendly facts summary for UI/CLI surfaces.
        facts = {
            "dataset_path": prof.get("abs_path"),
            "json_kind": prof.get("json_kind"),
            "size_bytes": prof.get("size_bytes"),
            "sampled_records": sampled_records,
            "estimated_total_records": est_total if isinstance(est_total, int) else None,
            "planned_batches": int(len(planned)),
            "duplicate_ratio": duplicate_ratio,
            "coverage_ratio": coverage_ratio,
            "confidence": confidence,
        }
        paths["dataset_facts"].write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "artifact_path": str(paths["dataset_profile"]), "planned_batches": len(planned)}

    def _format_themes_md(self, themes: List[Dict[str, Any]]) -> str:
        lines: List[str] = ["# Dataset Themes", ""]
        for t in themes:
            name = str(t.get("name") or "").strip() or "(unnamed)"
            desc = str(t.get("description") or "").strip()
            pct = t.get("estimated_percentage")
            pct_s = f" ({int(pct)}%)" if isinstance(pct, (int, float)) else ""
            lines.append(f"## {name}{pct_s}")
            lines.append("")
            if desc:
                lines.append(desc)
                lines.append("")
            examples = t.get("examples") or []
            if examples:
                lines.append("Examples:")
                for e in examples[:3]:
                    title = str((e or {}).get("title") or "").strip()
                    url = str((e or {}).get("url") or "").strip()
                    if title and url:
                        lines.append(f"- {title} ({url})")
                lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _deterministic_themes_from_examples(self, examples: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        stop = {
            "dan",
            "yang",
            "di",
            "ke",
            "untuk",
            "dengan",
            "pada",
            "dari",
            "the",
            "a",
            "an",
            "of",
            "to",
            "in",
            "on",
            "for",
            "with",
        }
        counts: Dict[str, int] = {}
        words_by_ex: List[List[str]] = []
        for ex in examples:
            title = (ex.get("title") or "").lower()
            toks = [w for w in re.findall(r"[a-z0-9']+", title) if w and w not in stop and len(w) >= 3]
            words_by_ex.append(toks)
            for w in set(toks):
                counts[w] = counts.get(w, 0) + 1

        top = [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:10]
        themes: List[Dict[str, Any]] = []
        used_idx: set[int] = set()
        for w in top[:7]:
            idxs = [i for i, toks in enumerate(words_by_ex) if w in toks]
            if len(idxs) < 2:
                continue
            exs: List[Dict[str, str]] = []
            for i in idxs:
                if i in used_idx:
                    continue
                used_idx.add(i)
                exs.append({"title": examples[i]["title"], "url": examples[i]["url"]})
                if len(exs) >= 3:
                    break
            if len(exs) >= 2:
                pct = int(round(100.0 * (len(idxs) / max(1, len(examples)))))
                themes.append(
                    {
                        "name": w.title(),
                        "description": f"Articles whose titles frequently mention '{w}'.",
                        "estimated_percentage": pct,
                        "examples": exs,
                    }
                )
            if len(themes) >= 6:
                break

        if not themes:
            themes = [
                {
                    "name": "Articles",
                    "description": "Dataset appears to be a collection of article-like records.",
                    "estimated_percentage": 0,
                    "examples": examples[:3],
                }
            ]
        return themes[:8]

    def _semanticize_theme_name(self, raw_name: str, examples: List[Dict[str, str]]) -> str:
        name = (raw_name or "").strip()
        nl = name.lower()
        titles = [(e.get("title") or "").lower() for e in (examples or []) if isinstance(e, dict)]

        # Year-only "themes" are usually temporal hints, not topics.
        if re.fullmatch(r"(19|20)\d{2}", nl):
            return f"Time References ({name})"

        # Strong keyword signals: respect the original label first.
        if nl in {"upsi"}:
            return "UPSI Campus News"
        if nl in {"kerjasama"}:
            return "Partnerships and Collaboration"
        if nl in {"pelajar"}:
            return "Student Affairs"

        # Verb-like names: replace with a topic bucket inferred from example titles.
        if nl in {"anjur", "terima", "jalin", "bina", "lancar"}:
            if any(x in t for t in titles for x in ("persidangan", "forum", "seminar", "konvensyen")):
                return "Conferences and Forums"
            if any(x in t for t in titles for x in ("biasiswa", "anugerah", "tauliah", "sumbangan")):
                return "Awards and Scholarships"
            if any(x in t for t in titles for x in ("kunjungan", "lawatan", "delegasi")):
                return "Visits and Delegations"
            if any(x in t for t in titles for x in ("hari", "festival", "program", "anjur")):
                return "Events and Programs"
            return "Announcements and Updates"

        # Common keywords we see in campus-news datasets.
        # Prefer specific topic buckets before falling back to a generic "UPSI" catch-all,
        # since many titles contain the org name.
        if any(x in t for t in titles for x in ("kerjasama", "jalin", "memorandum", "kolaborasi")):
            return "Partnerships and Collaboration"
        if any(x in t for t in titles for x in ("pelajar", "majl", "mpp", "kadet", "biasiswa")):
            return "Student Affairs"
        if any(x in t for t in titles for x in ("sukan", "berbasikal", "arena", "futsal", "bola")):
            return "Sports and Health"
        if nl in {"upsi"} or any("upsi" in t for t in titles):
            return "UPSI Campus News"

        return name

    def _semantic_theme_description(self, name: str) -> str:
        n = (name or "").strip()
        if n.startswith("Time References ("):
            return "Titles that include explicit year/date references (a temporal signal, not necessarily a topical cluster)."
        mapping = {
            "UPSI Campus News": "Campus news, events, and announcements mentioning UPSI.",
            "Partnerships and Collaboration": "Partnerships, collaborations, MoUs, and institutional relationship news.",
            "Student Affairs": "Student activities, representation, scholarships, and student-facing programs.",
            "Awards and Scholarships": "Awards, scholarships, recognitions, contributions, and related updates.",
            "Conferences and Forums": "Conferences, forums, talks, symposiums, and academic events.",
            "Sports and Health": "Sports events, health/fitness activities, and athletics-related updates.",
            "Visits and Delegations": "Visits, delegations, official meetings, and external engagement.",
            "Events and Programs": "Organized events, campaigns, festivals, and program announcements.",
            "Announcements and Updates": "General announcements and institutional updates.",
        }
        return mapping.get(n, "")

    def _normalize_theme_percentages(self, themes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        weights: List[int] = []
        for t in themes:
            pct = t.get("estimated_percentage")
            if isinstance(pct, (int, float)):
                weights.append(int(max(0, pct)))
            else:
                weights.append(int(len(t.get("examples") or [])))

        total = sum(weights)
        if total <= 0:
            return themes

        # Only normalize if the distribution is clearly off.
        if total < 70 or total > 110:
            raw = [int(round(w * 100.0 / total)) for w in weights]
            # Force sum to 100 by adjusting the largest bucket.
            s = sum(raw)
            if s != 100:
                i = max(range(len(raw)), key=lambda k: raw[k]) if raw else 0
                raw[i] = max(0, raw[i] + (100 - s))
            for t, p in zip(themes, raw):
                t["estimated_percentage"] = int(max(0, min(100, p)))
        return themes

    def _postprocess_themes(self, themes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for t in themes:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").strip()
            if not name:
                continue
            examples = t.get("examples") or []
            if not isinstance(examples, list):
                examples = []
            new_name = self._semanticize_theme_name(name, [e for e in examples if isinstance(e, dict)])
            t["name"] = new_name
            # If we've renamed the theme, override the description to match the new semantic label.
            if new_name != name:
                desc = self._semantic_theme_description(new_name)
                if desc:
                    t["description"] = desc
            out.append(t)

        # Collapse exact-duplicate names by combining examples (keep first description).
        merged: Dict[str, Dict[str, Any]] = {}
        for t in out:
            key = str(t.get("name") or "").strip()
            if not key:
                continue
            if key not in merged:
                merged[key] = {**t, "examples": list(t.get("examples") or [])}
            else:
                ex = merged[key].get("examples") or []
                for e in (t.get("examples") or []):
                    if e not in ex:
                        ex.append(e)
                merged[key]["examples"] = ex[:3]
                merged[key]["estimated_percentage"] = int(max(int(merged[key].get("estimated_percentage") or 0), int(t.get("estimated_percentage") or 0)))

        out2 = list(merged.values())
        out2 = self._normalize_theme_percentages(out2)

        # Order by percentage desc, then name.
        out2.sort(key=lambda t: (-int(t.get("estimated_percentage") or 0), str(t.get("name") or "")))
        return out2[:8]

    async def dataset_theme_extraction(self, task_id: str, inputs: ResearchInputs) -> Dict[str, Any]:
        """
        Input:
        - batch_summaries.json
        - dataset_brief.md
        Output:
        - themes.json
        - themes.md
        """
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        if not paths["batch_summaries"].exists():
            return {"ok": False, "error": "missing batch_summaries.json (run dataset_batch steps first)"}
        if not paths["dataset_brief"].exists():
            return {"ok": False, "error": "missing dataset_brief.md (run dataset_synthesis first)"}

        # If themes were already generated in a prior run, avoid re-calling the LLM.
        # Only (re)generate the consolidated operator report if missing.
        if paths["themes_json"].exists() and paths["themes_md"].exists():
            try:
                obj0 = json.loads(paths["themes_json"].read_text(encoding="utf-8"))
                themes0 = obj0.get("themes") if isinstance(obj0, dict) else None
                if isinstance(themes0, list):
                    themes0 = themes0[:8]
                else:
                    themes0 = []
            except Exception:
                themes0 = []
            if themes0 and not paths["dataset_theme_report"].exists():
                # Reuse the existing themes to write the operator-friendly report.
                facts = {}
                try:
                    if paths["dataset_facts"].exists():
                        facts = json.loads(paths["dataset_facts"].read_text(encoding="utf-8"))
                except Exception:
                    facts = {}

                header: List[str] = ["# Dataset Theme Report", "", "## Dataset Facts", ""]
                if facts.get("dataset_path"):
                    header.append(f"- Dataset: {facts.get('dataset_path')}")
                if facts.get("json_kind"):
                    header.append(f"- JSON kind: {facts.get('json_kind')}")
                if isinstance(facts.get("size_bytes"), int):
                    header.append(f"- Size: {facts.get('size_bytes')} bytes")
                if facts.get("estimated_total_records") is not None:
                    header.append(f"- Estimated total records: {facts.get('estimated_total_records')}")
                if facts.get("sampled_records") is not None:
                    header.append(f"- Sampled records: {facts.get('sampled_records')}")
                if facts.get("planned_batches") is not None:
                    header.append(f"- Planned batches: {facts.get('planned_batches')}")
                if facts.get("duplicate_ratio") is not None:
                    try:
                        header.append(f"- Duplicate ratio (URL-based): {float(facts.get('duplicate_ratio')):.3f}")
                    except Exception:
                        header.append(f"- Duplicate ratio (URL-based): {facts.get('duplicate_ratio')}")
                if facts.get("coverage_ratio") is not None:
                    try:
                        header.append(f"- Coverage ratio (sample/estimate): {float(facts.get('coverage_ratio')):.3f}")
                    except Exception:
                        header.append(f"- Coverage ratio (sample/estimate): {facts.get('coverage_ratio')}")
                if facts.get("confidence"):
                    header.append(f"- Confidence: {facts.get('confidence')}")
                header.append("")
                header.append("## Themes")
                header.append("")
                header.append(self._format_themes_md(themes0).strip().replace("# Dataset Themes", "").strip())
                header.append("")
                paths["dataset_theme_report"].write_text("\n".join(header).strip() + "\n", encoding="utf-8")
                return {"ok": True, "skipped": True, "artifact_path": str(paths["themes_json"]), "themes_count": len(themes0)}
            if paths["dataset_theme_report"].exists():
                return {"ok": True, "skipped": True, "artifact_path": str(paths["themes_json"]), "themes_count": len(themes0)}

        batches = json.loads(paths["batch_summaries"].read_text(encoding="utf-8"))
        brief = paths["dataset_brief"].read_text(encoding="utf-8")[:6000]

        allowed_examples: List[Dict[str, str]] = []
        allowed_set: set[tuple[str, str]] = set()
        if paths["sample_records"].exists():
            try:
                sr = json.loads(paths["sample_records"].read_text(encoding="utf-8"))
                recs = sr.get("records") or []
                for r in recs:
                    if not isinstance(r, dict):
                        continue
                    title = r.get("title")
                    url = r.get("url")
                    if isinstance(title, str) and isinstance(url, str) and title.strip() and url.strip():
                        t = title.strip()
                        u = url.strip()
                        allowed_examples.append({"title": t, "url": u})
                        allowed_set.add((t, u))
                    if len(allowed_examples) >= 50:
                        break
            except Exception:
                allowed_examples = []
                allowed_set = set()

        system = (
            "You are analyzing a dataset of articles.\n\n"
            "Input:\n- Batch summaries describing records (titles, bodies, URLs)\n\n"
            "Task:\n"
            "1) Identify the top 5-8 dominant themes\n"
            "2) Group similar topics together\n"
            "3) Estimate rough percentage distribution (integers; do not overclaim precision)\n"
            "4) Provide 2-3 real examples per theme\n\n"
            "Rules:\n"
            "- Use ONLY examples from ALLOWED_EXAMPLES. Do not invent titles or URLs.\n"
            "- If unsure about percentages, you may set 0.\n\n"
            "Output JSON ONLY with this exact shape:\n"
            '{"themes":[{"name":"...","description":"...","estimated_percentage":0,"examples":[{"title":"...","url":"..."}]}]}\n'
            "Start with '{' and end with '}'. No markdown. No extra keys."
        )
        user = (
            f"Question: {inputs.question or 'Extract dominant themes.'}\n\n"
            f"DATASET_BRIEF:\n{brief}\n\n"
            f"BATCH_SUMMARIES_JSON:\n{json.dumps(batches, ensure_ascii=False)[:12000]}\n\n"
            f"ALLOWED_EXAMPLES_JSON:\n{json.dumps(allowed_examples, ensure_ascii=False)[:10000]}"
        )

        text = await self.llm.text(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.1,
            num_predict=600,
        )
        obj = extract_json_object(text)
        themes_raw = obj.get("themes") if isinstance(obj, dict) else None

        themes: List[Dict[str, Any]] = []
        if not isinstance(themes_raw, list) or not themes_raw:
            themes = self._deterministic_themes_from_examples(allowed_examples)
        else:
            used: set[tuple[str, str]] = set()
            for t in themes_raw[:12]:
                if not isinstance(t, dict):
                    continue
                name = str(t.get("name") or "").strip()
                if not name:
                    continue
                desc = str(t.get("description") or "").strip()
                pct = t.get("estimated_percentage")
                pct_i = int(pct) if isinstance(pct, (int, float)) else 0
                pct_i = int(max(0, min(100, pct_i)))
                if pct_i == 0 and allowed_examples:
                    # Heuristic percentage based on keyword match in titles.
                    kw = [w for w in re.findall(r"[a-z0-9']+", name.lower()) if len(w) >= 3]
                    if kw:
                        hits = 0
                        for a in allowed_examples:
                            title = a.get("title", "").lower()
                            if any(k in title for k in kw):
                                hits += 1
                        pct_i = int(round(100.0 * (hits / max(1, len(allowed_examples)))))

                exs: List[Dict[str, str]] = []
                for e in (t.get("examples") or [])[:10]:
                    if not isinstance(e, dict):
                        continue
                    title = str(e.get("title") or "").strip()
                    url = str(e.get("url") or "").strip()
                    if not title or not url:
                        continue
                    if allowed_set and (title, url) not in allowed_set:
                        continue
                    if (title, url) in used:
                        continue
                    used.add((title, url))
                    exs.append({"title": title, "url": url})
                    if len(exs) >= 3:
                        break

                if len(exs) < 2 and allowed_examples:
                    kw = [w for w in re.findall(r"[a-z0-9']+", name.lower()) if len(w) >= 3]
                    for a in allowed_examples:
                        if len(exs) >= 3:
                            break
                        t0, u0 = a["title"], a["url"]
                        if (t0, u0) in used:
                            continue
                        if kw and not any(k in t0.lower() for k in kw):
                            continue
                        used.add((t0, u0))
                        exs.append({"title": t0, "url": u0})
                    for a in allowed_examples:
                        if len(exs) >= 3:
                            break
                        t0, u0 = a["title"], a["url"]
                        if (t0, u0) in used:
                            continue
                        used.add((t0, u0))
                        exs.append({"title": t0, "url": u0})

                themes.append({"name": name, "description": desc, "estimated_percentage": pct_i, "examples": exs[:3]})
                if len(themes) >= 8:
                    break

            if len(themes) < 5 and allowed_examples:
                leftover: List[Dict[str, str]] = []
                for a in allowed_examples:
                    t0, u0 = a["title"], a["url"]
                    if (t0, u0) in used:
                        continue
                    leftover.append({"title": t0, "url": u0})
                    if len(leftover) >= 3:
                        break
                if leftover:
                    themes.append(
                        {
                            "name": "Other / Uncategorized",
                            "description": "Additional topics observed in the sampled records.",
                            "estimated_percentage": 0,
                            "examples": leftover,
                        }
                    )

        themes = self._postprocess_themes(themes[:12])
        # Ensure at least 5 themes are present for operator usefulness; fill a residual bucket
        # using unused allowed examples (still grounded).
        if len(themes) < 5 and allowed_examples:
            used = set()
            for t in themes:
                for e in (t.get("examples") or []):
                    if isinstance(e, dict):
                        used.add((e.get("title"), e.get("url")))
            leftover: List[Dict[str, str]] = []
            for a in allowed_examples:
                key = (a.get("title"), a.get("url"))
                if key in used:
                    continue
                if a.get("title") and a.get("url"):
                    leftover.append({"title": a["title"], "url": a["url"]})
                if len(leftover) >= 3:
                    break
            if leftover:
                s = sum(int(t.get("estimated_percentage") or 0) for t in themes)
                other_pct = int(max(0, 100 - s)) if s <= 100 else 0
                themes.append(
                    {
                        "name": "Other / Uncategorized",
                        "description": "Additional topics observed in the sampled records.",
                        "estimated_percentage": other_pct,
                        "examples": leftover,
                    }
                )
        out = {"themes": themes[:8]}
        paths["themes_json"].write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["themes_md"].write_text(self._format_themes_md(themes[:8]), encoding="utf-8")
        # A more operator-friendly report that combines dataset facts + themes.
        facts = {}
        try:
            if paths["dataset_facts"].exists():
                facts = json.loads(paths["dataset_facts"].read_text(encoding="utf-8"))
            elif paths["dataset_profile"].exists():
                prof = json.loads(paths["dataset_profile"].read_text(encoding="utf-8"))
                cov = prof.get("coverage") or {}
                dup = prof.get("duplicate_analysis") or {}
                facts = {
                    "dataset_path": prof.get("abs_path"),
                    "json_kind": prof.get("json_kind"),
                    "size_bytes": prof.get("size_bytes"),
                    "sampled_records": cov.get("sampled_records"),
                    "estimated_total_records": cov.get("estimated_total_records"),
                    "planned_batches": len(prof.get("planned_batches") or []),
                    "duplicate_ratio": dup.get("duplicate_ratio"),
                    "coverage_ratio": (cov.get("coverage_ratio")),
                    "confidence": prof.get("confidence"),
                }
        except Exception:
            facts = {}

        header: List[str] = ["# Dataset Theme Report", ""]
        if facts:
            header.append("## Dataset Facts")
            header.append("")
            if facts.get("dataset_path"):
                header.append(f"- Dataset: {facts.get('dataset_path')}")
            if facts.get("json_kind"):
                header.append(f"- JSON kind: {facts.get('json_kind')}")
            if isinstance(facts.get("size_bytes"), int):
                header.append(f"- Size: {facts.get('size_bytes')} bytes")
            if facts.get("estimated_total_records") is not None:
                header.append(f"- Estimated total records: {facts.get('estimated_total_records')}")
            if facts.get("sampled_records") is not None:
                header.append(f"- Sampled records: {facts.get('sampled_records')}")
            if facts.get("planned_batches") is not None:
                header.append(f"- Planned batches: {facts.get('planned_batches')}")
            if facts.get("duplicate_ratio") is not None:
                try:
                    header.append(f"- Duplicate ratio (URL-based): {float(facts.get('duplicate_ratio')):.3f}")
                except Exception:
                    header.append(f"- Duplicate ratio (URL-based): {facts.get('duplicate_ratio')}")
            if facts.get("coverage_ratio") is not None:
                try:
                    header.append(f"- Coverage ratio (sample/estimate): {float(facts.get('coverage_ratio')):.3f}")
                except Exception:
                    header.append(f"- Coverage ratio (sample/estimate): {facts.get('coverage_ratio')}")
            if facts.get("confidence"):
                header.append(f"- Confidence: {facts.get('confidence')}")
            header.append("")

        header.append("## Themes")
        header.append("")
        theme_md = self._format_themes_md(themes[:8]).strip()
        # Remove the "# Dataset Themes" heading to avoid duplicate H1s.
        theme_md = re.sub(r"^# Dataset Themes\\s*", "", theme_md).strip()
        header.append(theme_md)
        header.append("")
        paths["dataset_theme_report"].write_text("\n".join(header).strip() + "\n", encoding="utf-8")
        return {"ok": True, "artifact_path": str(paths["themes_json"]), "themes_count": len(themes[:8])}

    async def dataset_summarize_batch(self, task_id: str, *, batch_index: int, inputs: ResearchInputs) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        if not paths["dataset_profile"].exists() or not paths["sample_records"].exists():
            return {"ok": False, "error": "missing dataset_profile.json or sample_records.json (run dataset_profile first)"}
        prof = json.loads(paths["dataset_profile"].read_text(encoding="utf-8"))
        sample = json.loads(paths["sample_records"].read_text(encoding="utf-8"))
        records = sample.get("records") or []
        planned = prof.get("planned_batches") or []
        b = next((x for x in planned if int(x.get("batch_index", -1)) == int(batch_index)), None)
        if not b:
            return {"ok": False, "error": f"batch not planned: {batch_index}"}
        start = int(b.get("start", 0))
        end = int(b.get("end", 0))
        batch_records = records[start:end]

        # Load existing batch summaries.
        db: Dict[str, Any] = {}
        if paths["batch_summaries"].exists():
            try:
                db = json.loads(paths["batch_summaries"].read_text(encoding="utf-8"))
            except Exception:
                db = {}
        key = str(batch_index)
        if key in (db or {}):
            return {"ok": True, "skipped": True, "batch_index": batch_index}

        question = inputs.question or "Summarize the dataset."
        system = (
            "You are a dataset batch summarizer. Return ONLY valid JSON with keys:\n"
            '{ "summary": string, "key_patterns": [string], "anomalies": [string], "open_questions": [string] }\n'
            "Rules:\n"
            "- Ground everything in the RECORDS.\n"
            "- Do not invent fields not present in the records.\n"
            "- Do not invent statistics (e.g. total record count). If unknown, say unknown.\n"
            "- Keep summary under 120 words.\n"
            "- Keep each list under 6 items.\n"
            "Return JSON only. Start with '{' and end with '}'.\n"
            'Example: {"summary":"...", "key_patterns":["..."], "anomalies":[], "open_questions":[]}'
            "No extra keys. No markdown."
        )
        user = f"Question: {question}\n\nRECORDS (JSON array):\n{json.dumps(batch_records, ensure_ascii=False)[:12000]}"
        text = await self.llm.text(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.1,
            num_predict=260,
        )
        obj = extract_json_object(text)
        # If the model fails to return JSON, fall back deterministically.
        if not obj or (not str(obj.get("summary") or "").strip() and not (obj.get("key_patterns") or obj.get("anomalies") or obj.get("open_questions"))):
            obj = {**_deterministic_dataset_batch_summary(batch_records), "fallback_used": True}
        out = {
            "batch_index": int(batch_index),
            "summary": str(obj.get("summary") or "").strip(),
            "key_patterns": [str(x).strip() for x in (obj.get("key_patterns") or []) if str(x).strip()],
            "anomalies": [str(x).strip() for x in (obj.get("anomalies") or []) if str(x).strip()],
            "open_questions": [str(x).strip() for x in (obj.get("open_questions") or []) if str(x).strip()],
            "fallback_used": bool(obj.get("fallback_used") is True),
            "record_range": {"start": start, "end": end},
        }
        db[key] = out
        paths["batch_summaries"].write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "artifact_path": str(paths["batch_summaries"]), "batch_index": int(batch_index)}

    async def dataset_synthesis(self, task_id: str, inputs: ResearchInputs) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        if not paths["dataset_profile"].exists():
            return {"ok": False, "error": "missing dataset_profile.json (run dataset_profile first)"}
        prof = json.loads(paths["dataset_profile"].read_text(encoding="utf-8"))
        batches = {}
        if paths["batch_summaries"].exists():
            try:
                batches = json.loads(paths["batch_summaries"].read_text(encoding="utf-8"))
            except Exception:
                batches = {}

        question = inputs.question or "Summarize the dataset."
        # Provide a few grounded examples from sample_records.json to keep synthesis anchored.
        examples: List[str] = []
        if paths["sample_records"].exists():
            try:
                sr = json.loads(paths["sample_records"].read_text(encoding="utf-8"))
                recs = sr.get("records") or []
                for r in recs[:10]:
                    if isinstance(r, dict):
                        title = r.get("title")
                        url = r.get("url")
                        if isinstance(title, str) and isinstance(url, str) and title and url:
                            examples.append(f"- {title} ({url})")
                    if len(examples) >= 3:
                        break
            except Exception:
                examples = []
        system = (
            "You are a dataset researcher. Write a concise dataset brief in Markdown with sections:\n"
            "1) Dataset Overview\n2) Key Patterns\n3) Notable Anomalies\n4) Open Questions\n5) Limitations\n"
            "Ground everything in the provided dataset profile and batch summaries.\n"
            "Do not invent statistics (e.g. total record count). If unknown, say unknown."
        )
        user = (
            f"Question: {question}\n\nDATASET_PROFILE:\n{json.dumps(prof, ensure_ascii=False)[:6000]}\n\n"
            f"BATCH_SUMMARIES:\n{json.dumps(batches, ensure_ascii=False)[:12000]}"
        )
        if examples:
            user += "\n\nSAMPLE_EXAMPLES:\n" + "\n".join(examples[:3])
        brief = (
            await self.llm.text(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
                num_predict=620,
            )
        ).strip()
        paths["dataset_brief"].write_text(brief + "\n", encoding="utf-8")
        return {"ok": True, "dataset_brief": str(paths["dataset_brief"]), "artifact_path": str(paths["dataset_brief"])}

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
            num_predict=260,
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

        # Bound per-invocation work so /continue stays responsive on large documents.
        max_chunks_per_call = 1
        start_idx = len(chunk_summaries)
        end_idx = min(len(chunks), start_idx + max_chunks_per_call)
        for idx in range(start_idx, end_idx):
            s = await self._summarize_chunk(chunks[idx], question=question, mode=inputs.mode)
            chunk_summaries.append({"chunk_index": idx, **s})
            # Persist after each chunk for resumability.
            chunk_db[file_key] = {
                "file_sha256": file_entry.get("sha256"),
                "chunks_total": len(chunks),
                "chunks": chunk_summaries,
            }
            paths["chunk_summaries"].write_text(json.dumps(chunk_db, ensure_ascii=False, indent=2), encoding="utf-8")

        # If not all chunks are processed yet, return partial so the TaskEngine keeps this step pending.
        if len(chunk_summaries) < len(chunks):
            return {"ok": True, "partial": True, "file": rel, "chunks_done": len(chunk_summaries), "chunks_total": len(chunks)}

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
        file_summary = (
            await self.llm.text(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
                num_predict=260,
            )
        ).strip()

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
        brief = (
            await self.llm.text(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
                num_predict=520,
            )
        ).strip()
        paths["research_brief"].write_text(brief + "\n", encoding="utf-8")
        return {"ok": True, "research_brief": str(paths['research_brief'])}

    async def final_report(self, task_id: str, inputs: ResearchInputs) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        brief = paths["research_brief"].read_text(encoding="utf-8") if paths["research_brief"].exists() else ""
        file_summaries = paths["file_summaries"].read_text(encoding="utf-8") if paths["file_summaries"].exists() else ""

        # Dataset mode grounding: include profile + batch summaries + a few sample examples.
        dataset_profile = ""
        batch_summaries = ""
        sample_examples = ""
        themes_json = ""
        themes_obj: Optional[Dict[str, Any]] = None
        dataset_facts: Dict[str, Any] = {}
        if paths.get("dataset_profile") and paths["dataset_profile"].exists():
            try:
                dataset_profile = paths["dataset_profile"].read_text(encoding="utf-8")[:8000]
                dp = json.loads(paths["dataset_profile"].read_text(encoding="utf-8"))
                dataset_facts = {
                    "json_kind": dp.get("json_kind"),
                    "size_bytes": dp.get("size_bytes"),
                    "estimated_total_records": dp.get("estimated_total_records"),
                    "sample_records_count": len(dp.get("sample_records") or []),
                    "duplicate_analysis": dp.get("duplicate_analysis"),
                    "coverage": dp.get("coverage"),
                    "confidence": dp.get("confidence"),
                }
            except Exception:
                dataset_profile = ""
        if paths.get("batch_summaries") and paths["batch_summaries"].exists():
            try:
                batch_summaries = paths["batch_summaries"].read_text(encoding="utf-8")[:12000]
            except Exception:
                batch_summaries = ""
        if paths.get("themes_json") and paths["themes_json"].exists():
            try:
                themes_json = paths["themes_json"].read_text(encoding="utf-8")[:12000]
                themes_obj = json.loads(paths["themes_json"].read_text(encoding="utf-8"))
            except Exception:
                themes_json = ""
                themes_obj = None
        if paths.get("sample_records") and paths["sample_records"].exists():
            try:
                sr = json.loads(paths["sample_records"].read_text(encoding="utf-8"))
                recs = sr.get("records") or []
                ex: List[str] = []
                # Extract schema hints deterministically (common keys and top domains).
                dicts = [r for r in recs if isinstance(r, dict)]
                if dicts:
                    key_counts: Dict[str, int] = {}
                    for d in dicts:
                        for k in d.keys():
                            key_counts[str(k)] = key_counts.get(str(k), 0) + 1
                    total = len(dicts)
                    common_keys = [k for k, c in sorted(key_counts.items(), key=lambda kv: (-kv[1], kv[0])) if c >= max(1, int(total * 0.8))]
                    top_keys = [k for k, _ in sorted(key_counts.items(), key=lambda kv: (-kv[1], kv[0]))][:10]
                    dataset_facts["common_keys_80pct"] = common_keys[:12]
                    dataset_facts["top_keys"] = top_keys[:12]

                    domains: Dict[str, int] = {}
                    url_key = "url" if "url" in key_counts else None
                    if url_key:
                        for d in dicts:
                            v = d.get(url_key)
                            if isinstance(v, str) and v:
                                try:
                                    dom = urlparse(v).netloc.lower()
                                except Exception:
                                    dom = ""
                                if dom:
                                    domains[dom] = domains.get(dom, 0) + 1
                        top_domains = [k for k, _ in sorted(domains.items(), key=lambda kv: (-kv[1], kv[0]))][:6]
                        dataset_facts["top_url_domains"] = top_domains

                        # Temporal hints (best-effort) from URL paths like /YYYY/MM/DD/.
                        years: Dict[str, int] = {}
                        for d in dicts:
                            v = d.get(url_key)
                            if not isinstance(v, str) or not v:
                                continue
                            m = re.search(r"/(20\\d{2})/([01]\\d)/([0-3]\\d)/", v)
                            if m:
                                y = m.group(1)
                                years[y] = years.get(y, 0) + 1
                        if years:
                            dataset_facts["top_years_in_urls"] = [y for y, _ in sorted(years.items(), key=lambda kv: (-kv[1], kv[0]))][:6]

                    # Missing-field signals over the sample.
                    keys_for_missing = (dataset_facts.get("common_keys_80pct") or dataset_facts.get("top_keys") or [])
                    miss: Dict[str, int] = {}
                    for k in keys_for_missing:
                        kk = str(k)
                        c = 0
                        for d in dicts:
                            if kk not in d:
                                c += 1
                                continue
                            v = d.get(kk)
                            if v is None:
                                c += 1
                            elif isinstance(v, str) and not v.strip():
                                c += 1
                        miss[kk] = c
                    if miss:
                        dataset_facts["missing_fields_sample"] = miss

                for r in recs[:20]:
                    if isinstance(r, dict):
                        title = r.get("title")
                        url = r.get("url")
                        if isinstance(title, str) and isinstance(url, str) and title and url:
                            ex.append(f"- {title} ({url})")
                    if len(ex) >= 5:
                        break
                if ex:
                    sample_examples = "\n".join(ex)
            except Exception:
                sample_examples = ""

        system = (
            "You are a senior technical writer. Write a final report in Markdown.\n"
            "Do not invent statistics (e.g. record counts, totals) that are not explicitly present in the inputs. If unknown, say unknown."
        )
        question = inputs.question or "Summarize the documents."
        user = f"Question: {question}\n\nBRIEF:\n{brief[:8000]}\n\nFILE SUMMARIES:\n{file_summaries[:12000]}"
        if dataset_profile or batch_summaries or sample_examples or themes_json:
            system += (
                "\n\nIf DATASET_PROFILE/BATCH_SUMMARIES/THEMES_JSON/SAMPLE_EXAMPLES are provided, treat this as a dataset analysis report.\n"
                "Use this exact structure:\n"
                "# Dataset Analysis Report\n\n"
                "## 1. Executive Summary\n"
                "- dataset size\n- estimated records\n- coverage + confidence level\n\n"
                "## 2. Key Themes\n"
                "(load from THEMES_JSON)\n\n"
                "## 3. Dataset Structure\n"
                "- schema\n- field stats\n\n"
                "## 4. Data Quality\n"
                "- duplicate ratio\n- missing fields (if detectable from sample)\n\n"
                "## 5. Observations\n"
                "- patterns (e.g. all from same domain)\n- temporal hints (if present)\n\n"
                "## 6. Recommendations\n"
                "- normalization\n- deduplication\n- downstream usage\n\n"
                "Rules:\n"
                "- Do not claim sample records are unavailable if SAMPLE_EXAMPLES are present.\n"
                "- In Key Themes: list each theme with name, description, estimated %, and 2-3 examples.\n"
                "- Examples must use the provided SAMPLE_EXAMPLES verbatim (do not invent).\n"
                "- Include coverage ratio, duplicate ratio, and confidence level if present.\n"
                "- Avoid placeholders."
            )
            if dataset_facts:
                user += "\n\nDATASET_FACTS_JSON:\n" + json.dumps(dataset_facts, ensure_ascii=False, indent=2)[:4000]
            if dataset_profile:
                user += "\n\nDATASET_PROFILE:\n" + dataset_profile
            if batch_summaries:
                user += "\n\nBATCH_SUMMARIES:\n" + batch_summaries
            if themes_json:
                user += "\n\nTHEMES_JSON:\n" + themes_json
            if sample_examples:
                user += "\n\nSAMPLE_EXAMPLES:\n" + sample_examples
        report = (
            await self.llm.text(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
                num_predict=900,
            )
        ).strip()

        # If the model output looks generic/un-grounded, fall back to a deterministic report.
        if dataset_facts and sample_examples:
            # Require at least one sample URL/title to appear.
            urls = []
            for ln in sample_examples.splitlines():
                if "(" in ln and ")" in ln:
                    u = ln.split("(", 1)[-1].rstrip(")")
                    if u.startswith("http"):
                        urls.append(u)
            grounded = any(u in report for u in urls[:3]) or ("url" in report.lower() and "title" in report.lower())
            if not grounded:
                report = _deterministic_dataset_final_report(
                    question=question,
                    dataset_facts=dataset_facts,
                    batch_summaries_text=batch_summaries,
                    sample_examples=sample_examples,
                    themes_obj=themes_obj if isinstance(themes_obj, dict) else None,
                ).strip()
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

        if step_type == "dataset_profile":
            return await self.dataset_profile(task_id, inputs)

        if step_type == "expand_dataset_steps":
            # This step is handled by the TaskEngine (it expands batch steps after profiling).
            return {"ok": True, "note": "expanded_by_engine"}

        if step_type.startswith("dataset_batch:"):
            try:
                bi = int(step_type[len("dataset_batch:") :].strip())
            except Exception:
                return {"ok": False, "error": f"invalid batch step_type: {step_type}"}
            return await self.dataset_summarize_batch(task_id, batch_index=bi, inputs=inputs)

        if step_type == "dataset_synthesis":
            return await self.dataset_synthesis(task_id, inputs)

        if step_type == "dataset_theme_extraction":
            return await self.dataset_theme_extraction(task_id, inputs)

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
            # Dataset mode: incorporate dataset_brief.md if present.
            if paths.get("dataset_brief") and paths["dataset_brief"].exists():
                # Reuse the existing final_report writer but seed file_summaries with dataset brief.
                # This keeps endpoint/artifact naming consistent.
                brief = paths["dataset_brief"].read_text(encoding="utf-8")
                paths["research_brief"].write_text(brief + "\n", encoding="utf-8")
            return await self.final_report(task_id, inputs)

        return {"ok": True, "note": f"noop:{step_type}"}
