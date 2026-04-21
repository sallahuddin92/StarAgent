import os
import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class ToolRegistry:
    """Registry for agent tools and their OpenAI-compatible definitions."""
    
    def __init__(self):
        self.tools = {}
        self._register_default_tools()
        
    def _register_default_tools(self):
        self.register("list_files", self.list_files, {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in a directory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path"}
                    },
                    "required": ["path"]
                }
            }
        })
        
        self.register("read_file", self.read_file, {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the content of a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"}
                    },
                    "required": ["path"]
                }
            }
        })

        self.register("search_files", self.search_files, {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": "Search for a pattern in files",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "path": {"type": "string", "description": "Root path to search"}
                    },
                    "required": ["query", "path"]
                }
            }
        })

        self.register("write_file", self.write_file, {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write or overwrite content in a file. RISKY: Requires matching verification.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"},
                        "content": {"type": "string", "description": "The full content to write"}
                    },
                    "required": ["path", "content"]
                }
            }
        })

    def register(self, name: str, handler: Any, definition: Dict[str, Any]):
        self.tools[name] = {"handler": handler, "definition": definition}

    def get_definitions(self) -> List[Dict[str, Any]]:
        return [t["definition"] for t in self.tools.values()]

    def list_files(self, path: str = ".") -> str:
        try:
            items = os.listdir(path)
            return json.dumps(items)
        except Exception as e:
            return f"Error: {str(e)}"

    def read_file(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"Error: {str(e)}"

    def search_files(self, query: str, path: str = ".") -> str:
        results = []
        try:
            import subprocess
            cmd = ["grep", "-rn", query, path]
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
            return output
        except subprocess.CalledProcessError:
            return "No matches found."
        except Exception as e:
            return f"Error: {str(e)}"

    def write_file(self, path: str, content: str) -> str:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote to {path}"
        except Exception as e:
            return f"Error: {str(e)}"
