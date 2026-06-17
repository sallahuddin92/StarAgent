import logging
import os
import httpx
from typing import List, Dict, Any, Optional
from duckduckgo_search import DDGS
import random
import wikipedia

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
]

logger = logging.getLogger(__name__)

class SearchBackend:
    """Unified search interface supporting SearXNG and DuckDuckGo."""
    
    def __init__(self):
        self.backend_type = os.getenv("STARAGENT_SEARCH_BACKEND", "duckduckgo").lower()
        self.searxng_url = os.getenv("STARAGENT_SEARXNG_URL", "http://127.0.0.1:8088").rstrip("/")
        self.timeout = int(os.getenv("STARAGENT_WEB_TIMEOUT_S", "15"))

    async def search(self, query: str, max_results: int = 5, domain_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Perform web search with configured backend."""
        logger.info(f"Performing {self.backend_type} search for: {query}")
        
        lower_query = query.lower()
        # SPECIAL CASE: Weather Direct (wttr.in bypass)
        weather_keywords = ("weather", "temperature", "rain", "forecast", "humidity", "wind", "sunny", "cloudy")
        if any(k in lower_query for k in weather_keywords):
            try:
                words = query.replace("?", " ").replace("_", " ").split()
                # Find capitalized word (usually the city)
                city_match = [w for w in words if w[0].isupper()]
                city = city_match[0] if city_match else words[-1]
                # Further sanitize: remove trailing non-alpha
                city = "".join(filter(str.isalnum, city))
                
                logger.info(f"Cleaned city for weather: '{city}'")
                async with httpx.AsyncClient(timeout=10) as client:
                    # Use wttr.in for fast, text-based weather
                    r = await client.get(f"https://wttr.in/{city}?format=3")
                    if r.status_code == 200:
                        return [{
                            "title": f"Current weather in {city}",
                            "url": f"https://wttr.in/{city}",
                            "snippet": r.text,
                            "engine": "wttr.in"
                        }]
            except Exception as e:
                logger.warning(f"wttr.in bypass failed: {e}")

        if self.backend_type == "searxng":
            try:
                return await self._search_searxng(query, max_results, domain_filter)
            except Exception as e:
                logger.warning(f"SearXNG failed, falling back to DDG: {e}")
                results = await self._search_ddg(query, max_results, domain_filter)
        else:
            results = await self._search_ddg(query, max_results, domain_filter)
            
        # Optimization: Determine if the query is asking for breaking/real-time info
        is_realtime = any(w in lower_query for w in ("latest", "breaking", "today", "now", "current", "stock price"))
        
        # Optimization: Boost Wikipedia for deep factual and economic queries
        is_fact_query = any(w in lower_query for w in ("population", "how many", "who is", "country count", "demographics", "history", "gdp", "economic", "economy"))
        
        if (not results or is_fact_query) and not is_realtime:
             logger.info(f"Economic/Factual query detected. Forcing high-density sources for: {query}")
             wiki_results = await self._search_wikipedia(query, max_results)
             if wiki_results:
                 results = results + wiki_results # Append wiki data (Web results first for freshness)
             
        return results

    async def _search_searxng(self, query: str, max_results: int, domain_filter: Optional[str]) -> List[Dict[str, Any]]:
        """Search via local SearXNG instance."""
        search_query = query
        if domain_filter:
            search_query = f"{query} site:{domain_filter}"
            
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.searxng_url}/search",
                params={
                    "q": search_query,
                    "format": "json",
                    "engines": "google,bing,duckduckgo",
                    "pageno": 1
                }
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            
            return [
                {
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "snippet": r.get("content"),
                    "engine": r.get("engine", "searxng")
                }
                for r in results[:max_results]
            ]

    async def _search_ddg(self, query: str, max_results: int, domain_filter: Optional[str]) -> List[Dict[str, Any]]:
        """Search via DuckDuckGo with retry logic."""
        search_query = query
        if domain_filter:
            search_query = f"{query} site:{domain_filter}"
            
        # RETRY LOOP: 3 attempts with different delays
        for attempt in range(3):
            results = []
            try:
                with DDGS() as ddgs:
                    # Primary: Text search
                    try:
                        ddgs_results = ddgs.text(search_query, max_results=max_results)
                        for r in ddgs_results:
                            results.append({
                                "title": r.get("title"),
                                "url": r.get("href"),
                                "snippet": r.get("body"),
                                "engine": "duckduckgo"
                            })
                    except Exception as e:
                        logger.warning(f"DDG attempt {attempt + 1} text search failed: {e}")

                    # Fallback: News search
                    if not results:
                        news_results = ddgs.news(search_query, max_results=max_results)
                        for r in news_results:
                            results.append({
                                "title": r.get("title"),
                                "url": r.get("url"),
                                "snippet": r.get("body"),
                                "engine": "duckduckgo_news"
                            })
                
                if results:
                    return results
            except Exception as e:
                logger.error(f"DDG attempt {attempt + 1} fatal error: {e}")
            
            import asyncio
            await asyncio.sleep(attempt + 0.5) # Quick backoff
                
        # FINAL FALLBACK: If DDG is dead, hit Wikipedia
        if not results:
            logger.info(f"Economic/Factual query detected. Forcing Wikipedia fallback for: {query}")
            return await self._search_wikipedia(query, max_results=max_results)
            
        return results



    async def _search_wikipedia(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        """Search via Wikipedia as a high-reliability fallback."""
        try:
            # Use random User-Agent
            wikipedia.set_user_agent(random.choice(USER_AGENTS))
            
            search_results = wikipedia.search(query, results=max_results)
            results = []
            
            # Identify core keywords for filtering (Case-insensitive)
            # We filter out generic words and keep nouns/entities
            stop_words = {"what", "current", "rate", "growth", "2024", "news", "expansion", "latest", "about", "there", "this"}
            core_keywords = [w.lower() for w in query.split() if len(w) > 3 and w.lower() not in stop_words]
            
            # THE PRIMARY ENTITY is likely the first or second word that isn't a stop word
            # e.g. "Intel expansion" -> Intel
            primary_subject = core_keywords[0] if core_keywords else None

            for title in search_results:
                title_lower = title.lower()
                
                # RELEVANCE CHECK: 
                # 1. If we have a primary subject, it MUST be in the title
                # 2. Otherwise, at least one core keyword must be in the title
                if primary_subject and primary_subject not in title_lower:
                    logger.debug(f"Skipping wiki page (fails primary subject {primary_subject}): {title}")
                    continue
                
                if not any(kw in title_lower for kw in core_keywords):
                    logger.debug(f"Skipping irrelevant wiki page: {title}")
                    continue

                try:
                    # Get larger summary for data extraction
                    p = wikipedia.page(title, auto_suggest=False)
                    results.append({
                        "title": p.title,
                        "url": p.url,
                        "snippet": p.summary[:3000], # Use summary for high-density facts
                        "engine": "wikipedia"
                    })
                except Exception as ex:
                    logger.warning(f"Failed to get wiki page {title}: {ex}")
                    
            return results[:2] # Only return top 2 HIGH-FIDELITY results
        except Exception as e:
            logger.error(f"Wikipedia fallback failed: {e}")
            return []
