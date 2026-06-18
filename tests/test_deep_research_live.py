import os
import sys
import json
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.research_reader import ResearchReader
from app.research_providers import LocalDocsProvider, ManualUrlsProvider, WebSearchStubProvider
from app.evidence_engine import EvidenceEngine
from app.gate_engine import GateEngine

def test_research_reader_local_file():
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
        tmp.write(b"<html><head><title>Test Doc</title></head><body><h1>Hello StarAgent</h1><script>alert(1)</script></body></html>")
        tmp_path = tmp.name

    try:
        reader = ResearchReader()
        res = reader.fetch_and_clean(f"file://{tmp_path}", "test_run", "S1")
        assert res["source_id"] == "S1"
        assert res["title"] == "Test Doc"
        assert "Hello StarAgent" in Path(res["file_path"]).read_text(encoding="utf-8")
        assert "alert" not in Path(res["file_path"]).read_text(encoding="utf-8")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def test_local_docs_provider_fallback():
    # Test that it gracefully handles missing docs searcher
    provider = LocalDocsProvider(docs_searcher=None, use_global_fallback=False)
    results = provider.search("test query")
    assert results == []

def test_empty_provider_limitation_report():
    engine = EvidenceEngine(llm_client=None)
    # If no sources provided, it should return a limitation report
    report = "".join(
        async_to_sync(
            engine.write_final_report("What is StarAgent?", [], [], [], [], "outline", "mock_model")
        )
    )
    assert "No configured live sources were available." in report
    assert "[S1]" not in report

def test_evidence_extraction_and_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        src_file = Path(tmpdir) / "source_text.txt"
        src_file.write_text("StarAgent is an agentic AI coding assistant developed by Google DeepMind.", encoding="utf-8")

        sources = [{
            "source_id": "S1",
            "title": "Google DeepMind release",
            "url": "https://deepmind.google/staragent",
            "file_path": str(src_file)
        }]

        engine = EvidenceEngine(llm_client=None)
        items = async_to_sync(
            engine.extract_evidence_items(sources, "test_run", "Who developed StarAgent?", "mock_model")
        )
        assert len(items) > 0
        assert items[0]["source_id"] == "S1"
        assert items[0]["evidence_id"] == "E1"
        assert "StarAgent" in items[0]["quote"]


def test_markdown_bullets_extract_evidence():
    """Rule-based extraction should handle markdown bullet points."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_file = Path(tmpdir) / "bullets.md"
        src_file.write_text(
            "# Key Features\n\n"
            "- **Serverless**: No separate server process.\n"
            "- **Zero Configuration**: No setup required.\n"
            "- **ACID Compliant**: Transactions are atomic.\n"
            "- **Single Writer**: Only one writer at a time.\n"
            "- **Lightweight**: Library size under 600KB.\n",
            encoding="utf-8",
        )
        sources = [{"source_id": "S1", "title": "Features", "url": "file:///bullets", "file_path": str(src_file)}]
        engine = EvidenceEngine(llm_client=None)
        items = async_to_sync(
            engine.extract_evidence_items(sources, "test_bullets", "What are the features?", "mock_model")
        )
        assert len(items) >= 3, f"Expected at least 3 evidence items from bullets, got {len(items)}"
        for item in items:
            assert item.get("assertion"), "Missing assertion"


def test_markdown_tables_extract_evidence():
    """Rule-based extraction should handle markdown table rows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_file = Path(tmpdir) / "table.md"
        src_file.write_text(
            "# Comparison Table\n\n"
            "| Aspect | SQLite | PostgreSQL |\n"
            "|--------|--------|------------|\n"
            "| Setup | None | Requires configuration |\n"
            "| Concurrency | Single writer | Many concurrent writers |\n"
            "| Management | File-based | Server-based |\n"
            "| Cost | Free | Free, hosting costs apply |\n",
            encoding="utf-8",
        )
        sources = [{"source_id": "S1", "title": "Comparison", "url": "file:///table", "file_path": str(src_file)}]
        engine = EvidenceEngine(llm_client=None)
        items = async_to_sync(
            engine.extract_evidence_items(sources, "test_table", "Compare SQLite and PostgreSQL", "mock_model")
        )
        assert len(items) >= 2, f"Expected at least 2 evidence items from table, got {len(items)}"


def test_multi_source_evidence_accepted_count():
    """Multiple benchmark markdown files should produce accepted evidence >= 3."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_files = {
            "sqlite.md": (
                "# SQLite Overview\n\n"
                "SQLite is a serverless SQL database. It requires zero configuration.\n\n"
                "## Key Characteristics\n\n"
                "- **Serverless**: No separate server process.\n"
                "- **Zero Configuration**: No setup or administration required.\n"
                "- **ACID Compliant**: Transactions are atomic and durable.\n"
                "- **Lightweight**: Library size under 600KB.\n"
            ),
            "postgres.md": (
                "# PostgreSQL Overview\n\n"
                "PostgreSQL is an open-source database with client-server architecture.\n\n"
                "## Key Characteristics\n\n"
                "- **Client-Server**: Runs as a separate server process.\n"
                "- **High Concurrency**: Supports many concurrent writers.\n"
                "- **Full ACID**: Strong transaction guarantees with MVCC.\n"
                "- **Advanced Features**: Full-text search, JSON support.\n"
            ),
            "comparison.md": (
                "# Comparison Notes\n\n"
                "## Trade-offs Summary\n\n"
                "| Aspect | SQLite | PostgreSQL |\n"
                "|--------|--------|------------|\n"
                "| Setup | None | Requires configuration |\n"
                "| Concurrency | Single writer | Many concurrent writers |\n"
                "| Scalability | Single machine | Horizontal scaling |\n"
            ),
        }
        sources = []
        for fname, content in src_files.items():
            path = Path(tmpdir) / fname
            path.write_text(content, encoding="utf-8")
            sid = fname.replace(".md", "").upper()
            sources.append({"source_id": sid, "title": fname, "url": f"file:///{fname}", "file_path": str(path)})

        engine = EvidenceEngine(llm_client=None)
        items = async_to_sync(
            engine.extract_evidence_items(
                sources, "test_multi", "Compare SQLite and PostgreSQL tradeoffs", "mock_model"
            )
        )
        assert len(items) >= 6, f"Expected >= 6 evidence items, got {len(items)}"
        accepted = [e for e in items if e.get("accepted", True)]
        assert len(accepted) >= 3, f"Expected >= 3 accepted evidence, got {len(accepted)}"
        source_ids = {e["source_id"] for e in items}
        assert len(source_ids) >= 2, f"Expected evidence from >= 2 sources, got {source_ids}"

def test_citation_audit_validation():
    engine = EvidenceEngine(llm_client=None)
    sources = [{"source_id": "S1", "title": "Source 1", "url": "url"}]
    evidence = [{"evidence_id": "E1", "source_id": "S1", "quote": "quote", "assertion": "assertion"}]
    
    # Passing audit
    report_pass = "StarAgent is built by Google DeepMind [S1] and it runs tests [E1]."
    res_pass = engine.citation_audit(report_pass, sources, evidence)
    assert res_pass["status"] == "passed"
    assert "S1" in res_pass["verified_citations"]
    assert "E1" in res_pass["verified_citations"]
    assert len(res_pass["unresolved"]) == 0
    
    # Failing audit (missing citation reference)
    report_fail = "StarAgent has a terminal UI [S2] and some unsourced evidence [E2]."
    res_fail = engine.citation_audit(report_fail, sources, evidence)
    assert res_fail["status"] == "failed"
    assert "S2" in res_fail["unresolved"]
    assert "E2" in res_fail["unresolved"]

def test_no_unsourced_claims_gate():
    with tempfile.TemporaryDirectory() as tmpdir:
        gate_eng = GateEngine(workspace_root=tmpdir)
        run_id = "test_run_gate"
        wf_dir = Path(tmpdir) / ".runtime" / "workflows" / run_id
        wf_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Unresolved citations present
        audit_content_fail = "# Citation Audit\n- Unresolved: S2, E2\n"
        (wf_dir / "citation_audit.md").write_text(audit_content_fail, encoding="utf-8")
        status, msg = gate_eng._gate_no_unsourced_claims({}, {"run_id": run_id, "workspace_root": tmpdir})
        assert status == "fail"
        
        # 2. No unresolved citations
        audit_content_pass = "# Citation Audit\n- Unresolved: None.\n"
        (wf_dir / "citation_audit.md").write_text(audit_content_pass, encoding="utf-8")
        status, msg = gate_eng._gate_no_unsourced_claims({}, {"run_id": run_id, "workspace_root": tmpdir})
        assert status == "pass"

def test_final_report_generation_citations():
    engine = EvidenceEngine(llm_client=None)
    sources = [{"source_id": "S1", "title": "Source 1", "url": "url"}]
    evidence = [{"evidence_id": "E1", "source_id": "S1", "quote": "quote", "assertion": "assertion"}]
    claims = [{"claim_id": "C1", "claim_text": "Claim text", "supporting_evidence_ids": ["E1"], "status": "consensus"}]
    contradictions = []
    
    report = async_to_sync(
        engine.write_final_report("What is StarAgent?", sources, evidence, claims, contradictions, "outline", "mock_model")
    )
    assert "[S1]" in report
    assert "[E1]" in report
    assert "## Summary" in report
    assert "## Evidence Table" in report

def test_tui_render_import():
    from cli.tui import draw_details
    assert draw_details is not None

# Helper to run async methods synchronously for testing
def async_to_sync(coro):
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)
