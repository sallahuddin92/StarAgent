import os
import sys
import tempfile
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.gate_engine import GateEngine
from app.workflow_engine import WORKFLOW_TEMPLATES

def test_deep_research_template_structure():
    assert "deep_research" in WORKFLOW_TEMPLATES
    stages = WORKFLOW_TEMPLATES["deep_research"]["stages"]
    assert len(stages) == 9
    stage_names = [s["name"] for s in stages]
    expected = [
        "scope", "source_plan", "collect_sources", "extract_evidence",
        "compare_claims", "synthesize", "verify_citations", "write_report", "review"
    ]
    assert stage_names == expected

def test_custom_research_gates_evaluation():
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = GateEngine(workspace_root=tmpdir)
        run_id = "test_run_123"
        wf_dir = Path(tmpdir) / ".runtime" / "workflows" / run_id
        wf_dir.mkdir(parents=True, exist_ok=True)
        
        # Write mock sources.json
        sources = [
            {"title": "Source 1", "url": "https://example1.com/page"},
            {"title": "Source 2", "url": "https://example2.com/page"},
            {"title": "Source 3", "url": "https://example1.com/another"}
        ]
        (wf_dir / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
        
        # Write mock claims_matrix.md
        claims = "# Claims Matrix\n\n- Contradiction: yes, this is a conflict.\n"
        (wf_dir / "claims_matrix.md").write_text(claims, encoding="utf-8")
        
        # Write mock citation_audit.md
        audit = "# Citation Audit\n- Claim 1: verified\n- Unresolved: None.\n"
        (wf_dir / "citation_audit.md").write_text(audit, encoding="utf-8")
        
        # Write mock final_report.md
        report = '# Final Report\n\n"This is a quote" [source-1] [source-2] [source-3]\n'
        (wf_dir / "final_report.md").write_text(report, encoding="utf-8")
        
        context = {"run_id": run_id, "workspace_root": tmpdir}
        
        # Test count min
        status, msg = engine._gate_source_count_min({"min_count": 3}, context)
        assert status == "pass"
        status, msg = engine._gate_source_count_min({"min_count": 5}, context)
        assert status == "fail"
        
        # Test diversity
        status, msg = engine._gate_source_diversity({"min_domains": 2}, context)
        assert status == "pass"
        status, msg = engine._gate_source_diversity({"min_domains": 3}, context)
        assert status == "fail"
        
        # Test contradiction
        status, msg = engine._gate_contradiction_check({}, context)
        assert status == "pass"
        
        # Test unsourced claims
        status, msg = engine._gate_no_unsourced_claims({}, context)
        assert status == "pass"
        
        # Test citation required
        status, msg = engine._gate_citation_required({"min_citations": 3}, context)
        assert status == "pass"
        status, msg = engine._gate_citation_required({"min_citations": 4}, context)
        assert status == "fail"
        
        # Test quote limit
        status, msg = engine._gate_quote_limit({"max_quotes": 5}, context)
        assert status == "pass"

def test_tui_import():
    from cli.tui import run_tui_dashboard
    assert run_tui_dashboard is not None
