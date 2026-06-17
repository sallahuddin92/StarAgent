from __future__ import annotations

import subprocess
from pathlib import Path

from app.database import DatabaseManager
from app.docs_embeddings import DocsEmbeddingProvider
from app.docs_ingest import DocsIngester
from app.docs_search import DocsSearcher
from app.docs_store import DocsStore


# Backward-compat: legacy module name in repo is repairer.py
from app.repairer import DocsRepairer as LegacyDocsRepairer


def _make_blank_pdf(path: Path) -> None:
    # Tiny valid PDF structure that pypdf can parse without throwing errors
    content = (
        b"%PDF-1.4\n"
        b"1 0 obj <</Type /Catalog /Pages 2 0 R>> endobj\n"
        b"2 0 obj <</Type /Pages /Kids [] /Count 0>> endobj\n"
        b"xref\n0 3\n"
        b"0000000000 65535 f\n"
        b"0000000009 00000 n\n"
        b"0000000056 00000 n\n"
        b"trailer <</Size 3 /Root 1 0 R>>\n"
        b"startxref\n111\n"
        b"%%EOF\n"
    )
    path.write_bytes(content)


def _build_store(tmp_path: Path) -> DocsStore:
    db = DatabaseManager(str(tmp_path / "docs_rag.db"))
    embeddings = DocsEmbeddingProvider(provider="disabled")
    return DocsStore(db_manager=db, embedding_provider=embeddings)


def test_docs_ingest_search_and_ask_with_citations(tmp_path: Path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    (docs_dir / "fake_sdk.md").write_text(
        """
# FakeSDK Quickstart

Use `FakeSDK` for auth and retrieval.

```python
from fake_sdk import FakeSDK
sdk = FakeSDK()
sdk.login(token="dev-token", timeout=30)
user = sdk.fetch_user(user_id="42")
```
""".strip(),
        encoding="utf-8",
    )

    (docs_dir / "notes.txt").write_text(
        "Token must be passed as `token` argument. `api_key` is invalid for login.",
        encoding="utf-8",
    )

    (docs_dir / "reference.html").write_text(
        """
<html><body>
<h1>FakeSDK API</h1>
<p>Call login(token, timeout) before fetch_user(user_id).</p>
</body></html>
""".strip(),
        encoding="utf-8",
    )

    _make_blank_pdf(docs_dir / "appendix.pdf")

    store = _build_store(tmp_path)
    ingester = DocsIngester(store)
    searcher = DocsSearcher(store)

    ingest = ingester.ingest_path("proj-rag", str(docs_dir), source_type="project_docs")
    assert ingest["status"] == "success"
    assert ingest["files_processed"] == 4
    assert ingest["chunks_added"] >= 3

    results = searcher.search_structured("proj-rag", "FakeSDK login token", max_results=5)
    assert results, "Expected evidence-bearing docs search results"
    for row in results:
        assert row.get("chunk_id")
        assert row.get("source_path") or row.get("path_or_url")
        assert row.get("content")

    ask = searcher.ask("proj-rag", "How should I authenticate with FakeSDK?", max_results=4)
    assert ask["status"] == "ok"
    assert ask["citations"], "Answers must include citations"
    assert "insufficient" not in ask["answer"].lower()

    no_evidence = searcher.ask("proj-rag", "What is the Kubernetes API server port for this cluster?", max_results=3)
    assert no_evidence["status"] == "no_evidence"
    assert no_evidence["citations"] == []


def test_docs_driven_code_repair_and_pytest_pass(tmp_path: Path):
    project = tmp_path / "sample_project"
    project.mkdir(parents=True, exist_ok=True)

    docs = project / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "fake_sdk.md").write_text(
        """
# FakeSDK Usage

```python
from fake_sdk import FakeSDK
sdk = FakeSDK()
sdk.login(token="dev-token", timeout=30)
print(sdk.fetch_user(user_id="42"))
```
""".strip(),
        encoding="utf-8",
    )

    (project / "fake_sdk.py").write_text(
        """
class FakeSDK:
    def __init__(self):
        self._token = None

    def login(self, token: str, timeout: int = 30):
        self._token = token
        return {"ok": True, "timeout": timeout}

    def fetch_user(self, user_id: str):
        if not self._token:
            raise RuntimeError("not authenticated")
        return {"id": user_id, "token": self._token}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    (project / "main.py").write_text(
        """
from fake_sdk import FakeSDK


def run() -> dict:
    sdk = FakeSDK()
    sdk.login(api_key="bad-token")
    return sdk.fetch_user(user_id="42")


if __name__ == "__main__":
    print(run())
""".strip()
        + "\n",
        encoding="utf-8",
    )

    (project / "test_main.py").write_text(
        """
from main import run


def test_run_returns_user_dict():
    out = run()
    assert out["id"] == "42"
    assert out["token"] == "dev-token"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    before = subprocess.run(["python3", "main.py"], cwd=project, capture_output=True, text=True)
    assert before.returncode != 0
    traceback = (before.stderr or "") + "\n" + (before.stdout or "")

    store = _build_store(tmp_path)
    ingester = DocsIngester(store)
    searcher = DocsSearcher(store)
    ing = ingester.ingest_path("proj-repair", str(docs), source_type="project_docs")
    assert ing["status"] == "success"

    docs_result = searcher.search_for_error("proj-repair", traceback)

    repairer = LegacyDocsRepairer()
    main_path = project / "main.py"
    lines = main_path.read_text(encoding="utf-8").splitlines()
    line_no = next(i + 1 for i, l in enumerate(lines) if "sdk.login(" in l)
    old_line = lines[line_no - 1].strip()
    file_path = str(main_path)
    example = repairer.extract_example(docs_result, target_method="login")
    assert example is not None

    patched = repairer.apply_patch(file_path, line_no, old_line, example)
    assert patched, "Repair bridge should patch failing call using docs evidence"

    after = subprocess.run(["python3", "-m", "pytest", "-q"], cwd=project, capture_output=True, text=True)
    assert after.returncode == 0, after.stdout + "\n" + after.stderr
