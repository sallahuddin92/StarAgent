import logging
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup
import trafilatura
from readability import Document

logger = logging.getLogger(__name__)

class ContentExtractor:
    """Extracts clean, readable text from HTML with multiple engine fallbacks."""
    
    def extract(self, html: str, url: str) -> Dict[str, Any]:
        """
        Run extraction pipeline: Trafilatura -> Readability -> BeautifulSoup.
        Returns: {title, text, word_count, excerpt}
        """
        if not html:
            return {"title": "No content", "text": "", "word_count": 0, "excerpt": ""}

        # 1. Try Trafilatura (Best for articles/news)
        try:
            extracted = trafilatura.extract(html, include_comments=False, include_tables=True)
            if extracted and len(extracted) > 200:
                # Use BeautifulSoup for a more reliable title extraction than trafilatura.find_title
                soup = BeautifulSoup(html, "html.parser")
                title = soup.title.string if soup.title else "Untitled"
                return self._package_result(title, extracted)
        except Exception as e:
            logger.warning(f"Trafilatura failed: {e}")

        # 2. Try Readability-lxml (Safari Reader Mode logic)
        try:
            doc = Document(html)
            title = doc.short_title()
            summary = doc.summary() # This is cleaned HTML
            # Strip tags for text
            soup = BeautifulSoup(summary, "html.parser")
            text = soup.get_text(separator="\n").strip()
            if text and len(text) > 200:
                return self._package_result(title, text)
        except Exception as e:
            logger.warning(f"Readability failed: {e}")

        # 3. Fallback: BeautifulSoup (Basic tag stripping)
        try:
            soup = BeautifulSoup(html, "html.parser")
            # Remove scripts/styles
            for script in soup(["script", "style", "header", "footer", "nav"]):
                script.decompose()
            text = soup.get_text(separator="\n").strip()
            title = soup.title.string if soup.title else "Untitled"
            return self._package_result(title, text)
        except Exception as e:
            logger.error(f"BS4 fallback failed: {e}")
            return {"title": "Error", "text": "Extraction failed", "word_count": 0, "excerpt": ""}

    def _package_result(self, title: str, text: str) -> Dict[str, Any]:
        """Normalize results into a dict."""
        words = text.split()
        return {
            "title": title,
            "text": text,
            "word_count": len(words),
            "excerpt": " ".join(words[:50]) + "..."
        }

from typing import Any
