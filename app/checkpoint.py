import os
import json
import shutil
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

def _checkpoint_dir(task_id: str, stage_index: int, stage_name: str) -> Path:
    # Example: .runtime/tasks/task123/checkpoints/01_inspect/
    return Path(".runtime") / "tasks" / task_id / "checkpoints" / f"{stage_index:02d}_{stage_name}"

def save_stage_checkpoint(
    task_id: str,
    workflow_name: str,
    stage_name: str,
    stage_index: int,
    status: str,
    variables: Dict[str, Any],
    files_produced: List[str],
    trace_data: Dict[str, Any],
    report_content: str
) -> Path:
    """
    Saves a complete snapshot of the stage execution.
    """
    cp_dir = _checkpoint_dir(task_id, stage_index, stage_name)
    cp_dir.mkdir(parents=True, exist_ok=True)

    # 1. Save checkpoint.json
    cp_info = {
        "task_id": task_id,
        "workflow_name": workflow_name,
        "stage_name": stage_name,
        "stage_index": stage_index,
        "status": status,
        "variables": variables,
        "files_produced": files_produced,
        "created_at": datetime.utcnow().isoformat()
    }
    (cp_dir / "checkpoint.json").write_text(json.dumps(cp_info, indent=2), encoding="utf-8")

    # 2. Save trace.json
    (cp_dir / "trace.json").write_text(json.dumps(trace_data, indent=2), encoding="utf-8")

    # 3. Save report.md
    (cp_dir / "report.md").write_text(report_content or f"# Stage Report: {stage_name}\nStatus: {status}\n", encoding="utf-8")

    # 4. Save artifacts
    artifacts_dir = cp_dir / "artifacts"
    if artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    for fp in files_produced:
        src = Path(fp)
        if src.exists() and src.is_file():
            try:
                shutil.copy(src, artifacts_dir / src.name)
            except Exception as e:
                logger.error(f"Failed to copy artifact {fp} to checkpoint: {e}")

    logger.info(f"Saved checkpoint for stage '{stage_name}' under {cp_dir}")
    return cp_dir

def load_stage_checkpoint(task_id: str, stage_name: str) -> Optional[Dict[str, Any]]:
    """
    Load a stage checkpoint by name.
    """
    checkpoints_dir = Path(".runtime") / "tasks" / task_id / "checkpoints"
    if not checkpoints_dir.exists():
        return None

    # Search for matching subdirectory
    for cp_path in sorted(checkpoints_dir.glob("*")):
        if cp_path.is_dir() and cp_path.name.endswith(f"_{stage_name}"):
            json_file = cp_path / "checkpoint.json"
            if json_file.exists():
                try:
                    return json.loads(json_file.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.error(f"Failed to read checkpoint.json at {json_file}: {e}")
    return None

def list_task_checkpoints(task_id: str) -> List[Dict[str, Any]]:
    """
    Lists all checkpoints available for a given task.
    """
    checkpoints_dir = Path(".runtime") / "tasks" / task_id / "checkpoints"
    results = []
    if not checkpoints_dir.exists():
        return results

    for cp_path in sorted(checkpoints_dir.glob("*")):
        if cp_path.is_dir():
            json_file = cp_path / "checkpoint.json"
            if json_file.exists():
                try:
                    results.append(json.loads(json_file.read_text(encoding="utf-8")))
                except Exception:
                    pass
    return results
