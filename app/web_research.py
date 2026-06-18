import logging
import asyncio
from typing import List, Dict, Any, Optional
from .search_backend import SearchBackend
from .web_fetcher import WebFetcher
from .content_extractor import ContentExtractor
from .source_store import SourceStore
from .llm_client import LLMClient

logger = logging.getLogger(__name__)

class WebResearcher:
    """Orchestrates multi-step web research: Search -> Fetch -> Extract -> Summarize -> Synthesize."""
    
    def __init__(self, llm: LLMClient, source_store: Optional[SourceStore] = None):
        self.search_backend = SearchBackend()
        self.fetcher = WebFetcher()
        self.extractor = ContentExtractor()
        self.store = source_store or SourceStore()
        self.llm = llm

    async def perform_research(
        self, 
        project_id: str, 
        query: str, 
        max_results: int = 5, 
        max_sources: int = 3,
        depth: int = 1
    ) -> Dict[str, Any]:
        """Execute full research workflow."""
        logger.info(f"Starting research on: {query} (Project: {project_id})")
        
        # 1. Search
        search_results = await self.search_backend.search(query, max_results=max_results)
        self.store.log_search(project_id, query, self.search_backend.backend_type, search_results)

        if not search_results:
            return {
                "query": query,
                "answer": "No search results found.",
                "sources": [],
                "status": "completed_with_limitations",
                "status_reason": "No search results were returned by the configured search backend.",
            }

        # 2. Process Sources (Fetch & Extract)
        processed_sources = []
        # We limit sources to avoid excessive time and token usage
        for sr in search_results[:max_sources]:
            url = sr["url"]
            
            # Check cache first
            html = self.store.get_cached_html(url)
            if not html:
                html = await self.fetcher.fetch_full(url)
                if html:
                    self.store.set_cached_html(url, html)
            
            if html:
                extracted = self.extractor.extract(html, url)
                if extracted["word_count"] > 100:
                    source_id = await self.store.save_source(project_id, url, extracted["title"], extracted["text"])
                    
                    # 3. Quick Summary per source
                    summary = await self._summarize_source(extracted["title"], extracted["text"][:4000])
                    processed_sources.append({
                        "id": source_id,
                        "url": url,
                        "title": extracted["title"],
                        "summary": summary,
                        "excerpt": extracted["excerpt"]
                    })

        # 4. Synthesize Final Report
        if not processed_sources:
            return {
                "query": query,
                "answer": "Failed to extract content from sources.",
                "sources": [],
                "status": "completed_with_limitations",
                "status_reason": "Sources were found but none yielded extractable content (all had insufficient word count or fetch failures).",
            }

        final_answer = await self._synthesize_report(query, processed_sources)
        
        return {
            "query": query,
            "answer": final_answer,
            "sources": processed_sources
        }

    async def _summarize_source(self, title: str, text: str) -> str:
        """Use local LLM to summarize a single source."""
        prompt = f"Summarize the following article titled '{title}' in 1-2 concise paragraphs. Focus on facts and data related to research.\n\nContent:\n{text}"
        try:
            return await self.llm.text([{"role": "user", "content": prompt}])
        except Exception as e:
            logger.warning(f"Source summary failed: {e}")
            return "Summary unavailable."

    async def _synthesize_report(self, query: str, sources: List[Dict[str, Any]]) -> str:
        """Synthesize all source summaries into one markdown report."""
        sources_text = ""
        for i, s in enumerate(sources):
            sources_text += f"\nSource [{i+1}]: {s['title']} ({s['url']})\nSummary: {s['summary']}\n"
            
        prompt = f"""You are a research assistant. Based on the following sources, provide a detailed, grounded report answering the query: '{query}'.
Include citations like [1], [2] corresponding to the source list. 
Structure with Markdown headings.

Sources:
{sources_text}
"""
        try:
            return await self.llm.text([{"role": "user", "content": prompt}])
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return "Failed to synthesize final report."
