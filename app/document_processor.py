import os
import logging
from typing import List, Dict, Any
from .vector_store import VectorStore

logger = logging.getLogger(__name__)

class DocumentProcessor:
    """Processes local documents (PDFs, etc.) and indexes them into VectorStore."""
    
    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store

    async def index_file(self, file_path: str, project_id: str = "default") -> bool:
        """Extract text from a single file and index it."""
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return False
            
        ext = os.path.splitext(file_path)[1].lower()
        content = ""
        metadata = {
            "source": "local_file",
            "path": file_path,
            "filename": os.path.basename(file_path),
            "project_id": project_id
        }

        try:
            if ext == ".pdf":
                content = self._extract_pdf(file_path)
            elif ext in (".txt", ".md", ".py", ".js", ".json"):
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            else:
                logger.warning(f"Unsupported file type: {ext}")
                return False

            if not content.strip():
                logger.warning(f"No content extracted from {file_path}")
                return False

            # Add to vector store
            doc_id = f"doc_{hash(file_path)}"
            await self.vector_store.add_document(doc_id, content, metadata)
            logger.info(f"Indexed local file: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to index file {file_path}: {e}")
            return False

    async def index_folder(self, folder_path: str, project_id: str = "default") -> Dict[str, Any]:
        """Scan a folder and index all supported documents."""
        if not os.path.isdir(folder_path):
            return {"error": f"Not a directory: {folder_path}"}

        indexed_count = 0
        failed_count = 0
        
        for root, _, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                success = await self.index_file(file_path, project_id)
                if success:
                    indexed_count += 1
                else:
                    failed_count += 1
                    
        return {
            "status": "complete",
            "indexed": indexed_count,
            "failed": failed_count,
            "folder": folder_path
        }

    def _extract_pdf(self, file_path: str) -> str:
        """Extract text from a PDF file."""
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        return text
