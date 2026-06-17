import logging
import os
import chromadb
from typing import List, Dict, Any, Optional
from chromadb.config import Settings
from .retrieval import EmbeddingModel

logger = logging.getLogger(__name__)

class VectorStore:
    """Semantic storage using ChromaDB and local sentence-transformers embeddings."""
    
    def __init__(self, embedding_model: EmbeddingModel, persist_directory: str = "data/chroma"):
        self.embedding_model = embedding_model
        self.persist_directory = persist_directory
        os.makedirs(persist_directory, exist_ok=True)
        
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_or_create_collection(
            name="research_knowledge",
            metadata={"hnsw:space": "cosine"} # Use cosine similarity
        )

    async def add_document(self, doc_id: str, text: str, metadata: Dict[str, Any]):
        """Embed and add a document to the vector store."""
        try:
            # We split the text into smaller chunks for better semantic retrieval
            chunks = self._chunk_text(text, chunk_size=1000)
            
            for i, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue
                    
                embedding = await self.embedding_model.embed(chunk)
                if not embedding:
                    continue
                    
                chunk_id = f"{doc_id}_{i}"
                chunk_metadata = metadata.copy()
                chunk_metadata["chunk"] = i
                
                self.collection.add(
                    ids=[chunk_id],
                    embeddings=[embedding],
                    metadatas=[chunk_metadata],
                    documents=[chunk]
                )
            logger.info(f"Successfully vectorized document {doc_id} in {len(chunks)} chunks.")
        except Exception as e:
            logger.error(f"Failed to vectorize document {doc_id}: {e}")

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search the vector store using a semantic query."""
        try:
            query_embedding = await self.embedding_model.embed(query)
            if not query_embedding:
                return []
                
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=limit
            )
            
            formatted = []
            if results["ids"] and results["ids"][0]:
                for i in range(len(results["ids"][0])):
                    formatted.append({
                        "id": results["ids"][0][i],
                        "document": results["documents"][0][i],
                        "metadata": results["metadatas"][0][i],
                        "distance": results["distances"][0][i]
                    })
            return formatted
        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return []

    def _chunk_text(self, text: str, chunk_size: int = 1000) -> List[str]:
        """Simple character-based chunking."""
        return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
