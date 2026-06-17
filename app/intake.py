from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class IntakeResult:
    input_type: str
    recommended_strategy: str
    warnings: list[str]
    estimated_work_units: int
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_type": self.input_type,
            "recommended_strategy": self.recommended_strategy,
            "warnings": self.warnings,
            "estimated_work_units": self.estimated_work_units,
            "details": self.details,
        }


def _safe_stat(p: Path) -> Optional[os.stat_result]:
    try:
        return p.stat()
    except Exception:
        return None


def _repo_metadata(root: Path) -> Dict[str, Any]:
    meta = {
        "is_repo": False,
        "language": "unknown",
        "package_manager": "unknown",
        "test_command": "unknown",
        "app_entry_points": [],
        "available_docs": []
    }
    
    if (root / ".git").exists():
        meta["is_repo"] = True

    # Detect package manager & language
    if (root / "package.json").exists():
        meta["is_repo"] = True
        meta["language"] = "JavaScript/TypeScript"
        meta["package_manager"] = "npm/yarn"
        meta["test_command"] = "npm test"
        if (root / "index.js").exists(): meta["app_entry_points"].append("index.js")
        if (root / "src/index.js").exists(): meta["app_entry_points"].append("src/index.js")
    
    if (root / "requirements.txt").exists() or (root / "setup.py").exists() or (root / "pyproject.toml").exists():
        meta["is_repo"] = True
        meta["language"] = "Python"
        meta["package_manager"] = "pip/poetry"
        meta["test_command"] = "pytest"
        if (root / "main.py").exists(): meta["app_entry_points"].append("main.py")
        if (root / "app.py").exists(): meta["app_entry_points"].append("app.py")

    if (root / "Cargo.toml").exists():
        meta["is_repo"] = True
        meta["language"] = "Rust"
        meta["package_manager"] = "cargo"
        meta["test_command"] = "cargo test"
        if (root / "src/main.rs").exists(): meta["app_entry_points"].append("src/main.rs")

    if (root / "go.mod").exists():
        meta["is_repo"] = True
        meta["language"] = "Go"
        meta["package_manager"] = "go modules"
        meta["test_command"] = "go test"
        if (root / "main.go").exists(): meta["app_entry_points"].append("main.go")

    # Docs
    if (root / "README.md").exists():
        meta["available_docs"].append("README.md")
    if (root / "docs").exists() and (root / "docs").is_dir():
        meta["available_docs"].append("docs/")

    return meta


def _classify_json_file(path: Path, *, sample_bytes: int = 512 * 1024) -> Dict[str, Any]:
    """
    Very lightweight JSON shape probe. Never reads the full file.
    Returns details that can be used to decide whether to run dataset mode.
    """
    st = _safe_stat(path)
    size = int(st.st_size) if st else None
    kind = "unknown"
    sample_ok = False
    sample_error = None

    try:
        with path.open("rb") as f:
            raw = f.read(sample_bytes)
        text = raw.decode("utf-8", errors="replace").lstrip()
        if text.startswith("{"):
            kind = "object_or_ndjson"
        elif text.startswith("["):
            kind = "array"
        else:
            kind = "unknown"

        # Try strict JSON parse on the sample; if it succeeds, we can disambiguate.
        try:
            obj = json.loads(text)
            sample_ok = True
            if isinstance(obj, list):
                kind = "array"
            elif isinstance(obj, dict):
                kind = "object"
        except Exception as e:
            sample_error = str(e)
            # Best-effort NDJSON check: parse a handful of lines as individual JSON objects.
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            ok = 0
            checked = 0
            for ln in lines[:30]:
                checked += 1
                try:
                    json.loads(ln)
                    ok += 1
                except Exception:
                    pass
            if checked >= 5 and ok >= max(4, int(checked * 0.75)):
                kind = "ndjson"
    except Exception as e:
        sample_error = str(e)

    return {
        "size_bytes": size,
        "json_kind_probe": kind,
        "sample_parse_ok": sample_ok,
        "sample_error": sample_error,
    }


def classify_input(
    path: str,
    *,
    max_files: int = 1500,
    large_file_bytes: int = 25 * 1024 * 1024,
    json_dataset_min_bytes: int = 1 * 1024 * 1024,
    dominance_ratio: float = 0.85,
) -> IntakeResult:
    """
    Adaptive intake / preflight classification.

    This must remain fast and safe:
    - avoid reading full file contents
    - bounded directory scanning
    """
    warnings: list[str] = []
    details: Dict[str, Any] = {"path": path}

    p = Path(path).expanduser()
    # Keep the resolved path for operator clarity, but do not fail classification if resolution fails.
    try:
        details["resolved_path"] = str(p.resolve())
    except Exception:
        details["resolved_path"] = str(p)

    if p.is_file():
        st = _safe_stat(p)
        size = int(st.st_size) if st else None
        details["is_file"] = True
        details["size_bytes"] = size
        ext = p.suffix.lower()
        details["ext"] = ext
        if ext in (".json", ".jsonl", ".ndjson"):
            probe = _classify_json_file(p)
            details["json_probe"] = probe
            if size and size >= large_file_bytes:
                warnings.append(f"Large JSON file detected ({size} bytes). Dataset mode recommended.")
            return IntakeResult(
                input_type="json_dataset",
                recommended_strategy="json_dataset_mode",
                warnings=warnings,
                estimated_work_units=1,
                details=details,
            )
        return IntakeResult(
            input_type="unknown",
            recommended_strategy="unknown",
            warnings=warnings,
            estimated_work_units=1,
            details=details,
        )

    if not p.exists() or not p.is_dir():
        warnings.append("Path not found or not a directory.")
        details["exists"] = False
        return IntakeResult(
            input_type="unknown",
            recommended_strategy="unknown",
            warnings=warnings,
            estimated_work_units=0,
            details=details,
        )

    details["is_dir"] = True
    
    repo_meta = _repo_metadata(p)
    details["looks_like_repo"] = repo_meta["is_repo"]
    details["repo_metadata"] = repo_meta
    details["risk_level"] = "low" # Default risk level

    file_count = 0
    total_bytes = 0
    largest: Optional[Dict[str, Any]] = None
    ext_counts: Dict[str, int] = {}
    json_files: list[Dict[str, Any]] = []

    # Bounded scan: stat only.
    try:
        for root, dirs, files in os.walk(p):
            # Skip dot folders and .git quickly.
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != ".git"]
            for fn in files:
                if fn.startswith("."):
                    continue
                fp = Path(root) / fn
                st = _safe_stat(fp)
                if not st:
                    continue
                size = int(st.st_size)
                file_count += 1
                total_bytes += size
                ext = fp.suffix.lower()
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
                rel = None
                try:
                    rel = str(fp.relative_to(p))
                except Exception:
                    rel = str(fp)
                if largest is None or size > int(largest.get("size_bytes") or 0):
                    largest = {"rel_path": rel, "abs_path": str(fp), "size_bytes": size, "ext": ext}
                if ext in (".json", ".jsonl", ".ndjson"):
                    json_files.append({"rel_path": rel, "abs_path": str(fp), "size_bytes": size})
                if file_count >= max_files:
                    warnings.append(f"Directory scan capped at {max_files} files for intake.")
                    raise StopIteration()
    except StopIteration:
        pass

    details["file_count"] = file_count
    details["total_bytes"] = total_bytes
    details["largest_file"] = largest
    details["ext_counts"] = dict(sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0])))

    # Type heuristics.
    if details["looks_like_repo"]:
        input_type = "repo"
        strategy = "repo_audit"
    else:
        input_type = "mixed_folder"
        strategy = "docs_research"

    # JSON dataset detection: strong structured-data signals should prefer dataset mode over mixed_folder.
    #
    # Rules:
    # 1) If a directory contains exactly one file and it is JSON/JSONL/NDJSON -> json_dataset
    # 2) If a directory has a dominant JSON/JSONL/NDJSON file by bytes -> json_dataset
    #
    # Important: do not override repo classification (repos often contain JSON lockfiles).
    if input_type != "repo" and largest and largest.get("ext") in (".json", ".jsonl", ".ndjson"):
        size = int(largest.get("size_bytes") or 0)
        ratio = (size / max(1, total_bytes)) if total_bytes > 0 else 0.0

        single_file_json = (file_count == 1)
        dominant_json = (ratio >= dominance_ratio) and (size >= json_dataset_min_bytes)
        if single_file_json or dominant_json:
            input_type = "json_dataset"
            strategy = "json_dataset_mode"
            warnings.append(
                f"JSON dataset detected (dominant file: {largest.get('rel_path')} "
                f"{size} / {total_bytes} bytes; ratio={ratio:.2f}). Dataset mode recommended."
            )
            # Include probe for the dominant file (bounded read).
            try:
                probe = _classify_json_file(Path(largest["abs_path"]))
                details["dominant_json_probe"] = probe
            except Exception:
                pass
            details["dominant_file"] = largest

    # If not a JSON dataset, try to classify as docs/logs for operator UX.
    if input_type not in ("repo", "json_dataset"):
        docs_exts = {".md", ".txt", ".rst"}
        log_exts = {".log"}
        docs = sum(ext_counts.get(e, 0) for e in docs_exts)
        logs = sum(ext_counts.get(e, 0) for e in log_exts)
        if docs >= max(3, int(file_count * 0.6)):
            input_type = "docs_folder"
            strategy = "docs_research"
        elif logs >= max(3, int(file_count * 0.6)):
            input_type = "logs_bundle"
            strategy = "issue_triage"

    est_units = file_count if input_type in ("repo", "docs_folder", "mixed_folder", "logs_bundle") else 1
    if largest and int(largest.get("size_bytes") or 0) >= large_file_bytes:
        warnings.append(f"Large file present: {largest.get('rel_path')} ({largest.get('size_bytes')} bytes).")

    return IntakeResult(
        input_type=input_type,
        recommended_strategy=strategy,
        warnings=warnings,
        estimated_work_units=int(est_units),
        details=details,
    )
