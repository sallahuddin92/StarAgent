from __future__ import annotations

import os
import sys
import time
import json
import httpx
from pathlib import Path
from typing import Dict, Any, List, Optional
import select
import tty
import termios
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.columns import Columns

from client.macagent_client import MacAgentClient

def get_key_nonblocking() -> str:
    if not sys.stdin.isatty():
        return ""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
        if rlist:
            key = sys.stdin.read(1)
            if key == '\x1b':
                rlist2, _, _ = select.select([sys.stdin], [], [], 0.05)
                if rlist2:
                    key += sys.stdin.read(2)
            return key
        return ""
    except Exception:
        return ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

def run_quick_doctor_checks(client: MacAgentClient) -> Dict[str, Any]:
    status = {
        "server": "Offline",
        "server_color": "red",
        "provider": "Unknown",
        "model": "Unknown",
        "traces": "Unknown",
        "traces_color": "yellow",
        "routes": "Unknown",
        "routes_color": "yellow",
    }
    
    try:
        health_payload = client.health(timeout=1.0)
        if health_payload.get("ok", False) or health_payload.get("status") == "ok":
            status["server"] = "Online"
            status["server_color"] = "green"
    except Exception:
        status["server"] = "Offline"
        status["server_color"] = "red"
        return status
        
    try:
        models_data = client.models()
        default_model = models_data.get("default") or "Unknown"
        status["model"] = default_model
        
        if "llama" in default_model.lower() or os.getenv("GROQ_API_KEY"):
            status["provider"] = "Groq"
        elif "ollama" in default_model.lower():
            status["provider"] = "Ollama"
        else:
            status["provider"] = "Ollama"
    except Exception:
        pass
        
    try:
        trace_dir = Path(os.getcwd()) / ".runtime" / "traces"
        if trace_dir.exists():
            status["traces"] = "Writable"
            status["traces_color"] = "green"
        else:
            status["traces"] = "Missing"
            status["traces_color"] = "red"
    except Exception:
        status["traces"] = "Error"
        status["traces_color"] = "red"
        
    try:
        r = client._http.get(f"{client.root_base_url}/openapi.json", timeout=1.0)
        r.raise_for_status()
        paths = (r.json() or {}).get("paths", {})
        required_paths = ["/v1/docs/ingest", "/v1/docs/search", "/v1/docs/ask"]
        missing = [p for p in required_paths if p not in paths]
        if not missing:
            status["routes"] = "Ready"
            status["routes_color"] = "green"
        else:
            status["routes"] = f"Missing: {len(missing)}"
            status["routes_color"] = "red"
    except Exception:
        status["routes"] = "Error"
        status["routes_color"] = "red"
        
    return status

def make_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=4),
        Layout(name="doctor_bar", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=2),
    )
    return layout

def draw_header() -> Panel:
    text = Text.assemble(
        ("STARAGENT OPERATOR CONSOLE  ", "bold white"),
        ("|  Local Time: ", "dim"),
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "bold cyan"),
        ("  |  System Status Dashboard", "dim"),
    )
    return Panel(text, style="white on blue")

def draw_doctor_bar(status: Dict[str, Any]) -> Panel:
    text = Text.assemble(
        (" Server: ", "bold"),
        (f" {status['server']} ", f"bold white on {status['server_color']}"),
        ("  |  Provider: ", "bold"),
        (f" {status['provider']} ", "bold magenta"),
        ("  |  Model: ", "bold"),
        (f" {status['model']} ", "bold cyan"),
        ("  |  Traces: ", "bold"),
        (f" {status['traces']} ", f"bold white on {status['traces_color']}"),
        ("  |  Routes: ", "bold"),
        (f" {status['routes']} ", f"bold white on {status['routes_color']}"),
    )
    return Panel(text, title="Diagnostics Quick Check", border_style="cyan")

def draw_runs_list(runs: List[Dict[str, Any]], selected_idx: int) -> Panel:
    table = Table(expand=True, show_edge=False)
    table.add_column("S", width=2, justify="center")
    table.add_column("Run ID", style="cyan")
    table.add_column("Workflow", style="magenta")
    table.add_column("Status")
    table.add_column("Stage")

    for idx, run in enumerate(runs):
        is_sel = idx == selected_idx
        sel_marker = ">" if is_sel else " "
        
        status = run.get("status") or ""
        if status == "completed":
            status_str = "[green]completed[/green]"
        elif status == "failed":
            status_str = "[red]failed[/red]"
        elif status == "paused":
            status_str = "[yellow]paused[/yellow]"
        elif status == "running":
            status_str = "[cyan]running[/cyan]"
        else:
            status_str = status
            
        row_style = "bold white on blue" if is_sel else ""
        table.add_row(
            sel_marker,
            run.get("run_id", ""),
            run.get("workflow_name", ""),
            status_str,
            str(run.get("current_stage_index", 0)),
            style=row_style
        )
        
    return Panel(table, title="[bold]Workflow Runs[/bold]", border_style="blue")

def draw_details(run_details: Optional[Dict[str, Any]], gates_data: Optional[Dict[str, Any]], checkpoints_data: Optional[Dict[str, Any]], trace_data: Optional[Dict[str, Any]], state_data: Optional[Dict[str, Any]]) -> Layout:
    l = Layout()
    l.split_column(
        Layout(name="info", size=7),
        Layout(name="middle"),
    )
    l["middle"].split_row(
        Layout(name="gates", ratio=1),
        Layout(name="checkpoints", ratio=1),
    )
    
    if not run_details:
        empty_text = Text("\n\nNo workflow run selected. Use Up/Down arrow keys to navigate the runs list.", justify="center", style="dim")
        l["info"].update(Panel(empty_text, title="[bold]Workflow Details[/bold]", border_style="dim"))
        return l
        
    # Info Panel
    status = run_details.get("status", "")
    workflow_name = run_details.get("workflow_name", "")
    run_id = run_details.get("run_id", "")
    user_goal = run_details.get("user_goal", "")
    
    info_text = Text()
    info_text.append(f"Run ID: {run_id}  |  Workflow: {workflow_name}  |  Status: {status}\n", style="bold")
    info_text.append(f"Goal: {user_goal}\n", style="yellow")
    
    # Render Stages progress bar
    stages = run_details.get("stages") or []
    stages_progress = []
    for idx, s in enumerate(stages):
        s_name = s.get("stage_name") or s.get("name") or f"Stage {idx}"
        s_status = s.get("status")
        icon = "✅" if s_status == "completed" else ("⏳" if s_status == "running" else ("⚠️" if s_status == "paused" else "▫️"))
        stages_progress.append(f"{icon} {s_name}")
    
    stages_bar = "  →  ".join(stages_progress)
    info_text.append("\nStages Progress:\n", style="bold cyan")
    info_text.append(stages_bar)
    
    # Check if action required
    is_paused = status == "paused"
    action_panel = None
    if is_paused:
        # Check if the active stage requires approval
        active_stage = None
        current_idx = run_details.get("current_stage_index", 0)
        if current_idx < len(stages):
            active_stage = stages[current_idx]
            
        if active_stage and (active_stage.get("status") == "paused" or active_stage.get("approval_required")):
            info_text.append(f"\n\n🚨 ACTION REQUIRED: Stage '{active_stage.get('stage_name')}' is waiting for human approval.\nPress [A] to Approve or [J] to Reject.", style="blink bold red")
            
    l["info"].update(Panel(info_text, title=f"[bold]Run: {run_id}[/bold]", border_style="cyan"))
    
    # Gates Panel
    gates_table = Table(expand=True, show_header=True)
    gates_table.add_column("Stage", style="magenta")
    gates_table.add_column("Gate/Check", style="cyan")
    gates_table.add_column("Status")
    
    if gates_data:
        gate_results = gates_data.get("gate_results") or {}
        for stage_name, res in gate_results.items():
            for r in res.get("results", []):
                icon = "✅" if r["status"] == "pass" else ("⚠️" if r["status"] == "warning" else "❌")
                gates_table.add_row(
                    stage_name,
                    r.get("type", ""),
                    f"{icon} {r['status']}"
                )
    
    l["middle"]["gates"].update(Panel(gates_table, title="[bold]Verification Gates[/bold]", border_style="magenta"))
    
    # Checkpoints Panel
    checkpoints_table = Table(expand=True, show_header=True)
    checkpoints_table.add_column("Stage", style="magenta")
    checkpoints_table.add_column("Status", style="green")
    checkpoints_table.add_column("Created At")
    
    if checkpoints_data:
        checkpoints = checkpoints_data.get("checkpoints") or []
        for cp in checkpoints[:5]:  # limit to top 5
            checkpoints_table.add_row(
                cp.get("stage_name", ""),
                cp.get("status", ""),
                cp.get("created_at", "")[:19]
            )
            
    l["middle"]["checkpoints"].update(Panel(checkpoints_table, title="[bold]Stage Checkpoints[/bold]", border_style="green"))
    
    return l

def draw_footer() -> Panel:
    text = Text.assemble(
        ("[Q]", "bold red"), (" Quit  ", "white"),
        ("[R]", "bold yellow"), (" Refresh  ", "white"),
        ("[Up/Down]", "bold cyan"), (" Navigate Runs  ", "white"),
        ("[A]", "bold green"), (" Approve  ", "white"),
        ("[J]", "bold red"), (" Reject  ", "white"),
        ("[C]", "bold magenta"), (" Continue/Resume  ", "white"),
    )
    return Panel(text, title="Keyboard Shortcuts", border_style="white")

def run_tui_dashboard(client: MacAgentClient) -> int:
    console = Console()
    layout = make_layout()
    
    # Initial diagnostics
    doc_status = run_quick_doctor_checks(client)
    layout["header"].update(draw_header())
    layout["doctor_bar"].update(draw_doctor_bar(doc_status))
    layout["footer"].update(draw_footer())
    
    runs = []
    selected_idx = 0
    
    run_details = None
    gates_data = None
    checkpoints_data = None
    trace_data = None
    state_data = None
    
    last_query_time = 0.0
    query_interval = 2.5  # API call throttling
    
    need_detail_refresh = True
    
    with Live(layout, console=console, refresh_per_second=4, screen=True) as live:
        while True:
            current_time = time.time()
            
            # Fetch workflow runs list periodically or on demand
            if current_time - last_query_time > query_interval:
                try:
                    runs_payload = client.workflow_runs()
                    runs = runs_payload.get("runs") or []
                    doc_status = run_quick_doctor_checks(client)
                    layout["doctor_bar"].update(draw_doctor_bar(doc_status))
                except Exception:
                    pass
                last_query_time = current_time
                
            # Bounds check for selected index
            if runs:
                if selected_idx >= len(runs):
                    selected_idx = len(runs) - 1
                if selected_idx < 0:
                    selected_idx = 0
            else:
                selected_idx = 0
                
            # Draw left panel
            layout["main"]["left"].update(draw_runs_list(runs, selected_idx))
            
            # Draw right panel
            if runs and (need_detail_refresh or not run_details):
                selected_run = runs[selected_idx]
                run_id = selected_run.get("run_id")
                try:
                    run_details = client.workflow_run_status(run_id)
                    gates_data = client.workflow_run_gates(run_id)
                    checkpoints_data = client.workflow_checkpoints(run_id)
                    trace_data = client.workflow_run_trace(run_id)
                    state_data = client.workflow_run_state(run_id)
                except Exception:
                    pass
                need_detail_refresh = False
                
            if runs:
                layout["main"]["right"].update(draw_details(run_details, gates_data, checkpoints_data, trace_data, state_data))
            else:
                layout["main"]["right"].update(draw_details(None, None, None, None, None))
                
            # Update time in header
            layout["header"].update(draw_header())
            
            # Non-blocking keyboard input read
            key = get_key_nonblocking()
            if not key:
                continue
                
            if key.lower() == 'q':
                break
            elif key.lower() == 'r':
                last_query_time = 0.0  # Force runs refresh
                need_detail_refresh = True
            elif key == '\x1b[A':  # Up arrow
                if selected_idx > 0:
                    selected_idx -= 1
                    need_detail_refresh = True
            elif key == '\x1b[B':  # Down arrow
                if selected_idx < len(runs) - 1:
                    selected_idx += 1
                    need_detail_refresh = True
            elif key.lower() == 'a':
                if runs:
                    selected_run = runs[selected_idx]
                    run_id = selected_run.get("run_id")
                    try:
                        client.workflow_run_approve(run_id)
                        last_query_time = 0.0
                        need_detail_refresh = True
                    except Exception:
                        pass
            elif key.lower() == 'j':
                if runs:
                    selected_run = runs[selected_idx]
                    run_id = selected_run.get("run_id")
                    try:
                        client.workflow_run_reject(run_id)
                        last_query_time = 0.0
                        need_detail_refresh = True
                    except Exception:
                        pass
            elif key.lower() == 'c':
                if runs:
                    selected_run = runs[selected_idx]
                    run_id = selected_run.get("run_id")
                    try:
                        client.workflow_resume(run_id)
                        last_query_time = 0.0
                        need_detail_refresh = True
                    except Exception:
                        pass
                        
    return 0
