import logging
import json
from typing import List, Dict, Any, Optional
import re
import httpx
from .models import MemoryState
from .workspace_state import WorkspaceTracker
from .llm_client import LLMClient

logger = logging.getLogger(__name__)

class Planner:
    """Creates and revises multi-step execution plans."""
    
    def __init__(self, llm_client: LLMClient, *args, **kwargs):
        self.llm = llm_client

    async def create_plan(
        self, 
        user_input: str, 
        memory: MemoryState, 
        workspace: WorkspaceTracker
    ) -> List[str]:
        """Generate an autonomous research plan using the configured LLM."""
        logger.info(f"Generating autonomous LLM plan for: {user_input}")
        
        lower = (user_input or "").lower()
        
        # 1. SPECIAL CASE BYPASSES (for low-latency utility commands)
        if any(kw in lower for kw in ("check current location", "where am i")):
            return ["Determine my current physical location"]
        
        if any(kw in lower for kw in ("index folder", "add document")):
            m_path = re.search(r"(/[a-zA-Z0-9_./-]+|app|docs|sandbox_test)", lower)
            if m_path: return [f"Index local folder at {m_path.group(1)}"]

        # Extract context from memory
        context = ""
        # Safely try to get history from archive_turns
        history = getattr(memory, "archive_turns", [])
        if history:
            # INCREASED DEPTH: Get last 6 turns to ensure we capture the root topic
            last_msgs = history[-6:] 
            context_parts = []
            for m in last_msgs:
                role = m.get("role", "unknown")
                content = str(m.get("content", ""))[:300] # Slightly more content per msg
                context_parts.append(f"{role}: {content}")
            context = "\n".join(context_parts)

        # 2. GPT-GRADE AUTONOMOUS NEURAL PLANNING
        prompt = f"""
        You are the Planning Brain of StarAgent.
        
        CONVERSATION CONTEXT:
        {context}
        ---
        CURRENT GOAL: "{user_input}"
        
        CRITICAL INSTRUCTIONS:
        - **English Only**: All tool calls and plan steps MUST be in English.
        - **No Markdown Formatting**: Do not use bold (**) or headers (###).
        - **Flat List Only**: Provide exactly one instruction per line.
        - **Pure Tool Language**: Use the capability names directly (e.g., staragent_deep_research(query)).
        - **No Meta-Chatter**: Do not explain your plan. Just output the steps.
        - **Goal Dominance**: Focus on the CURRENT GOAL.
        
        Available Capabilities:
        - read_file(path): Read a local file. MANDATORY if the goal mentions a specific file path (e.g. scratch/test.py).
        - read_multiple_files(paths): Read several files at once.
        - staragent_docs_search(query): Mandatory for project-specific APIs or internal docs. Use this BEFORE web search if the topic is likely local or recent.
        - staragent_deep_research(query): Mandatory for general technical tasks.
        - web_search(query): Real-time news/weather.
        - write_file(path, content): Save code. Note: content MUST be the FULL script, not just a snippet. DO NOT use placeholders like [FOUND_KEY]. Use actual values found in research.
        - create_directory(path): Setup folders.
        - run_command(command): Execute/Verify.
        - list_files(path): Inspect folders.

        PLANNING STRATEGY:
        - LOCAL PATH RULE: If the goal mentions a local path, use `read_file` as the first step.
        - FOR CODE INSPECTION/AUDITS: Use `read_file` or `read_multiple_files` first.
        - FOR SIMPLE CODING TASKS: 
            - DO NOT use web research or docs search.
            - ONLY use `write_file(path, content)`, then `run_command(...)`, then `Analyze & Synthesize`.
            - Simple script prompt -> Use a single file like scratch/solution.py
        - FOR FRAMEWORK/BACKEND TASKS: -> Create project folder + app files + tests.
        - FOR FULLSTACK TASKS: -> Create backend folder + frontend folder + tests/build.
        - VERIFICATION: Never claim completed unless required files exist and verification command ran.
        - FOR COMPLEX CODING/APIs: Use `staragent_docs_search` or `staragent_deep_research` ONLY IF the task mentions an unfamiliar/new API, docs, latest version, or error recovery.
        - A plan for a 'build' goal WITHOUT a `write_file` step is a FAILURE.
        - NEVER omit `write_file` if the user mentions 'create', 'build', or 'write'.
        - Max 10 steps total for complex plans.







        OUTPUT FORMAT EXAMPLE:
        staragent_deep_research(how to build x)
        create_directory(src)
        write_file(src/main.py, """
        print("hello")
        """)
        run_command(python src/main.py)
        Analyze & Synthesize

        PLAN FOR: "{user_input}"



        """
        
        try:
            plan_text = await self.llm.text(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            
            # BLOCK-AWARE PARSING: Extract tools and their multiline contents
            steps = []
            current_block = []
            paren_count = 0
            
            for line in plan_text.split("\n"):
                line = line.strip()
                if not line: continue
                
                # Check for starts of tool calls (with possible prefixes)
                clean_line = re.sub(r"^(?:[\d.*\s-]|tool_code:\s*)+", "", line).strip()
                
                # Handle continuing multiline block
                if current_block:
                    current_block.append(line)
                    # Update parenthesis count
                    paren_count += line.count("(") - line.count(")")
                    if paren_count <= 0 or (line.strip() == ")" and paren_count <= 1):
                        steps.append("\n".join(current_block))
                        current_block = []
                        paren_count = 0
                    continue
                
                # Handle new multiline write_file start (even without triple-quotes)
                if "write_file(" in clean_line:
                    paren_count = clean_line.count("(") - clean_line.count(")")
                    if paren_count > 0:
                        current_block = [clean_line]
                        continue
                
                # Regular single-line tool call detection
                if any(tool in clean_line.lower() for tool in ("search", "write", "create", "list", "run", "analyze", "read")):
                    if "(" in clean_line or "analyze" in clean_line.lower():
                        steps.append(clean_line)
            
            if not steps:
                if any(w in lower for w in ("fastapi", "backend", "endpoint", "pytest", "test", "api", "frontend", "react", "vite")):
                    return [
                        'create_directory(scratch/stream_api)',
                        'write_file(scratch/stream_api/main.py, """\nfrom fastapi import FastAPI\napp = FastAPI()\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n""")',
                        'write_file(scratch/stream_api/test_main.py, """\nfrom fastapi.testclient import TestClient\nfrom main import app\n\nclient = TestClient(app)\n\ndef test_health():\n    response = client.get("/health")\n    assert response.status_code == 200\n    assert response.json() == {"status": "ok"}\n""")',
                        'run_command("PYTHONPATH=. python3 -m pytest -q test_main.py", "scratch/stream_api")',
                        "Analyze & Synthesize"
                    ]
                elif any(w in lower for w in ("write", "create", "build", "script", "code")):
                    # Deterministic coding fallback
                    return [
                        f'write_file(scratch/solution.py, """\n# Implementation for: {user_input}\nprint("hello")\n""")',
                        "run_command(python3 scratch/solution.py)",
                        "Analyze & Synthesize"
                    ]
                return [f"Search the web for: {user_input}", "Analyze and Synthesize"]

            # Ensure 'Analyze & Synthesize' is present
            if steps and not any("analyze" in s.lower() for s in steps):
                steps.append("Analyze & Synthesize")
            
            # Log the thought process
            logger.info(f"Autonomous Plan: {steps}")
            return steps[:15]

        except Exception as e:
            logger.error(f"Autonomous planning failed (falling back to simple): {e}")
            if any(w in lower for w in ("fastapi", "backend", "endpoint", "pytest", "test", "api", "frontend", "react", "vite")):
                return [
                    'create_directory(scratch/stream_api)',
                    'write_file(scratch/stream_api/main.py, """\nfrom fastapi import FastAPI\napp = FastAPI()\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n""")',
                    'write_file(scratch/stream_api/test_main.py, """\nfrom fastapi.testclient import TestClient\nfrom main import app\n\nclient = TestClient(app)\n\ndef test_health():\n    response = client.get("/health")\n    assert response.status_code == 200\n    assert response.json() == {"status": "ok"}\n""")',
                    'run_command("PYTHONPATH=. python3 -m pytest -q test_main.py", "scratch/stream_api")',
                    "Analyze & Synthesize"
                ]
            elif any(w in lower for w in ("write", "create", "build", "script", "code")):
                return [
                    f'write_file(scratch/solution.py, """\n# Implementation for: {user_input}\nprint("hello")\n""")',
                    "run_command(python3 scratch/solution.py)",
                    "Analyze & Synthesize"
                ]
            return [f"Search the web for: {user_input}", "Analyze and Synthesize"]

    def parse_plan(self, llm_output: str) -> List[str]:
        """Extract steps from LLM text."""
        # Logic to parse numbered lists or JSON from LLM
        steps = []
        for line in llm_output.split("\n"):
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith("-")):
                steps.append(line.lstrip("0123456789.- ").strip())
        return steps or ["No clear steps identified"]
