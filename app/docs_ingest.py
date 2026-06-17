from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .docs_store import DocsStore

logger = logging.getLogger(__name__)


class DocsIngester:
    """Parse, chunk, and persist local/project docs into the docs store."""

    SUPPORTED_EXTS = {".pdf", ".txt", ".md", ".markdown", ".html", ".htm"}

    def __init__(self, docs_store: DocsStore):
        self.store = docs_store

    def ingest_folder(self, project_id: str, folder_path: str, source_type: str = "project_docs") -> Dict[str, Any]:
        """Backward-compatible entrypoint; now supports both file and folder paths."""
        return self.ingest_path(project_id, folder_path, source_type=source_type)

    def ingest_path(self, project_id: str, path: str, source_type: str = "project_docs") -> Dict[str, Any]:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return {"error": f"Path {path} not found."}

        if p.is_dir() and (p / "db.json").exists() and (p / "index.json").exists():
            return self.ingest_devdocs_format(project_id, str(p))

        files_processed = 0
        chunks_added = 0
        skipped = 0

        for doc_path in self._iter_supported_files(p):
            try:
                source_id = self.store.add_source(
                    project_id=project_id,
                    source_type=source_type,
                    title=doc_path.name,
                    path_or_url=str(doc_path),
                )

                chunk_records = self._extract_and_chunk(doc_path)
                for idx, chunk in enumerate(chunk_records):
                    content = (chunk.get("content") or "").strip()
                    if not content:
                        continue
                    added = self.store.add_chunk(
                        source_id=source_id,
                        content=content,
                        heading=chunk.get("heading"),
                        code_examples=chunk.get("code_examples"),
                        chunk_index=idx,
                        source_path=str(doc_path),
                        page_ref=chunk.get("page_ref"),
                        section_ref=chunk.get("section_ref") or chunk.get("heading"),
                        metadata={
                            "ext": doc_path.suffix.lower(),
                            "filename": doc_path.name,
                            "chunk_len": len(content),
                        },
                    )
                    if added:
                        chunks_added += 1
                files_processed += 1
            except Exception as exc:
                logger.warning("Failed to ingest %s: %s", doc_path, exc)
                skipped += 1

        return {
            "status": "success",
            "path": str(p),
            "files_processed": files_processed,
            "chunks_added": chunks_added,
            "files_skipped": skipped,
        }

    def ingest_devdocs_format(self, project_id: str, folder_path: str) -> Dict[str, Any]:
        """
        Parse a DevDocs-style folder containing `db.json` and `index.json`.
        """
        path = Path(folder_path).resolve()
        try:
            db_json = json.loads((path / "db.json").read_text(encoding="utf-8", errors="ignore"))
            index_json = json.loads((path / "index.json").read_text(encoding="utf-8", errors="ignore"))

            pkg_name = index_json.get("name", "unknown-devdocs")
            pkg_version = index_json.get("version", "unknown")

            source_id = self.store.add_source(
                project_id=project_id,
                source_type="devdocs",
                title=f"{pkg_name} Docs",
                path_or_url=str(path),
                package_name=str(pkg_name).lower(),
                version=str(pkg_version),
            )

            chunks_added = 0
            idx = 0
            for doc_path, html_content in db_json.items():
                if not html_content:
                    continue
                for chunk in self._chunk_html(html_content, heading_fallback=str(doc_path)):
                    content = (chunk.get("content") or "").strip()
                    if not content:
                        continue
                    added = self.store.add_chunk(
                        source_id=source_id,
                        content=content,
                        heading=chunk.get("heading"),
                        code_examples=chunk.get("code_examples"),
                        chunk_index=idx,
                        source_path=str(path / doc_path),
                        page_ref=chunk.get("page_ref"),
                        section_ref=chunk.get("section_ref") or chunk.get("heading"),
                        metadata={"devdocs_path": doc_path, "package": pkg_name},
                    )
                    if added:
                        chunks_added += 1
                    idx += 1

            return {
                "status": "success",
                "source": str(pkg_name),
                "version": str(pkg_version),
                "chunks_added": chunks_added,
            }
        except Exception as exc:
            logger.error("Failed to parse DevDocs format: %s", exc)
            return {"error": str(exc)}

    def ingest_package(self, project_id: str, package_name: str, manager: str = "pip") -> Dict[str, Any]:
        """Extract package docs from local runtime using `pydoc` (Python)."""
        if manager != "pip":
            return {"error": f"Unsupported package manager: {manager}"}

        try:
            result = subprocess.run(
                ["python3", "-m", "pydoc", package_name],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode != 0 or "no Python documentation found" in (result.stdout or ""):
                return {"error": f"No documentation found for package '{package_name}' via pydoc."}

            source_id = self.store.add_source(
                project_id=project_id,
                source_type="package_source",
                title=f"{package_name} Pydoc",
                path_or_url=f"pydoc://{package_name}",
                package_name=package_name,
            )

            chunks_added = 0
            for i, chunk in enumerate(self._chunk_plain_text(result.stdout or "", heading="Pydoc")):
                content = (chunk.get("content") or "").strip()
                if not content:
                    continue
                if self.store.add_chunk(
                    source_id=source_id,
                    content=content,
                    heading=chunk.get("heading"),
                    code_examples=chunk.get("code_examples"),
                    chunk_index=i,
                    source_path=f"pydoc://{package_name}",
                    section_ref=chunk.get("section_ref") or chunk.get("heading"),
                    metadata={"package": package_name, "manager": manager},
                ):
                    chunks_added += 1
            return {"status": "success", "chunks_added": chunks_added}
        except Exception as exc:
            return {"error": str(exc)}

    def _iter_supported_files(self, path: Path) -> Iterable[Path]:
        if path.is_file():
            if path.suffix.lower() in self.SUPPORTED_EXTS:
                yield path
            return

        for root, dirs, files in os.walk(path):
            root_path = Path(root)
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__", ".git"}]
            if any(part.startswith(".") for part in root_path.parts):
                continue
            for f in files:
                fp = root_path / f
                if fp.suffix.lower() in self.SUPPORTED_EXTS:
                    yield fp

    def _extract_and_chunk(self, file_path: Path) -> List[Dict[str, Any]]:
        ext = file_path.suffix.lower()
        if ext == ".pdf":
            return self._chunk_pdf(file_path)
        if ext in {".md", ".markdown"}:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            return self._chunk_markdown(text)
        if ext in {".html", ".htm"}:
            html = file_path.read_text(encoding="utf-8", errors="ignore")
            return self._chunk_html(html, heading_fallback=file_path.name)

        text = file_path.read_text(encoding="utf-8", errors="ignore")
        return self._chunk_plain_text(text, heading=file_path.name)

    def _chunk_pdf(self, file_path: Path) -> List[Dict[str, Any]]:
        try:
            from pypdf import PdfReader
        except Exception:
            logger.warning("pypdf is not installed; skipping text extraction for %s", file_path)
            return []

        reader = PdfReader(str(file_path))
        chunks: List[Dict[str, Any]] = []
        for page_idx, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if not text.strip():
                continue
            page_chunks = self._chunk_plain_text(text, heading=f"Page {page_idx + 1}", section_ref=f"page-{page_idx + 1}")
            for c in page_chunks:
                c["page_ref"] = f"page-{page_idx + 1}"
            chunks.extend(page_chunks)
        return chunks

    def _chunk_markdown(self, text: str) -> List[Dict[str, Any]]:
        sections: List[Dict[str, Any]] = []
        current_heading = "Top Level"
        current_lines: List[str] = []

        code_blocks = re.findall(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", text, re.DOTALL)
        code_blob = "\n\n---\n\n".join(cb.strip() for cb in code_blocks if cb.strip()) or None

        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                if current_lines:
                    sections.extend(
                        self._chunk_plain_text(
                            "\n".join(current_lines),
                            heading=current_heading,
                            section_ref=current_heading,
                            code_examples=code_blob,
                        )
                    )
                    current_lines = []
                current_heading = line.lstrip("#").strip() or "Section"
            current_lines.append(line)

        if current_lines:
            sections.extend(
                self._chunk_plain_text(
                    "\n".join(current_lines),
                    heading=current_heading,
                    section_ref=current_heading,
                    code_examples=code_blob,
                )
            )
        return sections

    def _chunk_html(self, html_str: str, heading_fallback: str = "HTML Document") -> List[Dict[str, Any]]:
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html_str, "html.parser")
            for bad in soup(["script", "style", "noscript"]):
                bad.decompose()

            code_blocks = [c.get_text("\n", strip=True) for c in soup.find_all(["pre", "code"]) if c.get_text(strip=True)]
            code_blob = "\n\n---\n\n".join(code_blocks) or None

            chunks: List[Dict[str, Any]] = []
            current_heading = heading_fallback
            buffer: List[str] = []

            for node in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]):
                text = node.get_text(" ", strip=True)
                if not text:
                    continue
                if node.name and node.name.startswith("h"):
                    if buffer:
                        chunks.extend(
                            self._chunk_plain_text(
                                "\n".join(buffer),
                                heading=current_heading,
                                section_ref=current_heading,
                                code_examples=code_blob,
                            )
                        )
                        buffer = []
                    current_heading = text
                else:
                    buffer.append(text)

            if buffer:
                chunks.extend(
                    self._chunk_plain_text(
                        "\n".join(buffer),
                        heading=current_heading,
                        section_ref=current_heading,
                        code_examples=code_blob,
                    )
                )

            if not chunks:
                plain = soup.get_text("\n", strip=True)
                chunks.extend(self._chunk_plain_text(plain, heading=heading_fallback, section_ref=heading_fallback, code_examples=code_blob))

            return chunks
        except Exception as exc:
            logger.warning("Failed to parse HTML: %s", exc)
            return []

    def _chunk_plain_text(
        self,
        text: str,
        *,
        heading: str,
        section_ref: Optional[str] = None,
        code_examples: Optional[str] = None,
        chunk_size: int = 1200,
        overlap: int = 180,
    ) -> List[Dict[str, Any]]:
        text = (text or "").strip()
        if not text:
            return []

        chunks: List[Dict[str, Any]] = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + chunk_size, n)
            if end < n:
                soft_break = text.rfind("\n", start, end)
                if soft_break > start + (chunk_size // 2):
                    end = soft_break
            slice_text = text[start:end].strip()
            if slice_text:
                chunks.append(
                    {
                        "heading": heading,
                        "section_ref": section_ref,
                        "content": slice_text,
                        "code_examples": code_examples,
                    }
                )
            if end >= n:
                break
            start = max(end - overlap, start + 1)

        return chunks
