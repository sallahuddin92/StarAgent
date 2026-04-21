import os
import glob
import time
import json

HISTORY_PATH = os.path.expanduser("~/Library/Application Support/Code/User/History")

SIGNATURES = {
    "main.py": "from fastapi import FastAPI",
    "agent.py": "class AgentLoop",
    "executor.py": "class Executor",
    "planner.py": "class Planner",
    "models.py": "class MemoryState",
    "database.py": "class DatabaseManager",
    "routing.py": "def determine_execution_route",
    "workspace_state.py": "class WorkspaceTracker",
    "tools.py": "class ToolRegistry",
    "tool_executor.py": "class ToolExecutor",
    "tool_schemas.py": "class ChatCompletionRequest",
    "tokenbudget.py": "class TokenBudgetManager",
    "retrieval.py": "class SemanticRetriever",
    "memory.py": "class MemoryStore",
    "approval.py": "class ApprovalPolicy",
    "reflection.py": "class ReflectionLayer",
    "prompting.py": "class MemoryCompactor",
    "utils.py": "def _content_to_text",
}

def find_latest_file(signature):
    files = []
    # Scan all py files in history
    search_path = os.path.join(HISTORY_PATH, "**", "*.py")
    for f in glob.iglob(search_path, recursive=True):
        try:
            with open(f, 'r', encoding='utf-8', errors='ignore') as file:
                content = file.read() # Read FULL file
                if signature in content:
                    files.append((f, os.path.getmtime(f)))
        except:
            continue
    
    if not files:
        return None
    
    # Sort by mtime descending
    files.sort(key=lambda x: x[1], reverse=True)
    return files[0][0]

def main():
    print("--- RECOVERY TOOL V2: STARTING THOROUGH SCAN ---")
    results = {}
    for filename, sig in SIGNATURES.items():
        print(f"Searching for {filename} (sig: '{sig}')...")
        match = find_latest_file(sig)
        if match:
            print(f"  FOUND: {match}")
            results[filename] = match
        else:
            print(f"  NOT FOUND")
            
    # Also find a version of main.py that includes AgentLoop integration
    print("Searching for INTEGRATED main.py (sig: 'AgentLoop(planner, executor)')...")
    integrated_main = find_latest_file("AgentLoop(planner, executor)")
    if integrated_main:
        print(f"  FOUND INTEGRATED MAIN: {integrated_main}")
        results["main_integrated.py"] = integrated_main

    print("\n--- RECOVERY MAPPING ---")
    print(json.dumps(results, indent=2))
    
    with open("recovery_mapping.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
