import sys
import os
import json

# Add project root to sys path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__name__), "..")))

from app.docs_store import DocsStore
from app.docs_ingest import DocsIngester
from app.docs_search import DocsSearcher

def test_docs_flow():
    print("Testing Docs Store Initialization...")
    store = DocsStore()
    ingester = DocsIngester(store)
    searcher = DocsSearcher(store)
    
    print("\n1. Testing Package Ingestion (FastAPI)")
    res = ingester.ingest_package("test_project", "fastapi")
    print(f"Result: {res}")
    
    print("\n2. Testing Search for FastAPI")
    search_res = store.search("test_project", "FastAPI middleware", package_name="fastapi")
    print(f"Found {len(search_res)} chunks")
    for r in search_res:
        print(f" - {r['heading'][:50]}")
        
    print("\n3. Testing Error Heuristic Search")
    error_msg = "AttributeError: 'FastAPI' object has no attribute 'listen'"
    error_res = searcher.search_for_error("test_project", error_msg)
    print(f"Heuristic Search length: {len(error_res)} chars")
    
    print("\nTesting Completed.")

if __name__ == "__main__":
    test_docs_flow()
