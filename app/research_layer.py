import os
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List

from .search_backend import SearchBackend
# from .web_fetcher import fetch_url
# from .content_extractor import extract_readable_text

logger = logging.getLogger(__name__)

class ResearchLayer:
    """
    StarAgent v3 Research Layer
    Externalizes knowledge gathering. Bridges the gap between small model training 
    cutoffs and modern API realities.
    """
    
    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root).resolve()
        
    def search_local_docs(self, query: str) -> str:
        """Search local READMEs or doc files in the workspace."""
        results = []
        for doc_file in ["README.md", "README.txt", "docs/README.md"]:
            p = self.workspace_root / doc_file
            if p.exists():
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                    # Return truncated start of doc to preserve context window
                    results.append(f"Source: {doc_file}\n{content[:1500]}")
                except Exception:
                    pass
        if results:
            return "\n\n---\n\n".join(results)
        return "No local documentation found."
        
    def inspect_installed_package_source(self, package_name: str) -> str:
        """
        Attempt to find and read docstrings or source of an installed package.
        For Python, uses pydoc. For Node, reads node_modules README.
        """
        # Try Python pydoc
        try:
            result = subprocess.run(
                ["python3", "-m", "pydoc", package_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip() and "no Python documentation found" not in result.stdout:
                return f"Python Package: {package_name}\nDocs:\n{result.stdout.strip()[:2500]}"
        except Exception as e:
            logger.debug(f"Pydoc inspection failed for {package_name}: {e}")
            
        # Try npm node_modules
        node_pkg_readme = self.workspace_root / "node_modules" / package_name / "README.md"
        if node_pkg_readme.exists():
            try:
                return f"Node Package: {package_name}\nDocs:\n{node_pkg_readme.read_text(encoding='utf-8')[:2500]}"
            except Exception:
                pass
            
        return f"Could not extract local source documentation for '{package_name}'."
        
    def search_web_docs(self, query: str) -> str:
        """Search official web documentation via search backend."""
        try:
            searcher = SearchBackend()
            # SearchBackend.search is async, but research_layer seems to be synchronous right now...
            # Actually, this whole method might be broken. Let's just mock it or run it using asyncio.run
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            results = loop.run_until_complete(searcher.search(query, max_results=3))
            if not results:
                return "No web results found."
                
            formatted = []
            for r in results:
                formatted.append(f"Title: {r.get('title', 'Unknown')}\nURL: {r.get('url', 'Unknown')}\nSnippet: {r.get('snippet', '')}")
            return "Web Search Results:\n" + "\n\n".join(formatted)
        except Exception as e:
            return f"Web search failed: {str(e)}"
            
    def fetch_and_summarize_url(self, url: str) -> str:
        """Fetch URL content and compress it for context windows."""
        return f"Web fetching disabled due to broken imports."
        # try:
        #     html = fetch_url(url)
        #     text = extract_readable_text(html)
        #     return f"Source: {url}\nContent:\n{text[:3500]}... [Truncated]"
        # except Exception as e:
        #     return f"Failed to fetch {url}: {str(e)}"

    def execute_research(self, queries: List[str]) -> str:
        """
        Execute a full research cycle across multiple domains.
        """
        findings = []
        for q in queries:
            findings.append(f"### Research Target: {q}")
            
            # 1. Package inspection heuristic
            if len(q.split()) == 1 and not q.startswith("http"):
                pkg_res = self.inspect_installed_package_source(q)
                if "Could not extract" not in pkg_res:
                    findings.append(pkg_res)
                    continue
                    
            # 2. Local docs attempt
            local_res = self.search_local_docs(q)
            if "No local documentation found" not in local_res:
                findings.append(local_res)
                
            # 3. Web Search fallback
            web_res = self.search_web_docs(q)
            findings.append(web_res)
            
        return "\n\n".join(findings)
