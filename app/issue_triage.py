from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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
    ".log",
}


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _read_text_file(path: Path, *, max_bytes: int = 350_000) -> Tuple[Optional[str], Optional[str]]:
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

def _normalize_ws(s: str) -> str:
    return " ".join((s or "").strip().split())


def _keywords_from_issue(issue: str) -> List[str]:
    # Conservative: extract meaningful tokens but also add known runtime strings.
    tokens = set()
    for w in re.split(r"[^a-zA-Z0-9_:/.-]+", issue or ""):
        w = w.strip()
        if len(w) < 4:
            continue
        if w.lower() in {
            "this",
            "that",
            "with",
            "from",
            "into",
            "have",
            "been",
            "seems",
            "return",
            "request",
            "triage",
            "message",
            "internal",
            "stall",
            "stalls",
            "fallback",
        }:
            continue
        tokens.add(w.lower())
    # Add repo-specific high-signal keywords for agent-path / fallback issues.
    tokens.update(
        {
            # High signal strings
            "task finished or iteration limit reached",
            "agent_path",
            "planner",
            "executor",
            "agentloop",
            "agent_loop",
            "x_agent_status",
            "tool_call",
            "tool_calls",
            "approval_required",
            "pending_approval",
            "determine_execution_route",
            "iteration limit reached",
            "timeout",
            "continue",
        }
    )
    # Prefer multi-word phrases first.
    phrases = [t for t in tokens if " " in t]
    singles = [t for t in tokens if " " not in t]
    return phrases + sorted(singles)


def _evidence_from_text(
    *,
    rel_path: str,
    abs_path: str,
    text: str,
    keywords: List[str],
    max_items: int = 8,
) -> List[Dict[str, Any]]:
    """
    Deterministic evidence extraction: find the first few keyword matches and
    emit short, grounded snippets (no fabrication).
    """
    if not text:
        return []
    low = text.lower()
    if not any(k in low for k in keywords if k):
        return []

    out: List[Dict[str, Any]] = []
    lines = text.splitlines()
    for i, raw in enumerate(lines, start=1):
        l = raw.strip()
        if not l:
            continue
        ll = l.lower()
        # Prefer the highest-signal keyword match on the line.
        hit = None
        hit_score = -1
        for k in keywords:
            if not k:
                continue
            if k not in ll:
                continue
            score = 10
            if "task finished or iteration limit reached" in k:
                score = 100
            elif k in {"x_agent_status", "agent_path", "determine_execution_route"}:
                score = 80
            elif k in {"approval_required", "pending_approval", "tool_call", "tool_calls"}:
                score = 70
            elif k in {"planner", "executor", "agent_loop", "agentloop"}:
                score = 50
            elif k in {"timeout", "iteration limit reached"}:
                score = 60
            if score > hit_score:
                hit_score = score
                hit = k
        if not hit:
            continue
        # Drop low-signal matches (keeps evidence_table focused and usable).
        if hit_score < 40:
            continue
        snippet = _normalize_ws(l)[:240]
        # Confidence heuristic: exact high-signal string matches higher.
        conf = 0.65
        if "task finished or iteration limit reached" in hit:
            conf = 0.9
        elif hit in {"x_agent_status", "agent_path", "determine_execution_route"}:
            conf = 0.85
        elif hit in {"approval_required", "pending_approval", "tool_call"}:
            conf = 0.8
        out.append(
            {
                "source_file": rel_path,
                "source_type": "log" if rel_path.endswith(".log") else "code",
                "line": i,
                "snippet": snippet,
                "relevance_reason": f"Matched keyword '{hit}' in line {i}.",
                "confidence": conf,
                "matched_keyword": hit,
            }
        )
        if len(out) >= max_items:
            break
    return out


@dataclass
class IssueTriageInputs:
    root_path: str
    issue: str
    files: Optional[List[str]] = None
    logs: Optional[List[str]] = None


class IssueTriagePipeline:
    """
    Bounded, artifact-first issue triage workflow.
    Produces:
      issue_summary.md
      evidence_table.json
      likely_causes.md
      reproduction_steps.md
      next_actions.md
    """

    def __init__(self, llm: OllamaChatClient):
        self.llm = llm

    def artifact_dir(self, task_id: str) -> Path:
        return Path(".runtime") / "tasks" / task_id

    def _paths(self, task_id: str) -> Dict[str, Path]:
        d = self.artifact_dir(task_id)
        return {
            "dir": d,
            "issue_summary": d / "issue_summary.md",
            "evidence_table": d / "evidence_table.json",
            "likely_causes": d / "likely_causes.md",
            "reproduction_steps": d / "reproduction_steps.md",
            "next_actions": d / "next_actions.md",
        }

    def plan_steps(self, inputs: IssueTriageInputs) -> List[Dict[str, Any]]:
        # Deterministic steps for stability.
        return [
            {"step_type": "inspect_targets", "instruction": "Inspect the repo and gather likely relevant files/logs"},
            {"step_type": "extract_evidence", "instruction": "Extract grounded evidence snippets and build evidence_table.json"},
            {"step_type": "rank_causes", "instruction": "Rank likely causes and explain reasoning"},
            {"step_type": "repro_checklist", "instruction": "Generate reproduction checklist"},
            {"step_type": "next_actions", "instruction": "Recommend next actions"},
        ]

    def _resolve_paths(self, inputs: IssueTriageInputs) -> Dict[str, Any]:
        root = Path(inputs.root_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return {"ok": False, "error": f"root_path not found or not a directory: {root}"}

        targets: List[Path] = []
        skipped: List[Dict[str, Any]] = []

        explicit = (inputs.files or []) + (inputs.logs or [])
        for fp in explicit:
            p = (root / fp).resolve() if not os.path.isabs(fp) else Path(fp).expanduser().resolve()
            if not p.exists() or not p.is_file():
                skipped.append({"path": str(fp), "reason": "missing_or_not_file"})
                continue
            if p.suffix and p.suffix.lower() not in ALLOWED_TEXT_EXTS:
                skipped.append({"path": str(fp), "reason": f"unsupported_ext:{p.suffix}"})
                continue
            targets.append(p)

        # If no explicit targets, pick a small default set from common folders.
        if not targets:
            common = ["app", "src", "server", "cli", "mcp"]
            for d in common:
                cand = root / d
                if not cand.exists() or not cand.is_dir():
                    continue
                for p in cand.rglob("*"):
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
                    targets.append(p)
                    if len(targets) >= 25:
                        break
                if len(targets) >= 25:
                    break

        # De-dupe and cap.
        uniq: List[Path] = []
        seen = set()
        for p in targets:
            if str(p) in seen:
                continue
            seen.add(str(p))
            uniq.append(p)
            if len(uniq) >= 30:
                break
        targets = uniq

        files_out: List[Dict[str, Any]] = []
        for p in targets:
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

        return {"ok": True, "root": str(root), "targets": files_out, "skipped": skipped}

    async def _write_issue_summary(self, task_id: str, inputs: IssueTriageInputs, targets: Dict[str, Any]) -> None:
        paths = self._paths(task_id)
        system = (
            "You are an issue triage assistant. Write a concise Markdown issue summary.\n"
            "Include: scope, symptoms, and what evidence is available (by file path)."
        )
        tlist = "\n".join(f"- {t.get('path')} ({t.get('size_bytes')} bytes)" for t in (targets.get("targets") or [])[:30])
        user = f"ISSUE:\n{inputs.issue}\n\nAVAILABLE TARGETS:\n{tlist}"
        md = (await self.llm.text([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.1)).strip()
        if not md:
            md = f"# Issue Summary\n\n{inputs.issue}\n"
        paths["issue_summary"].write_text(md + "\n", encoding="utf-8")

    async def _extract_evidence_table(self, task_id: str, inputs: IssueTriageInputs, targets: Dict[str, Any]) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)

        keywords = _keywords_from_issue(inputs.issue)

        # Read bounded excerpts + keep an excerpt lookup so we can verify LLM-cited snippets.
        excerpts: List[str] = []
        excerpt_by_file: Dict[str, str] = {}
        for t in (targets.get("targets") or [])[:20]:
            ap = Path(t["abs_path"])
            text, err = _read_text_file(ap)
            if text is None:
                continue
            head = "\n".join(text.splitlines()[:160])
            rel = str(t.get("path") or ap.name)
            excerpt_by_file[rel] = head
            excerpts.append(f"FILE: {rel}\n---\n{head}\n")

        system = (
            "You are an issue triage assistant. Return ONLY valid JSON with keys:\n"
            '{ "evidence": [ { "source_file": string, "source_type": string, "snippet": string, "relevance_reason": string, "confidence": number } ] }\n'
            "Rules:\n"
            "- Only cite snippets that appear in the excerpts.\n"
            "- Keep snippet short (<= 200 chars).\n"
            "- source_type must be one of: code, log.\n"
            "- confidence must be between 0 and 1.\n"
            "No markdown."
        )
        user = (
            f"ISSUE:\n{inputs.issue}\n\n"
            f"KEYWORDS (use for matching):\n" + "\n".join(f"- {k}" for k in keywords[:30]) + "\n\n"
            "EXCERPTS:\n\n" + "\n\n".join(excerpts)
        )[:14000]
        raw = await self.llm.text([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.1)
        obj = extract_json_object(raw)
        evidence = obj.get("evidence") if isinstance(obj, dict) else None
        if not isinstance(evidence, list):
            evidence = []

        # Validate LLM evidence items to avoid fabricated snippets.
        cleaned: List[Dict[str, Any]] = []
        for it in evidence[:80]:
            if not isinstance(it, dict):
                continue
            sf = str(it.get("source_file") or "").strip()
            sn = _normalize_ws(str(it.get("snippet") or ""))
            if not sf or not sn:
                continue
            excerpt = excerpt_by_file.get(sf, "")
            if excerpt and sn.replace("`", "") not in excerpt:
                # If snippet doesn't appear in excerpt, drop it.
                continue
            stype = str(it.get("source_type") or "code").strip().lower()
            if stype not in {"code", "log"}:
                stype = "code"
            try:
                conf = float(it.get("confidence"))
            except Exception:
                conf = 0.6
            conf = max(0.0, min(1.0, conf))
            cleaned.append(
                {
                    "source_file": sf,
                    "source_type": stype,
                    "snippet": sn[:240],
                    "relevance_reason": _normalize_ws(str(it.get("relevance_reason") or it.get("why_relevant") or ""))[:300] or "Matched issue keyword in excerpt.",
                    "confidence": conf,
                }
            )

        # Deterministic fallback: if LLM returned nothing useful, do keyword matching directly on the actual files.
        if not cleaned:
            fallback: List[Dict[str, Any]] = []
            for t in (targets.get("targets") or [])[:30]:
                ap = Path(t["abs_path"])
                rel = str(t.get("path") or ap.name)
                text, err = _read_text_file(ap)
                if text is None:
                    continue
                fallback.extend(_evidence_from_text(rel_path=rel, abs_path=str(ap), text=text, keywords=keywords, max_items=6))
                if len(fallback) >= 40:
                    break
            # De-dupe by (file, snippet)
            seen = set()
            for it in fallback:
                key = (it.get("source_file"), it.get("snippet"))
                if key in seen:
                    continue
                seen.add(key)
                cleaned.append(it)
                if len(cleaned) >= 40:
                    break

        # Prefer higher-confidence (more specific) evidence first.
        try:
            cleaned.sort(key=lambda x: (-float(x.get("confidence") or 0.0), str(x.get("source_file") or ""), int(x.get("line") or 0)))
        except Exception:
            pass

        out = {"ok": True, "root": targets.get("root"), "issue": inputs.issue, "evidence": cleaned[:60], "targets": targets.get("targets") or []}
        paths["evidence_table"].write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    async def _write_likely_causes(self, task_id: str, inputs: IssueTriageInputs, evidence_table: Dict[str, Any]) -> None:
        paths = self._paths(task_id)
        evidence_list = evidence_table.get("evidence") if isinstance(evidence_table, dict) else []
        evidence_snippets = []
        for it in (evidence_list or [])[:12]:
            if not isinstance(it, dict):
                continue
            sf = it.get("source_file")
            ln = it.get("line")
            sn = it.get("snippet")
            if sf and sn:
                evidence_snippets.append(f"- {sf}:L{ln} {sn}")
        system = (
            "You are a pragmatic debugger. Write Markdown listing likely causes ranked (most likely first).\n"
            "Rules:\n"
            "- Ground each cause in the evidence_table entries by quoting short snippets and the source_file.\n"
            "- If evidence is weak, say so.\n"
            "Format:\n"
            "- Use numbered causes.\n"
            "- Under each cause, add 1-3 Evidence bullets with `source_file: snippet`.\n"
        )
        user = (
            f"ISSUE:\n{inputs.issue}\n\n"
            "TOP EVIDENCE SNIPPETS:\n"
            + ("\n".join(evidence_snippets) if evidence_snippets else "- (none)\n")
            + "\n\nEVIDENCE (JSON):\n"
            + json.dumps(evidence_list, ensure_ascii=False)[:12000]
        )
        md = (await self.llm.text([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.1)).strip()
        if not md:
            md = "# Likely Causes\n\n- (insufficient evidence)\n"
        paths["likely_causes"].write_text(md + "\n", encoding="utf-8")

    async def _write_repro_steps(self, task_id: str, inputs: IssueTriageInputs) -> None:
        paths = self._paths(task_id)
        system = "Write a Markdown reproduction checklist with steps and expected/actual."
        user = f"ISSUE:\n{inputs.issue}\n"
        md = (await self.llm.text([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.1)).strip()
        if not md:
            md = "# Reproduction Steps\n\n- (none)\n"
        paths["reproduction_steps"].write_text(md + "\n", encoding="utf-8")

    async def _write_next_actions(self, task_id: str, inputs: IssueTriageInputs) -> None:
        paths = self._paths(task_id)
        likely = paths["likely_causes"].read_text(encoding="utf-8") if paths["likely_causes"].exists() else ""
        ev = paths["evidence_table"].read_text(encoding="utf-8") if paths["evidence_table"].exists() else ""
        evidence_list = []
        try:
            evidence_list = (json.loads(ev).get("evidence") or []) if ev else []
        except Exception:
            evidence_list = []
        system = (
            "Write a short Markdown list of next actions.\n"
            "Include: what to check next, what logs to capture, and what minimal code areas to inspect.\n"
            "Ground the actions in the available evidence snippets when present.\n"
        )
        top_snips = []
        for it in (evidence_list or [])[:10]:
            if not isinstance(it, dict):
                continue
            if it.get("source_file") and it.get("snippet"):
                top_snips.append(f"- {it.get('source_file')}:L{it.get('line')} {it.get('snippet')}")
        user = (
            f"ISSUE:\n{inputs.issue}\n\nLIKELY_CAUSES:\n{likely[:8000]}\n\n"
            "TOP EVIDENCE SNIPPETS:\n"
            + ("\n".join(top_snips) if top_snips else "- (none)\n")
        )
        md = (await self.llm.text([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.1)).strip()
        if not md:
            md = "# Next Actions\n\n- (none)\n"
        paths["next_actions"].write_text(md + "\n", encoding="utf-8")

    async def run_step(self, task_id: str, step: Dict[str, Any], inputs: IssueTriageInputs) -> Dict[str, Any]:
        paths = self._paths(task_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        st = step.get("step_type") or "generic"

        if st == "inspect_targets":
            targets = self._resolve_paths(inputs)
            if not targets.get("ok"):
                return {"ok": False, "error": targets.get("error", "inspect_failed")}
            await self._write_issue_summary(task_id, inputs, targets)
            return {"ok": True, "artifact_path": str(paths["issue_summary"]), "targets_count": len(targets.get("targets") or [])}

        if st == "extract_evidence":
            targets = self._resolve_paths(inputs)
            if not targets.get("ok"):
                return {"ok": False, "error": targets.get("error", "inspect_failed")}
            out = await self._extract_evidence_table(task_id, inputs, targets)
            return {"ok": True, "artifact_path": str(paths["evidence_table"]), "evidence_count": len(out.get("evidence") or [])}

        if st == "rank_causes":
            if not paths["evidence_table"].exists():
                return {"ok": False, "error": "missing evidence_table.json"}
            ev = json.loads(paths["evidence_table"].read_text(encoding="utf-8"))
            await self._write_likely_causes(task_id, inputs, ev)
            return {"ok": True, "artifact_path": str(paths["likely_causes"])}

        if st == "repro_checklist":
            await self._write_repro_steps(task_id, inputs)
            return {"ok": True, "artifact_path": str(paths["reproduction_steps"])}

        if st == "next_actions":
            await self._write_next_actions(task_id, inputs)
            return {"ok": True, "artifact_path": str(paths["next_actions"])}

        return {"ok": True, "note": f"noop:{st}"}
