"""
Semantic retrieval engine with embedding-based search.
Supports local embeddings via sentence-transformers or Ollama embeddings.
"""

import logging
import json
import numpy as np
from typing import List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Import dependencies
try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False
    logger.warning("sentence-transformers not available, using heuristic search fallback")


@dataclass
class RetrievedItem:
    """Retrieved memory item with similarity score."""
    category: str
    content: str
    score: float


class EmbeddingModel:
    """Handles embedding generation locally or via Ollama."""
    
    def __init__(self, use_ollama: bool = False, ollama_url: str = "http://127.0.0.1:11434"):
        self.use_ollama = use_ollama
        self.ollama_url = ollama_url
        self.model = None
        
        if not use_ollama and HAS_SENTENCE_TRANSFORMERS:
            try:
                # Use lightweight model for local deployment
                self.model = SentenceTransformer("all-MiniLM-L6-v2")
                logger.info("Loaded sentence-transformers model: all-MiniLM-L6-v2")
            except Exception as e:
                logger.error(f"Failed to load embedding model: {e}")
                self.use_ollama = True
    
    async def embed(self, text: str) -> Optional[List[float]]:
        """Generate embedding for text."""
        if not text:
            return None
        
        try:
            if self.use_ollama:
                return await self._embed_with_ollama(text)
            elif self.model:
                embedding = self.model.encode(text, convert_to_numpy=True)
                return embedding.tolist()
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
        
        return None
    
    async def _embed_with_ollama(self, text: str) -> Optional[List[float]]:
        """Generate embedding via Ollama embeddings endpoint."""
        import httpx
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/embeddings",
                    json={
                        "model": "nomic-embed-text",  # Lightweight embedding model
                        "prompt": text
                    }
                )
                if response.status_code == 200:
                    return response.json().get("embedding")
        except Exception as e:
            logger.error(f"Ollama embedding request failed: {e}")
        
        return None
    
    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """Generate embeddings for multiple texts (synchronous)."""
        if self.use_ollama:
            # For Ollama, fall back to heuristic search
            return [None] * len(texts)
        
        if not self.model or not texts:
            return [None] * len(texts)
        
        try:
            embeddings = self.model.encode(texts, convert_to_numpy=True)
            return [emb.tolist() for emb in embeddings]
        except Exception as e:
            logger.error(f"Batch embedding failed: {e}")
            return [None] * len(texts)


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b:
        return 0.0
    
    a = np.array(a)
    b = np.array(b)
    
    dot_product = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return float(dot_product / (norm_a * norm_b))


class SemanticRetriever:
    """Semantic search over memory items using embeddings or fallback heuristics."""
    
    # Stopwords for heuristic fallback
    STOPWORDS = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'is', 'was', 'are', 'been', 'be',
        'have', 'has', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'can', 'i', 'you', 'he', 'she',
        'it', 'we', 'they', 'what', 'which', 'who', 'why', 'how', 'where', 'when'
    }
    
    def __init__(self, embedding_model: Optional[EmbeddingModel] = None):
        self.embedding_model = embedding_model or EmbeddingModel()
    
    async def retrieve(
        self,
        query: str,
        memory_items: List[dict],
        top_k: int = 6,
        use_semantic: bool = True
    ) -> List[RetrievedItem]:
        """
        Retrieve top-k most relevant memory items.
        Falls back to heuristic search if embeddings unavailable.
        """
        if not memory_items:
            return []
        
        if use_semantic and self.embedding_model.model:
            return await self._semantic_retrieve(query, memory_items, top_k)
        else:
            return self._heuristic_retrieve(query, memory_items, top_k)
    
    async def _semantic_retrieve(
        self,
        query: str,
        memory_items: List[dict],
        top_k: int
    ) -> List[RetrievedItem]:
        """Retrieve using semantic similarity."""
        # Generate query embedding
        query_embedding = await self.embedding_model.embed(query)
        if not query_embedding:
            return self._heuristic_retrieve(query, memory_items, top_k)
        
        scores = []
        for item in memory_items:
            content = item.get("content", "")
            embedding = item.get("embedding")
            
            # Parse embedding if stored as JSON string
            if isinstance(embedding, str):
                try:
                    embedding = json.loads(embedding)
                except (json.JSONDecodeError, TypeError):
                    embedding = None
            
            if embedding:
                score = cosine_similarity(query_embedding, embedding)
                scores.append((score, item))
        
        # Sort by score and return top-k
        scores.sort(key=lambda x: x[0], reverse=True)
        return [
            RetrievedItem(
                category=item.get("category", "unknown"),
                content=item.get("content", ""),
                score=float(score)
            )
            for score, item in scores[:top_k]
        ]
    
    def _heuristic_retrieve(
        self,
        query: str,
        memory_items: List[dict],
        top_k: int
    ) -> List[RetrievedItem]:
        """Retrieve using keyword-based heuristic matching (fallback)."""
        query_terms = self._extract_keywords(query)
        
        if not query_terms:
            # Return first top_k items if no keywords extracted
            return [
                RetrievedItem(
                    category=item.get("category", "unknown"),
                    content=item.get("content", ""),
                    score=0.5
                )
                for item in memory_items[:top_k]
            ]
        
        scores = []
        for item in memory_items:
            content = item.get("content", "")
            item_terms = self._extract_keywords(content)
            
            # Compute Jaccard similarity
            if item_terms:
                intersection = len(query_terms & item_terms)
                union = len(query_terms | item_terms)
                score = intersection / union if union > 0 else 0
            else:
                score = 0
            
            scores.append((score, item))
        
        # Sort and return top-k
        scores.sort(key=lambda x: x[0], reverse=True)
        return [
            RetrievedItem(
                category=item.get("category", "unknown"),
                content=item.get("content", ""),
                score=float(score)
            )
            for score, item in scores[:top_k]
            if score > 0  # Only return items with non-zero score
        ]
    
    @staticmethod
    def _extract_keywords(text: str) -> set:
        """Extract meaningful keywords from text."""
        words = text.lower().split()
        keywords = {
            w.strip('.,!?;:') for w in words
            if len(w.strip('.,!?;:')) > 2 and w.lower() not in SemanticRetriever.STOPWORDS
        }
        return keywords


async def retrieve_context(
    query: str,
    db_memory_items: List,
    top_k: int = 6,
    embedding_model: Optional[EmbeddingModel] = None
) -> List[RetrievedItem]:
    """
    Convenience function to retrieve relevant context items.
    
    Args:
        query: User query or message
        db_memory_items: List of MemoryItem objects from database
        top_k: Number of items to retrieve
        embedding_model: Optional embedding model instance
    
    Returns:
        List of RetrievedItem with scores
    """
    if embedding_model is None:
        embedding_model = EmbeddingModel()
    
    retriever = SemanticRetriever(embedding_model)
    
    # Convert database objects to dictionaries
    items_dicts = [
        {
            "category": item.category,
            "content": item.content,
            "embedding": item.embedding
        }
        for item in db_memory_items
    ]
    
    return await retriever.retrieve(query, items_dicts, top_k=top_k)
