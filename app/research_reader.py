import datetime
import httpx
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup

class ResearchReader:
    def __init__(self, max_chars: int = 100000):
        self.max_chars = max_chars

    def fetch_and_clean(self, url: str, run_id: str, source_id: str) -> dict:
        """
        Fetches the URL content, cleans it by removing scripts/styles/HTML tags,
        captures metadata, stores the cleaned text, and returns metadata.
        """
        fetched_at = datetime.datetime.utcnow().isoformat()
        title = "Untitled"
        content = ""
        error = None
        
        try:
            if url.startswith("file://"):
                # Handle file url
                parsed = urlparse(url)
                # On macOS/Linux, parsed.path is the absolute path
                path = Path(parsed.path)
                if not path.exists():
                    # Fallback for parsing differences
                    path = Path(url.replace("file://", ""))
                
                content = path.read_text(encoding="utf-8", errors="replace")
                title = path.name
            else:
                # HTTP/HTTPS request
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                # Follow redirects, set timeout
                r = httpx.get(url, headers=headers, follow_redirects=True, timeout=10.0)
                r.raise_for_status()
                content = r.text
                title = url
                
            # If HTML, clean it
            if content.strip().startswith("<") or "<html" in content.lower():
                soup = BeautifulSoup(content, "html.parser")
                # Extract title
                if soup.title and soup.title.string:
                    title = soup.title.string.strip()
                # Strip script and style elements
                for script in soup(["script", "style"]):
                    script.extract()
                # Get text
                text = soup.get_text(separator="\n")
                # Collapse whitespace
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                content = "\n".join(chunk for chunk in chunks if chunk)
            else:
                # Plain text / markdown
                # Try to extract title from markdown e.g. # Title
                match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
                if match:
                    title = match.group(1).strip()
            
            # Truncate content to respect max chars
            if len(content) > self.max_chars:
                content = content[:self.max_chars]
                
        except Exception as e:
            error = str(e)
            content = f"Error fetching URL: {error}"
            
        # Store clean text under the run directory
        raw_sources_dir = Path(".runtime") / "workflows" / run_id / "raw_sources"
        raw_sources_dir.mkdir(parents=True, exist_ok=True)
        source_file = raw_sources_dir / f"{source_id}.txt"
        source_file.write_text(content, encoding="utf-8")
        
        return {
            "source_id": source_id,
            "url": url,
            "title": title,
            "fetched_at": fetched_at,
            "file_path": str(source_file),
            "error": error
        }
