import os
import json
import logging
import asyncio
import httpx
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

BUILTIN_TOOL_METADATA = {
    "list_files": {"provider": "local", "type": "filesystem", "read_write_destructive": "read"},
    "read_file": {"provider": "local", "type": "filesystem", "read_write_destructive": "read"},
    "search_files": {"provider": "local", "type": "filesystem", "read_write_destructive": "read"},
    "get_file_tree": {"provider": "local", "type": "filesystem", "read_write_destructive": "read"},
    "read_multiple_files": {"provider": "local", "type": "filesystem", "read_write_destructive": "read"},
    
    "write_file": {"provider": "local", "type": "filesystem", "read_write_destructive": "write", "stage_allowlist": ["execute", "plan"]},
    "create_directory": {"provider": "local", "type": "filesystem", "read_write_destructive": "write", "stage_allowlist": ["execute"]},
    "install_dependency": {"provider": "local", "type": "system", "read_write_destructive": "write", "stage_allowlist": ["execute", "verify"]},
    "run_command": {"provider": "local", "type": "shell", "read_write_destructive": "destructive", "stage_allowlist": ["execute", "verify"]},
    
    "web_search": {"provider": "local", "type": "research", "read_write_destructive": "read"},
    "search_web": {"provider": "local", "type": "research", "read_write_destructive": "read"},
    "web_research": {"provider": "local", "type": "research", "read_write_destructive": "read"},
    "staragent_deep_research": {"provider": "local", "type": "research", "read_write_destructive": "read"},
    "wikipedia_search": {"provider": "local", "type": "research", "read_write_destructive": "read"},
    "read_url_content": {"provider": "local", "type": "research", "read_write_destructive": "read"},
    "read_browser_page": {"provider": "local", "type": "research", "read_write_destructive": "read"},
    
    "get_location": {"provider": "local", "type": "knowledge", "read_write_destructive": "read"},
    "semantic_search": {"provider": "local", "type": "knowledge", "read_write_destructive": "read"},
    "search_sources": {"provider": "local", "type": "knowledge", "read_write_destructive": "read"},
    "index_folder": {"provider": "local", "type": "knowledge", "read_write_destructive": "write", "stage_allowlist": ["execute"]},
    
    "staragent_docs_search": {"provider": "local", "type": "docs", "read_write_destructive": "read"},
    "staragent_docs_ingest": {"provider": "local", "type": "docs", "read_write_destructive": "write", "stage_allowlist": ["execute"]},
    "staragent_docs_ingest_package": {"provider": "local", "type": "docs", "read_write_destructive": "write", "stage_allowlist": ["execute"]},
    "staragent_docs_ask": {"provider": "local", "type": "docs", "read_write_destructive": "read"},
}

class ToolRegistry:
    """Registry for agent tools and their OpenAI-compatible definitions."""
    
    def __init__(self, search_backend=None, web_researcher=None, web_extractor=None, source_store=None, document_processor=None):
        self.tools = {}
        self.search_backend = search_backend
        self.web_researcher = web_researcher
        self.web_extractor = web_extractor
        self.source_store = source_store
        self.document_processor = document_processor
        self._register_default_tools()
        self._register_research_tools()
        self._register_knowledge_tools()
        
        try:
            from .docs_store import DocsStore
            from .docs_search import DocsSearcher
            from .docs_ingest import DocsIngester
            self.docs_store = DocsStore()
            self.docs_searcher = DocsSearcher(self.docs_store)
            self.docs_ingester = DocsIngester(self.docs_store)
            self._register_docs_tools()
        except ImportError as e:
            logger.warning(f"Could not load docs modules: {e}")
        except Exception as e:
            logger.warning(f"Failed to initialize docs tools: {e}")
        
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

        self.register("run_command", self.run_command, {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Execute a shell command",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command to run"},
                        "cwd": {"type": "string", "description": "Working directory"}
                    },
                    "required": ["command"]
                }
            }
        })

        self.register("create_directory", self.create_directory, {
            "type": "function",
            "function": {
                "name": "create_directory",
                "description": "Create a new directory (recursive)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path to create"}
                    },
                    "required": ["path"]
                }
            }
        })

        self.register("install_dependency", self.install_dependency, {
            "type": "function",
            "function": {
                "name": "install_dependency",
                "description": "Install a package/dependency using pip, npm, etc. Used for resolving Missing Dependency errors.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "package": {"type": "string", "description": "Package name to install"},
                        "manager": {"type": "string", "description": "Package manager (pip, npm, yarn, cargo)"}
                    },
                    "required": ["package", "manager"]
                }
            }
        })


        self.register("get_file_tree", self.get_file_tree, {
            "type": "function",
            "function": {
                "name": "get_file_tree",
                "description": "Get a recursive file tree listing for a path",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Root path"},
                        "max_depth": {"type": "integer", "default": 3}
                    },
                    "required": ["path"]
                }
            }
        })

        self.register("read_multiple_files", self.read_multiple_files, {
            "type": "function",
            "function": {
                "name": "read_multiple_files",
                "description": "Read contents of multiple files at once",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}, "description": "List of file paths"}
                    },
                    "required": ["paths"]
                }
            }
        })

    def _register_research_tools(self):
        self.register("web_search", self.web_search, {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web for up-to-date information, documentation, or code examples.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query (e.g., 'remotion 4.0 frame accurate docs')"},
                        "max_results": {"type": "integer", "default": 5, "description": "Number of results to return"}
                    },
                    "required": ["query"]
                }
            }
        })
        
        self.register("web_research", self.web_research_handler, {
            "type": "function",
            "function": {
                "name": "web_research",
                "description": "Perform multi-source autonomous research and generate a grounded markdown report with citations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Topic to research"},
                        "max_results": {"type": "integer", "default": 5},
                        "max_sources": {"type": "integer", "default": 3},
                        "project_id": {"type": "string", "description": "The project ID for research storage"}
                    },
                    "required": ["query"]
                }
            }
        })
        self.register("staragent_deep_research", self.deep_research_handler, {
            "type": "function",
            "function": {
                "name": "staragent_deep_research",
                "description": "Perform high-speed web search and FULL SITE SCAN of the top 3 results to extract precise, factual data in one go.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Factual topic to research (e.g., 'Malaysia GDP 2024')"},
                        "project_id": {"type": "string", "description": "The project ID for research storage"}
                    },
                    "required": ["query"]
                }
            }
        })

        self.register("wikipedia_search", self.wikipedia_search_handler, {
            "type": "function",
            "function": {
                "name": "wikipedia_search",
                "description": "Access Wikipedia for encyclopedia historical and biographical info.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Encyclopedic topic to research"},
                        "max_results": {"type": "integer", "default": 3}
                    },
                    "required": ["query"]
                }
            }
        })

        self.register("search_web", self.web_search, {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "Search the web for up-to-date information.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query"},
                        "max_results": {"type": "integer", "default": 5}
                    },
                    "required": ["query"]
                }
            }
        })

        self.register("read_url_content", self.read_url_content_handler, {
            "type": "function",
            "function": {
                "name": "read_url_content",
                "description": "Fetch content from a URL via HTTP request.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to read content from"}
                    },
                    "required": ["url"]
                }
            }
        })

        self.register("read_browser_page", self.read_url_content_handler, {
            "type": "function",
            "function": {
                "name": "read_browser_page",
                "description": "Render a webpage in a browser and read its contents.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to read content from"}
                    },
                    "required": ["url"]
                }
            }
        })


    def _register_knowledge_tools(self):
        self.register("get_location", self.get_location_handler, {
            "type": "function",
            "function": {
                "name": "get_location",
                "description": "Get current physical location based on IP address.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        })
        self.register("semantic_search", self.semantic_search_handler, {
            "type": "function",
            "function": {
                "name": "semantic_search",
                "description": "Search local documents and research using concept-based semantic search.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Concept to search for"},
                        "limit": {"type": "integer", "default": 5}
                    },
                    "required": ["query"]
                }
            }
        })
        
        self.register("search_sources", self.search_sources_handler, {
            "type": "function",
            "function": {
                "name": "search_sources",
                "description": "Search local sources using keyword matching (FTS5).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Keywords to match"},
                        "project_id": {"type": "string", "default": "default"}
                    },
                    "required": ["query"]
                }
            }
        })

        self.register("index_folder", self.index_folder_handler, {
            "type": "function",
            "function": {
                "name": "index_folder",
                "description": "Index a local folder into semantic memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Folder path"},
                        "project_id": {"type": "string", "default": "default"}
                    },
                    "required": ["path"]
                }
            }
        })

    def _register_docs_tools(self):
        self.register("staragent_docs_search", self.docs_search_handler, {
            "type": "function",
            "function": {
                "name": "staragent_docs_search",
                "description": "Search local offline documentation knowledge base (fast and internet-free). Use this before searching the web.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "package_name": {"type": "string", "description": "Optional package name to filter (e.g. 'fastapi')"},
                        "project_id": {"type": "string", "default": "default"}
                    },
                    "required": ["query"]
                }
            }
        })
        
        self.register("staragent_docs_ingest", self.docs_ingest_handler, {
            "type": "function",
            "function": {
                "name": "staragent_docs_ingest",
                "description": "Ingest a folder containing markdown, html, or a DevDocs export into the local docs database.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Folder path to ingest"},
                        "project_id": {"type": "string", "default": "default"},
                        "source_type": {"type": "string", "default": "project_docs"}
                    },
                    "required": ["path"]
                }
            }
        })

        self.register("staragent_docs_ingest_package", self.docs_ingest_package_handler, {
            "type": "function",
            "function": {
                "name": "staragent_docs_ingest_package",
                "description": "Extract docs from an installed package using pydoc.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "package_name": {"type": "string", "description": "Name of the installed python package"},
                        "project_id": {"type": "string", "default": "default"}
                    },
                    "required": ["package_name"]
                }
            }
        })

        self.register("staragent_docs_ask", self.docs_ask_handler, {
            "type": "function",
            "function": {
                "name": "staragent_docs_ask",
                "description": "Answer a docs question strictly from project documentation evidence. Returns no answer when evidence is missing.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "Question to answer from project docs evidence"},
                        "project_id": {"type": "string", "default": "default"},
                        "package_name": {"type": "string", "description": "Optional package filter"},
                        "max_results": {"type": "integer", "default": 5}
                    },
                    "required": ["question"]
                }
            }
        })

    def register(self, name: str, handler: Any, definition: Dict[str, Any]):
        # Map built-in properties
        meta = BUILTIN_TOOL_METADATA.get(name, {
            "provider": "local",
            "type": "local",
            "read_write_destructive": "read",
            "stage_allowlist": None
        }).copy()

        metadata = {
            "name": name,
            "provider": meta.get("provider", "local"),
            "type": meta.get("type", "local"),
            "permissions": [meta.get("read_write_destructive", "read")],
            "read_write_destructive": meta.get("read_write_destructive", "read"),
            "stage_allowlist": meta.get("stage_allowlist"),
            "schemas": definition,
            "safety_policy": "default_safe"
        }

        self.tools[name] = {
            "handler": handler,
            "definition": definition,
            "metadata": metadata
        }

    def register_mcp_tool(
        self, 
        name: str, 
        handler: Any, 
        definition: Dict[str, Any], 
        server_name: str,
        read_write_destructive: str = "read",
        stage_allowlist: Optional[List[str]] = None,
        safety_policy: Optional[str] = None
    ):
        """Dynamic registration of MCP tools with equivalent metadata."""
        metadata = {
            "name": name,
            "provider": "mcp",
            "type": f"mcp:{server_name}",
            "permissions": [read_write_destructive],
            "read_write_destructive": read_write_destructive,
            "stage_allowlist": stage_allowlist,
            "schemas": definition,
            "safety_policy": safety_policy or "default_safe"
        }
        self.tools[name] = {
            "handler": handler,
            "definition": definition,
            "metadata": metadata
        }

    async def get_location_handler(self) -> str:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get("http://ip-api.com/json")
                if r.status_code == 200:
                    data = r.json()
                    return f"LOCATION: {data.get('city')}, {data.get('regionName')}, {data.get('country')}\nCOORDS: {data.get('lat')}, {data.get('lon')}\nISP: {data.get('isp')}"
                return "Error: Could not retrieve location data."
        except Exception as e:
            return f"Geolocation error: {str(e)}"

    async def semantic_search_handler(self, query: str, limit: int = 5) -> str:
        if not self.source_store: return "Error: Source store not initialized"
        results = await self.source_store.semantic_search(query, limit=limit)
        if not results: return "No semantic matches found in local knowledge base."
        
        formatted = []
        for r in results:
            formatted.append(f"CONCEPT MATCH: {r['metadata'].get('title', 'Unknown')}\nDOC: {r['document'][:500]}...\n---")
        return "\n".join(formatted)

    def search_sources_handler(self, query: str, project_id: str = "default") -> str:
        if not self.source_store: return "Error: Source store not initialized"
        results = self.source_store.search_sources(project_id, query)
        if not results: return "No keyword matches found in local sources."
        
        formatted = []
        for r in results:
            formatted.append(f"KEYWORD MATCH: {r['title']}\nCONTENT: {r['content'][:500]}...\n---")
        return "\n".join(formatted)

    async def index_folder_handler(self, path: str, project_id: str = "default") -> str:
        if not self.document_processor: return "Error: Document processor not initialized"
        res = await self.document_processor.index_folder(path, project_id)
        return f"Indexing result for {path}: Indexed {res.get('indexed', 0)}, Failed {res.get('failed', 0)}"

    def docs_search_handler(self, query: str, package_name: str = None, project_id: str = "default", **kwargs) -> str:
        if not getattr(self, "docs_searcher", None): return "Error: Local docs store not initialized"
        max_results = int(kwargs.get("max_results", 5))
        return self.docs_searcher.search(project_id, query, package_name, max_results=max_results)

    def docs_ingest_handler(self, path: str, project_id: str = "default", source_type: str = "project_docs", **kwargs) -> str:
        if not getattr(self, "docs_ingester", None): return "Error: Local docs store not initialized"
        res = self.docs_ingester.ingest_folder(project_id, path, source_type)
        if "error" in res:
            return f"Ingestion failed: {res['error']}"
        return f"Ingested {res.get('files_processed')} files, added {res.get('chunks_added')} searchable chunks."

    def docs_ingest_package_handler(self, package_name: str, project_id: str = "default", **kwargs) -> str:
        if not getattr(self, "docs_ingester", None): return "Error: Local docs store not initialized"
        manager = kwargs.get("manager", "pip")
        res = self.docs_ingester.ingest_package(project_id, package_name, manager=manager)
        if "error" in res:
            return f"Ingestion failed: {res['error']}"
        return f"Ingested {package_name} package docs, added {res.get('chunks_added')} searchable chunks."

    def docs_ask_handler(
        self,
        question: str,
        project_id: str = "default",
        package_name: str = None,
        max_results: int = 5,
        **kwargs,
    ) -> str:
        if not getattr(self, "docs_searcher", None):
            return "Error: Local docs store not initialized"
        payload = self.docs_searcher.ask(
            project_id=project_id,
            question=question,
            package_name=package_name,
            max_results=int(max_results or 5),
        )
        answer = payload.get("answer", "")
        citations = payload.get("citations") or []
        if citations:
            answer += "\n\nCitations:\n" + "\n".join(
                f"- {(c.get('source_path') or c.get('path_or_url'))}#chunk={c.get('chunk_id')}" for c in citations
            )
        return answer

    def get_definitions(self) -> List[Dict[str, Any]]:
        return [t["definition"] for t in self.tools.values()]

    def get_tool_descriptions(self) -> str:
        from .model_registry import is_compact_prompts_enabled
        if is_compact_prompts_enabled():
            lines = []
            for name, info in self.tools.items():
                defn = info.get("definition", {})
                func = defn.get("function", {})
                desc = func.get("description", "")
                params = func.get("parameters", {})
                properties = params.get("properties", {})
                required = params.get("required", [])
                
                arg_parts = []
                for prop_name, prop_info in properties.items():
                    prop_type = prop_info.get("type", "any")
                    if prop_name in required:
                        arg_parts.append(f"{prop_name}: {prop_type}")
                    else:
                        arg_parts.append(f"{prop_name}?: {prop_type}")
                sig = f"{name}({', '.join(arg_parts)})"
                lines.append(f"- {sig}: {desc}")
            return "\n".join(lines)
        defs = self.get_definitions()
        return "\n".join([f"- {d['function']['name']}: {d['function']['description']}" for d in defs])


    def list_files(self, path: str = ".", **kwargs) -> str:
        try:
            items = os.listdir(path)
            return json.dumps(items)
        except Exception as e:
            return f"Error: {str(e)}"

    def get_file_tree(self, path: str, max_depth: int = 3, **kwargs) -> str:
        tree = []
        path = os.path.abspath(path)
        base_level = path.count(os.sep)
        try:
            for root, dirs, files in os.walk(path):
                current_level = root.count(os.sep)
                if current_level - base_level >= max_depth:
                    del dirs[:]
                    continue
                
                rel_path = os.path.relpath(root, path)
                if rel_path == ".":
                    rel_path = os.path.basename(path)
                
                tree.append(f"{rel_path}/")
                for f in files:
                    if not f.startswith('.'):
                        tree.append(f"  {f}")
            return "\n".join(tree)
        except Exception as e:
            return f"Error: {str(e)}"

    def read_file(self, path: str, **kwargs) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"Error: {str(e)}"

    def read_multiple_files(self, paths: List[str], **kwargs) -> str:
        results = {}
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    results[p] = f.read()
            except Exception as e:
                results[p] = f"Error: {str(e)}"
        return json.dumps(results, indent=2)

    def search_files(self, query: str, path: str = ".", **kwargs) -> str:
        try:
            import subprocess
            cmd = ["grep", "-rn", "--exclude-dir=.next", "--exclude-dir=node_modules", query, path]
            # Limit output to 2000 chars to avoid token blowout
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
            return output[:2000] + ("\n...(truncated)" if len(output) > 2000 else "")
        except subprocess.CalledProcessError:
            return "No matches found."
        except Exception as e:
            return f"Error: {str(e)}"

    def write_file(self, path: str, content: str, **kwargs) -> str:
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote to {path}"
        except Exception as e:
            return f"Error: {str(e)}"

    def create_directory(self, path: str, **kwargs) -> str:
        try:
            os.makedirs(path, exist_ok=True)
            return f"Successfully created directory {path}"
        except Exception as e:
            return f"Error creating directory: {str(e)}"

    def install_dependency(self, package: str, manager: str = "pip", **kwargs) -> str:
        try:
            import subprocess
            import sys
            cmd = []
            if manager == "pip":
                cmd = [sys.executable, "-m", "pip", "install", package]
            elif manager == "npm":
                cmd = ["npm", "install", package]
            elif manager == "yarn":
                cmd = ["yarn", "add", package]
            elif manager == "cargo":
                cmd = ["cargo", "add", package]
            else:
                return f"Unsupported package manager: {manager}"
                
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return f"Successfully installed {package} via {manager}\n{result.stdout}"
            else:
                return f"Failed to install {package}:\n{result.stderr}"
        except Exception as e:
            return f"Error installing dependency: {str(e)}"

    @staticmethod
    def _normalize_python_command(command: str) -> str:
        """
        Normalize Python-related commands to use the current venv's interpreter.

        Rewrites:
          pip install ...        → /path/to/venv/python -m pip install ...
          pytest ...             → /path/to/venv/python -m pytest ...
          python script.py       → python3 script.py
          python -m ...          → /path/to/venv/python -m ...

        Does NOT rewrite:
          npm run build, echo, curl, etc.
        """
        import sys
        import shutil

        stripped = command.strip()
        py = sys.executable  # e.g. /Users/.../venv/bin/python

        # pip ... → python -m pip ...
        # pip ... → python -m pip ...
        if stripped.startswith("pip ") or stripped == "pip":
            return f"{py} -m {stripped}"

        # pip3 ... → python -m pip ...
        if stripped.startswith("pip3 ") or stripped == "pip3":
            return f"{py} -m pip{stripped[4:]}"

        # pytest ... → PYTHONPATH=. python3 -m pytest -q ...
        if stripped.startswith("pytest") or "pytest" in stripped:
            if "PYTHONPATH" not in stripped:
                # Extract args after pytest
                parts = stripped.split("pytest", 1)
                args = parts[1].strip() if len(parts) > 1 else ""
                if not args:
                    args = "-q"
                return f"PYTHONPATH=. {py} -m pytest {args}".strip()
            return stripped

        # python -m ... → venv python -m ...
        if stripped.startswith("python -m "):
            return f"{py} -m {stripped[10:]}"

        # python3 -m ... → venv python -m ...
        if stripped.startswith("python3 -m "):
            return f"{py} -m {stripped[11:]}"

        # python script.py → python3 script.py (use venv)
        if stripped.startswith("python ") and not stripped.startswith("python3"):
            # Check if python is available, otherwise use python3
            if not shutil.which("python"):
                return f"{py} {stripped[7:]}"

        return command

    def run_command(self, command: str, cwd: str = ".", **kwargs) -> str:
        try:
            import subprocess
            original = command
            command = self._normalize_python_command(command)
            if command != original:
                logger.info(f"Normalized command: original={original!r} → normalized={command!r}")
            # We use a short timeout for dev starts so we don't block the server forever.
            # Usually services yield some output before backgrounding.
            result = subprocess.run(
                command, 
                cwd=cwd, 
                shell=True, 
                capture_output=True, 
                text=True, 
                timeout=30
            )
            out = result.stdout + result.stderr
            status = "Success" if result.returncode == 0 else "Failed"
            return f"[{status}] Output:\n{out}"
        except subprocess.TimeoutExpired as e:
            # If it timed out, it might be a long-running service that started successfully!
            out = (e.stdout.decode() if e.stdout else "") + (e.stderr.decode() if e.stderr else "")
            return f"[Timeout/Started] Command timed out but might be running. Output:\n{out}"
        except Exception as e:
            return f"Error: {str(e)}"

    async def web_search(self, query: str, max_results: int = 5, **kwargs) -> str:
        """Execute a web search and return results as a formatted string."""
        if not self.search_backend:
             return "Error: Search backend not initialized"
             
        try:
            results = await self.search_backend.search(query, max_results=max_results)
            
            # Audit Log
            try:
                self.source_store.log_search("agent_tool", query, self.search_backend.backend_type, results)
            except Exception as le:
                logger.warning(f"Failed to log search: {le}")

            formatted = []
            for r in results:
                formatted.append(f"SOURCE: {r['title']}\nURL: {r['url']}\nSUMMARY: {r['snippet']}\n[Engine: {r.get('engine', 'unknown')}]\n---")
            
            if not formatted:
                return f"No results found for query: {query}"
                
            return "\n".join(formatted)
        except Exception as e:
            logger.error(f"Web search failed: {e}")
            return f"Web search error: {str(e)}"

    async def web_research_handler(self, query: str, max_results: int = 5, max_sources: int = 3, project_id: str = "default", **kwargs) -> str:
        """Perform autonomous research and return a report."""
        if not self.web_researcher:
            return "Error: Web researcher not initialized"
            
        try:
            # Full research task
            result = await self.web_researcher.perform_research(
                project_id=project_id,
                query=query,
                max_results=max_results,
                max_sources=max_sources
            )
            
            report = result.get("answer", "No answer generated.")
            sources = result.get("sources", [])
            
            if sources:
                report += "\n\n### Sources\n"
                for s in sources:
                    report += f"- {s['title']} ({s['url']})\n"
                    
            return report
        except Exception as e:
            logger.error(f"Web research failed: {e}")
            return f"Web research error: {str(e)}"

    async def deep_research_handler(self, query: str, project_id: str = "default", **kwargs) -> str:
        """Perform high-speed web search and FULL SITE SCAN of top results."""
        if not self.search_backend or not self.web_extractor:
            return "Error: Search or Extraction backend not initialized"
        
        try:
            logger.info(f"Raptor Recon: Starting deep research for {query}")
            # 1. Search for top 3 candidates
            results = await self.search_backend.search(query, max_results=3)
            if not results:
                return "No high-fidelity sources found for this topic."
            
            # 2. Parallel fetch and extract
            fetch_tasks = []
            async with httpx.AsyncClient(headers={"User-Agent": "StarAgent/2.1"}, timeout=15) as client:
                for r in results:
                    url = r.get("url")
                    if url:
                        fetch_tasks.append(self._fetch_and_extract(client, url))
                
                # Run all in parallel
                extracted_data = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            
            # 3. Format intelligence pack
            intelligence_pack = [f"### RAPTOR RECON REPORT: {query}\n"]
            for i, data in enumerate(extracted_data):
                if isinstance(data, dict) and data.get("text"):
                    intelligence_pack.append(f"SOURCE {i+1}: {data['title']}\nURL: {data['url']}\nCONTENT: {data['text'][:5000]}\n---")
                elif isinstance(data, Exception):
                    logger.warning(f"Fetch failed for one source: {data}")
            
            if len(intelligence_pack) == 1:
                return "Failed to extract deep data from sources. Falling back to snippets."
            
            return "\n".join(intelligence_pack)
        except Exception as e:
            logger.error(f"Deep research failed: {e}")
            return f"Error performing deep research: {str(e)}"

    async def wikipedia_search_handler(self, query: str, max_results: int = 3, **kwargs) -> str:
        """Handler for Wikipedia search."""
        if not self.search_backend:
            return "Error: Search backend not initialized"
        
        try:
            logger.info(f"Wiki Triage: Searching for {query}")
            results = await self.search_backend._search_wikipedia(query, max_results)
            if not results:
                return "No relevant Wikipedia pages found for this topic."
            
            output = [f"### WIKIPEDIA RESEARCH: {query}\n"]
            for r in results:
                output.append(f"TITLE: {r['title']}\nURL: {r['url']}\nSUMMARY: {r['snippet']}\n---")
            
            # Audit log for history
            if self.source_store:
                self.source_store.log_search("default", query, "wikipedia", results)
                
            return "\n".join(output)
        except Exception as e:
            logger.error(f"Wikipedia search failed: {e}")
            return f"Error performing wikipedia search: {str(e)}"

    async def _fetch_and_extract(self, client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
        """Fetch HTML and extract text."""
        try:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            html = resp.text
            result = self.web_extractor.extract(html, url)
            result["url"] = url
            return result
        except Exception as e:
            return {"url": url, "text": None, "error": str(e)}

    async def read_url_content_handler(self, url: str, **kwargs) -> str:
        """Fetch URL content and return extracted text."""
        if not self.web_extractor:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(url, follow_redirects=True)
                    resp.raise_for_status()
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, "html.parser")
                    return f"### CONTENT FROM {url}\n\n{soup.get_text()[:4000]}"
            except Exception as e:
                return f"Failed to extract URL content: {e}"
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await self._fetch_and_extract(client, url)
                if res.get("error"):
                    return f"Error extracting content: {res['error']}"
                return f"### CONTENT FROM {url}\n\n{res.get('text', '')}"
        except Exception as e:
            return f"Failed to extract URL content: {e}"
