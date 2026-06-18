from typing import List, Dict, Any, Optional

class ResearchProvider:
    def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        raise NotImplementedError

class LocalDocsProvider(ResearchProvider):
    def __init__(self, docs_searcher: Optional[Any] = None, use_global_fallback: bool = True):
        self.docs_searcher = docs_searcher
        self.use_global_fallback = use_global_fallback

    def search(self, query: str, project_id: str = "default", **kwargs) -> List[Dict[str, Any]]:
        if not self.docs_searcher and self.use_global_fallback:
            try:
                from app.main import tool_registry
                self.docs_searcher = getattr(tool_registry, "docs_searcher", None)
            except Exception:
                pass
        
        if not self.docs_searcher:
            return []
            
        results = self.docs_searcher.search_structured(project_id, query)
        out = []
        for r in results:
            out.append({
                "url": r.get("source_path") or f"docs://{r.get('chunk_id')}",
                "title": r.get("title") or "Local Document",
                "content": r.get("content") or ""
            })
        return out

class ManualUrlsProvider(ResearchProvider):
    def search(self, query: str, urls: Optional[List[str]] = None, **kwargs) -> List[Dict[str, Any]]:
        out = []
        if urls:
            for url in urls:
                out.append({
                    "url": url,
                    "title": url,
                    "content": ""
                })
        return out

class WebSearchStubProvider(ResearchProvider):
    def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        print("web provider not configured")
        return []

class BraveFutureProvider(WebSearchStubProvider):
    pass

class TavilyFutureProvider(WebSearchStubProvider):
    pass

class SerperFutureProvider(WebSearchStubProvider):
    pass
