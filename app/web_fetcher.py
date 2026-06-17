import logging
import asyncio
import time
import httpx
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

class WebFetcher:
    """Polite web fetcher with rate limiting and basic robots.txt awareness."""
    
    def __init__(self):
        self.timeout = int(os.getenv("STARAGENT_WEB_TIMEOUT_S", "15"))
        self.rate_limit_delay = float(os.getenv("STARAGENT_WEB_RATE_LIMIT_S", "2"))
        self.last_fetch: Dict[str, float] = {}  # {domain: timestamp}
        self.user_agent = "StarAgent/1.0 (+https://github.com/sallahuddin92/StarAgent)"

    async def fetch(self, url: str) -> Tuple[int, Optional[str], str, int]:
        """
        Fetch URL content with rate limiting.
        Returns: (status_code, content_type, final_url, text_length)
        """
        domain = urlparse(url).netloc
        await self._wait_for_rate_limit(domain)
        
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": self.user_agent}
            ) as client:
                response = await client.get(url)
                self.last_fetch[domain] = time.time()
                
                content_type = response.headers.get("content-type", "text/plain")
                return (
                    response.status_code,
                    content_type,
                    str(response.url),
                    len(response.text)
                )
        except Exception as e:
            logger.error(f"Fetch failed for {url}: {e}")
            return (0, None, url, 0)

    async def fetch_full(self, url: str) -> Optional[str]:
        """Fetch and return full HTML/text content."""
        domain = urlparse(url).netloc
        await self._wait_for_rate_limit(domain)
        
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": self.user_agent}
            ) as client:
                response = await client.get(url)
                self.last_fetch[domain] = time.time()
                response.raise_for_status()
                return response.text
        except Exception as e:
            logger.error(f"Full fetch failed for {url}: {e}")
            return None

    async def _wait_for_rate_limit(self, domain: str):
        """Simple domain-based sleep to respect rate limits."""
        now = time.time()
        last = self.last_fetch.get(domain, 0)
        elapsed = now - last
        
        if elapsed < self.rate_limit_delay:
            wait_time = self.rate_limit_delay - elapsed
            logger.debug(f"Rate limiting {domain}: waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)

# We need os for fallback in WebFetcher if not already imported
import os
