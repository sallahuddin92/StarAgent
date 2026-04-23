from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .artifact_registry import task_conventions


def _parse_iso_dt(s: Any) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        st = str(s)
        if st.endswith("Z"):
            st = st[:-1] + "+00:00"
        return datetime.fromisoformat(st)
    except Exception:
        return None


def time_meta(task_run: Dict[str, Any]) -> Dict[str, Any]:
    created = _parse_iso_dt(task_run.get("created_at"))
    updated = _parse_iso_dt(task_run.get("updated_at"))
    now = datetime.utcnow()
    age_s = (now - created).total_seconds() if created else None
    duration_s = (updated - created).total_seconds() if (created and updated) else None
    return {"age_s": age_s, "duration_s": duration_s}


def progress_meta(task_run: Dict[str, Any], steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    completed = [s for s in steps if s.get("status") == "completed"]
    failed = [s for s in steps if s.get("status") == "failed"]
    paused = [s for s in steps if s.get("status") == "paused"]
    running = [s for s in steps if s.get("status") == "running"]
    pending = [s for s in steps if s.get("status") == "pending"]

    cur = None
    idx = int(task_run.get("current_step_index") or 0)
    for s in steps:
        if int(s.get("step_index") or 0) == idx:
            cur = s
            break
    if cur is None:
        for s in steps:
            if s.get("status") != "completed":
                cur = s
                break

    pct = 0.0
    if steps:
        pct = round(len(completed) / max(1, len(steps)) * 100.0, 1)
    return {
        "counts": {
            "total": len(steps),
            "completed": len(completed),
            "pending": len(pending),
            "running": len(running),
            "paused": len(paused),
            "failed": len(failed),
        },
        "percent_complete": pct,
        "current_step": cur,
        "last_completed_step": completed[-1] if completed else None,
    }


def _safe_read_json_file(p: Path, *, max_bytes: int = 2_000_000) -> Optional[Any]:
    try:
        if not p.exists() or not p.is_file():
            return None
        try:
            if int(p.stat().st_size) > max_bytes:
                return None
        except Exception:
            pass
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def dataset_meta(task_run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Best-effort dataset metrics surfaced for operator UX.
    Additive only: if dataset artifacts don't exist, returns None.
    """
    aj = task_run.get("artifacts_json") or {}
    task_id = str(task_run.get("task_id") or "")
    artifact_dir = Path(str(aj.get("artifact_dir") or (Path(".runtime") / "tasks" / task_id))).resolve()

    prof = _safe_read_json_file(artifact_dir / "dataset_profile.json")
    if not isinstance(prof, dict):
        return None

    cov = prof.get("coverage") or {}
    dup = prof.get("duplicate_analysis") or {}
    planned = prof.get("planned_batches") or []

    sampled = cov.get("sampled_records")
    try:
        sampled = int(sampled) if sampled is not None else None
    except Exception:
        sampled = None
    planned_n = len(planned) if isinstance(planned, list) else None

    themes_obj = _safe_read_json_file(artifact_dir / "themes.json")
    themes_list: List[Dict[str, Any]] = []
    if isinstance(themes_obj, dict) and isinstance(themes_obj.get("themes"), list):
        themes_list = [t for t in themes_obj.get("themes") if isinstance(t, dict)]

    def _pct(t: Dict[str, Any]) -> int:
        try:
            return int(t.get("estimated_percentage") or 0)
        except Exception:
            return 0

    themes_list.sort(key=lambda t: (-_pct(t), str(t.get("name") or "")))
    top_themes = []
    for t in themes_list[:6]:
        nm = str(t.get("name") or "").strip()
        if not nm:
            continue
        top_themes.append({"name": nm, "estimated_percentage": _pct(t)})

    dm = {
        "dataset_path": prof.get("abs_path"),
        "json_kind": prof.get("json_kind"),
        "size_bytes": prof.get("size_bytes"),
        "sample_records_count": sampled,
        "planned_batches": planned_n,
        "duplicate_ratio": (dup.get("duplicate_ratio") if isinstance(dup, dict) else None),
        "coverage_ratio": (cov.get("coverage_ratio") if isinstance(cov, dict) else None),
        "confidence": prof.get("confidence"),
        "top_themes": top_themes,
    }

    # A small, display-ready representation to reduce duplication across surfaces.
    lines: List[str] = []
    if dm.get("dataset_path"):
        lines.append(f"path: {dm.get('dataset_path')}")
    if dm.get("json_kind"):
        lines.append(f"kind: {dm.get('json_kind')}")
    if dm.get("size_bytes") is not None:
        lines.append(f"size_bytes: {dm.get('size_bytes')}")
    if dm.get("sample_records_count") is not None:
        lines.append(f"sample_records: {dm.get('sample_records_count')}")
    if dm.get("planned_batches") is not None:
        lines.append(f"planned_batches: {dm.get('planned_batches')}")
    if dm.get("duplicate_ratio") is not None:
        lines.append(f"duplicate_ratio: {dm.get('duplicate_ratio')}")
    if dm.get("coverage_ratio") is not None:
        lines.append(f"coverage_ratio: {dm.get('coverage_ratio')}")
    if dm.get("confidence"):
        lines.append(f"confidence: {dm.get('confidence')}")
    if top_themes:
        parts = []
        for t in top_themes[:5]:
            nm = str(t.get("name") or "").strip()
            if not nm:
                continue
            pct = t.get("estimated_percentage")
            if isinstance(pct, int) and pct > 0:
                parts.append(f"{nm} ({pct}%)")
            else:
                parts.append(nm)
        if parts:
            lines.append("top_themes: " + "; ".join(parts))
    dm["display_lines"] = lines
    return dm


def approval_meta(task_run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize approval/pending tool-call information so surfaces don't need to
    re-parse tool_call JSON in multiple places.
    """
    aj = task_run.get("artifacts_json") or {}
    pending = aj.get("pending_approval")
    if not isinstance(pending, dict):
        return None
    tc = pending.get("tool_call") if isinstance(pending.get("tool_call"), dict) else None
    if not tc:
        return None
    fn = ((tc.get("function") or {}) if isinstance(tc.get("function"), dict) else {}).get("name") or "unknown"
    args_raw = ((tc.get("function") or {}) if isinstance(tc.get("function"), dict) else {}).get("arguments") or "{}"
    args: Dict[str, Any] = {}
    try:
        if isinstance(args_raw, str):
            args = json.loads(args_raw) if args_raw.strip() else {}
    except Exception:
        args = {"raw_arguments": args_raw}
    target = args.get("path") if isinstance(args, dict) else None
    note = pending.get("note")
    out = {"required": True, "action": str(fn), "target": target, "note": note, "args": args}
    lines = [f"action: {fn}"]
    if target:
        lines.append(f"target: {target}")
    if note:
        lines.append(f"note: {note}")
    out["display_lines"] = lines
    return out


def labels(task_run: Dict[str, Any]) -> Dict[str, Any]:
    aj = task_run.get("artifacts_json") or {}
    pack = aj.get("pack_name")
    preset = aj.get("preset") or aj.get("pack_preset")
    return {
        "pack_name": pack if isinstance(pack, str) else None,
        "preset_name": preset if isinstance(preset, str) else None,
    }


def primary_artifact(task_run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    aj = task_run.get("artifacts_json") or {}
    task_id = str(task_run.get("task_id") or "")
    conv = task_conventions(task_run.get("task_type"), artifacts_json=aj)
    name = conv.primary
    if not name:
        return None
    artifact_dir = Path(str(aj.get("artifact_dir") or (Path(".runtime") / "tasks" / task_id))).resolve()
    p = (artifact_dir / name).resolve()
    exists = artifact_dir.exists() and p.exists() and p.is_file()
    out: Dict[str, Any] = {"name": name, "exists": exists, "path": str(p)}
    if exists:
        out["preview_url"] = f"/v1/tasks/{task_id}/artifacts/{name}"
    return out


def task_meta(task_run: Dict[str, Any], steps: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    steps = steps or []
    prog = progress_meta(task_run, steps) if steps else None
    return {
        "labels": labels(task_run),
        "time": time_meta(task_run),
        "progress": prog,
        "primary_artifact": primary_artifact(task_run),
        "dataset_meta": dataset_meta(task_run),
        "approval": approval_meta(task_run),
        "artifact_conventions": {
            "primary_name": task_conventions(task_run.get("task_type"), artifacts_json=(task_run.get("artifacts_json") or {})).primary,
            "important": task_conventions(task_run.get("task_type"), artifacts_json=(task_run.get("artifacts_json") or {})).important,
        },
    }

