import logging
from typing import Dict, Any, List, Optional
from .tools import ToolRegistry

logger = logging.getLogger(__name__)

class ToolExecutor:
    """Executes tool calls and returns results."""
    
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    @staticmethod
    def _normalize_tool_args(func_name: str, args: Any) -> Dict[str, Any]:
        """
        Normalize parsed JSON args into a mapping accepted by handler(**args).

        Supports list-style legacy/planner outputs:
        - run_command(["python3", "path/main.py"]) -> {"command":"python3 path/main.py","cwd":"."}
        - run_command(["python3 main.py", "scratch/app"]) -> {"command":"python3 main.py","cwd":"scratch/app"}
        - create_directory(["scratch/app"]) -> {"path":"scratch/app"}
        - list_files(["scratch/app"]) -> {"path":"scratch/app"}
        - write_file(["path.py","content"]) -> {"path":"path.py","content":"content"}
        """
        if args is None:
            return {}
        if isinstance(args, dict):
            return args

        if not isinstance(args, list):
            raise ValueError(
                f"Invalid args type for {func_name}; expected mapping or list, got {type(args).__name__}"
            )

        if func_name == "run_command":
            if len(args) == 0:
                raise ValueError(
                    "Invalid list args for run_command; expected [command_tokens...] or [command, cwd]"
                )
            if not all(isinstance(x, str) for x in args):
                raise ValueError(
                    "Invalid list args for run_command; expected string items"
                )
            # Explicit [command, cwd] form when first arg already contains command+args.
            if len(args) == 2 and " " in args[0]:
                return {"command": args[0], "cwd": args[1]}
            return {"command": " ".join(args).strip(), "cwd": "."}

        if func_name in {"create_directory", "list_files"}:
            if len(args) == 1 and isinstance(args[0], str):
                return {"path": args[0]}
            raise ValueError(
                f"Invalid list args for {func_name}; expected [path]"
            )

        if func_name == "write_file":
            if len(args) == 2 and all(isinstance(x, str) for x in args):
                return {"path": args[0], "content": args[1]}
            raise ValueError(
                "Invalid list args for write_file; expected [path, content]"
            )

        raise ValueError(
            f"Invalid list args for {func_name}; expected mapping arguments"
        )
        
    def execute_tool_call(
        self, 
        tool_call: Any, 
        tool_name: Optional[str] = None, 
        arguments: Optional[Dict[str, Any]] = None, 
        context: Any = None,
        *args,
        **kwargs
    ) -> Any:
        """
        Execute a tool call. Supports both modern async Dict signature and
        legacy sync positional signature (project_id, tool_name, arguments, context).
        """
        if tool_name is not None or isinstance(tool_call, str):
            project_id = tool_call
            return self._execute_tool_call_sync_legacy(project_id, tool_name, arguments, context)
        else:
            return self._execute_tool_call_async_modern(tool_call)

    def _execute_tool_call_sync_legacy(
        self,
        project_id: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]],
        context: Any
    ) -> Dict[str, Any]:
        logger.info(f"Legacy sync tool call: {tool_name} with args: {arguments}")
        if arguments is None:
            arguments = {}
            
        # Map legacy directory to path if present
        if "directory" in arguments:
            arguments = {"path": arguments["directory"], **{k: v for k, v in arguments.items() if k != "directory"}}

        import os
        import json

        def resolve_path(p: str) -> str:
            base = getattr(self.registry, "search_backend", None)
            if base and isinstance(base, str) and os.path.isdir(base):
                if os.path.isabs(p):
                    return p
                return os.path.abspath(os.path.join(base, p))
            return os.path.abspath(p)

        action_id = arguments.get("action_id")

        # Special lister implementation for list_files legacy calls
        if tool_name == "list_files":
            path_arg = arguments.get("path") or "."
            target_dir = resolve_path(path_arg)
            found_files = []
            try:
                for root, dirs, files in os.walk(target_dir):
                    for f in files:
                        rel_p = os.path.relpath(os.path.join(root, f), target_dir)
                        found_files.append(rel_p)
                        if path_arg != ".":
                            found_files.append(os.path.join(path_arg, rel_p))
                return {"success": True, "output": json.dumps(list(set(found_files)))}
            except Exception as e:
                return {"success": False, "output": f"Error: {str(e)}"}
            
        if tool_name == "search_memory_or_files":
            query = arguments.get("query") or ""
            db = None
            for attr in ["web_researcher", "db", "search_backend"]:
                val = getattr(self.registry, attr, None)
                if val and hasattr(val, "get_memory_items"):
                    db = val
                    break
            if db:
                items = db.get_memory_items(conversation_id=None, project_id=project_id)
                matching = [item.content for item in items if query.lower() in item.content.lower()]
                output_str = "\n".join(matching)
                return {
                    "success": True,
                    "output": output_str,
                    "metadata": {"project_id": project_id}
                }
            else:
                return {"success": False, "output": "Database not available for memory search."}

        if tool_name == "write_file":
            path_arg = arguments.get("path")
            content_arg = arguments.get("content") or ""
            target_path = resolve_path(path_arg)
            
            original_content = None
            if os.path.exists(target_path):
                try:
                    with open(target_path, "r", encoding="utf-8") as f:
                        original_content = f.read()
                except Exception:
                    pass
            
            try:
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(content_arg)
                
                if context is not None:
                    if not hasattr(context, "workspace_state") or context.workspace_state is None:
                        context.workspace_state = {}
                    ws = context.workspace_state
                    if "actions" not in ws:
                        ws["actions"] = {}
                    if "modified_files_by_action" not in ws:
                        ws["modified_files_by_action"] = {}
                        
                    if action_id:
                        ws["actions"][action_id] = {
                            "action_id": action_id,
                            "tool_name": "write_file",
                            "arguments": arguments,
                            "rollback_available": True,
                            "backup": {path_arg: original_content}
                        }
                        if action_id not in ws["modified_files_by_action"]:
                            ws["modified_files_by_action"][action_id] = []
                        if path_arg not in ws["modified_files_by_action"][action_id]:
                            ws["modified_files_by_action"][action_id].append(path_arg)
                
                return {"success": True, "output": f"Successfully wrote to {path_arg}"}
            except Exception as e:
                return {"success": False, "output": f"Error writing file: {str(e)}"}

        if tool_name == "edit_file":
            path_arg = arguments.get("path")
            find_str = arguments.get("find") or ""
            replace_str = arguments.get("replace") or ""
            target_path = resolve_path(path_arg)
            
            if not os.path.exists(target_path):
                return {"success": False, "output": f"Error: File not found at {path_arg}"}
                
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    original_content = f.read()
                
                if find_str not in original_content:
                    return {"success": False, "output": f"Error: string '{find_str}' not found in file."}
                    
                new_content = original_content.replace(find_str, replace_str)
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                    
                if context is not None:
                    if not hasattr(context, "workspace_state") or context.workspace_state is None:
                        context.workspace_state = {}
                    ws = context.workspace_state
                    if "actions" not in ws:
                        ws["actions"] = {}
                    if "modified_files_by_action" not in ws:
                        ws["modified_files_by_action"] = {}
                        
                    if action_id:
                        ws["actions"][action_id] = {
                            "action_id": action_id,
                            "tool_name": "edit_file",
                            "arguments": arguments,
                            "rollback_available": True,
                            "backup": {path_arg: original_content}
                        }
                        if action_id not in ws["modified_files_by_action"]:
                            ws["modified_files_by_action"][action_id] = []
                        if path_arg not in ws["modified_files_by_action"][action_id]:
                            ws["modified_files_by_action"][action_id].append(path_arg)
                            
                return {"success": True, "output": f"Successfully edited {path_arg}"}
            except Exception as e:
                return {"success": False, "output": f"Error editing file: {str(e)}"}

        if tool_name == "rollback_last_action":
            if context is None or not hasattr(context, "workspace_state") or context.workspace_state is None:
                return {"success": False, "output": "No action state found for rollback."}
                
            ws = context.workspace_state
            actions = ws.get("actions", {})
            if action_id not in actions:
                return {"success": False, "output": f"Action {action_id} not found."}
                
            act = actions[action_id]
            if not act.get("rollback_available"):
                return {"success": False, "output": f"Rollback not available for action {action_id}."}
                
            backup = act.get("backup", {})
            try:
                for path_arg, old_content in backup.items():
                    target_path = resolve_path(path_arg)
                    if old_content is None:
                        if os.path.exists(target_path):
                            os.remove(target_path)
                    else:
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        with open(target_path, "w", encoding="utf-8") as f:
                            f.write(old_content)
                
                act["rollback_available"] = False
                return {"success": True, "output": f"Successfully rolled back action {action_id}."}
            except Exception as e:
                return {"success": False, "output": f"Error during rollback: {str(e)}"}

        if tool_name == "file_exists":
            path_arg = arguments.get("path")
            target_path = resolve_path(path_arg)
            exists = os.path.exists(target_path)
            return {"success": exists, "output": f"File exists: {exists}"}

        if tool_name == "syntax_check":
            path_arg = arguments.get("path")
            target_path = resolve_path(path_arg)
            if not os.path.exists(target_path):
                return {"success": False, "output": "File not found."}
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    source = f.read()
                compile(source, target_path, "exec")
                return {"success": True, "output": "Syntax OK"}
            except Exception as e:
                return {"success": False, "output": f"Syntax Error: {str(e)}"}

        if tool_name == "run_tests":
            command = arguments.get("command") or ""
            import subprocess
            cwd = getattr(self.registry, "search_backend", None) or "."
            if not os.path.isdir(cwd):
                cwd = "."
            try:
                res = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=cwd)
                success = (res.returncode == 0)
                return {"success": success, "output": res.stdout + res.stderr}
            except Exception as e:
                return {"success": False, "output": f"Error running tests: {str(e)}"}

        if tool_name not in self.registry.tools:
            return {"success": False, "output": f"Error: Tool '{tool_name}' not found."}
            
        handler = self.registry.tools[tool_name]["handler"]
        try:
            import asyncio
            if asyncio.iscoroutinefunction(handler):
                result = asyncio.run(handler(**arguments))
            else:
                result = handler(**arguments)
            return {"success": True, "output": str(result)}
        except Exception as e:
            return {"success": False, "output": f"Error: {str(e)}"}

    async def _execute_tool_call_async_modern(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single tool call from the LLM."""
        call_id = tool_call.get("id")
        func_info = tool_call.get("function", {})
        func_name = func_info.get("name")
        import json
        try:
            raw_args = json.loads(func_info.get("arguments", "{}"))
        except Exception:
            args = {}
        else:
            try:
                args = self._normalize_tool_args(func_name, raw_args)
            except ValueError as ve:
                return {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "tool_name": func_name,
                    "content": f"Error: {ve}",
                }
            
        # GUARD: check for generic or invalid paths
        paths_to_check = []
        if isinstance(args, dict):
            if "path" in args and isinstance(args["path"], str):
                paths_to_check.append(args["path"])
            if "paths" in args and isinstance(args["paths"], list):
                for p in args["paths"]:
                    if isinstance(p, str):
                        paths_to_check.append(p)
        
        blocked = False
        blocked_reason = ""
        import os
        from pathlib import Path
        for p_str in paths_to_check:
            p_clean = p_str.strip().lower()
            if p_clean in {"the", "workspace", "project", "repo"}:
                blocked = True
                blocked_reason = f"Generic relative path '{p_str}' is blocked."
                break
            resolved_abs = os.path.abspath(p_str)
            workspace_abs = os.path.abspath(Path.cwd())
            try:
                is_under = os.path.commonpath([workspace_abs, resolved_abs]) == workspace_abs
            except ValueError:
                is_under = False
            if not is_under:
                blocked = True
                blocked_reason = f"Path '{p_str}' resolves to '{resolved_abs}', which is outside the workspace root."
                break
        
        if blocked:
            logger.warning(f"[GUARD] Blocked tool execution for {func_name} due to: {blocked_reason}")
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "tool_name": func_name,
                "content": f"[GUARD] Blocked invalid or generic path: {blocked_reason}"
            }

        logger.info(f"Executing tool: {func_name} with args: {args}")
        
        if func_name not in self.registry.tools:
            result = f"Error: Tool '{func_name}' not found."
        else:
            handler = self.registry.tools[func_name]["handler"]
            try:
                import asyncio
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(**args)
                else:
                    result = handler(**args)
            except Exception as e:
                logger.error(f"Error executing tool {func_name}: {e}")
                result = f"Error: {str(e)}"
                
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "tool_name": func_name,
            "content": str(result)
        }
