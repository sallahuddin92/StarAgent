import sys
import importlib
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

def test_normalize_model_name():
    from app.model_registry import registry
    assert registry.normalize_model_name("Gemma4:e2b") == "gemma4:e2b"
    assert registry.normalize_model_name("gemma4") == "gemma4:latest"
    assert registry.normalize_model_name("  gemma4  ") == "gemma4:latest"
    assert registry.normalize_model_name("") == ":latest"


def test_is_local_ollama_model_installed():
    from app.model_registry import ModelRegistry
    reg = ModelRegistry()
    # Mock list_local_ollama_models to return a set of names
    reg.list_local_ollama_models = MagicMock(return_value=[
        {"name": "gemma4:e2b"},
        {"name": "qwen2.5-coder:14b"},
        {"name": "deepseek-coder:latest"},
    ])
    
    assert reg.is_local_ollama_model_installed("gemma4:e2b") is True
    assert reg.is_local_ollama_model_installed("gemma4:E2B") is True
    assert reg.is_local_ollama_model_installed("deepseek-coder") is True
    assert reg.is_local_ollama_model_installed("deepseek-coder:latest") is True
    assert reg.is_local_ollama_model_installed("gemma4") is False
    assert reg.is_local_ollama_model_installed("unknown") is False


def test_inspect_model_installed_check():
    from app.model_registry import ModelRegistry
    reg = ModelRegistry()
    reg.list_local_ollama_models = MagicMock(return_value=[
        {"name": "gemma4:e2b", "size": 1000},
    ])
    
    res = reg.inspect_model("gemma4:E2B")
    assert res["installed"] is True
    assert res["size"] == 1000
    
    res = reg.inspect_model("unknown:latest")
    assert res["installed"] is False


def test_lazy_load_retrieval_embedding_model():
    from app.retrieval import EmbeddingModel
    import app.retrieval as ret
    import types
    
    # Reset singleton model cache
    ret._SENTENCE_TRANSFORMER_MODEL = None
    
    # Create an EmbeddingModel with use_ollama=False
    with patch("importlib.util.find_spec", return_value=MagicMock()), \
         patch.object(ret, "HAS_SENTENCE_TRANSFORMERS", True):
        # Mock SentenceTransformer inside the local import scope of _get_sentence_transformer
        mock_st_class = MagicMock()
        mock_module = types.ModuleType("sentence_transformers")
        mock_module.SentenceTransformer = mock_st_class
        with patch.dict(sys.modules, {"sentence_transformers": mock_module}):
            em = EmbeddingModel(use_ollama=False)
            
            # The model shouldn't be loaded at instantiation time
            assert ret._SENTENCE_TRANSFORMER_MODEL is None
            
            # Accessing the property should load the model
            model = em.model
            assert model is not None
            assert ret._SENTENCE_TRANSFORMER_MODEL is not None
            
            # Creating another EmbeddingModel should reuse the cached model
            em2 = EmbeddingModel(use_ollama=False)
            assert em2.model is model


def test_lazy_load_docs_embedding_provider():
    from app.docs_embeddings import DocsEmbeddingProvider
    import app.docs_embeddings as de
    import types
    
    # Reset singleton models cache
    de._DOCS_EMBEDDING_MODELS.clear()
    
    with patch("importlib.util.find_spec", return_value=MagicMock()), \
         patch.object(de, "_HAS_SENTENCE_TRANSFORMERS", True):
        mock_st_class = MagicMock()
        mock_module = types.ModuleType("sentence_transformers")
        mock_module.SentenceTransformer = mock_st_class
        with patch.dict(sys.modules, {"sentence_transformers": mock_module}):
            provider = DocsEmbeddingProvider(provider="local", model_name="all-MiniLM-L6-v2")
            
            # Should not be loaded in __init__
            assert "all-MiniLM-L6-v2" not in de._DOCS_EMBEDDING_MODELS
            
            # Accessing _model should load it
            model = provider._model
            assert model is not None
            assert "all-MiniLM-L6-v2" in de._DOCS_EMBEDDING_MODELS
            
            # Another provider with same model name should reuse it
            provider2 = DocsEmbeddingProvider(provider="local", model_name="all-MiniLM-L6-v2")
            assert provider2._model is model


def test_import_app_main_does_not_load_embeddings():
    import app.retrieval as ret
    import app.docs_embeddings as de
    
    # Ensure caches are empty
    ret._SENTENCE_TRANSFORMER_MODEL = None
    de._DOCS_EMBEDDING_MODELS.clear()
    
    # Unimport app.main if it is imported
    if "app.main" in sys.modules:
        del sys.modules["app.main"]
        
    # Import app.main
    importlib.import_module("app.main")
    
    # Verify that retrieval and docs_embeddings singletons remain uninitialized
    assert ret._SENTENCE_TRANSFORMER_MODEL is None
    assert len(de._DOCS_EMBEDDING_MODELS) == 0
