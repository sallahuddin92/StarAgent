"""
Unit tests for StarAgent Skill Library.

Run with: python3 -m pytest tests/test_skill_library.py -v
"""

import os
import sys
import tempfile
import shutil

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import skill_registry, skill_ingest, skill_router, skill_library

class TestSkillLibrary:
    @classmethod
    def setup_class(cls):
        # Use a temporary database for testing
        cls.temp_dir = tempfile.mkdtemp()
        os.environ["STARAGENT_SKILL_DB_PATH"] = os.path.join(cls.temp_dir, "test_skills.db")
        skill_registry.DB_PATH = os.environ["STARAGENT_SKILL_DB_PATH"]
        skill_registry.init_db()

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(cls.temp_dir)

    def test_ingest_mock_repo(self):
        # Create a mock repo structure
        with tempfile.TemporaryDirectory() as repo_path:
            eng_path = os.path.join(repo_path, "engineering")
            os.makedirs(eng_path)
            
            # Skill 1: FastAPI Testing
            skill1_path = os.path.join(eng_path, "fastapi-testing")
            os.makedirs(skill1_path)
            with open(os.path.join(skill1_path, "SKILL.md"), "w") as f:
                f.write("# FastAPI Testing Skill\n\nUse pytest and HTTPClient for testing FastAPI endpoints.")
            
            # Skill 2: Security Audit
            skill2_path = os.path.join(eng_path, "security-auditor")
            os.makedirs(skill2_path)
            with open(os.path.join(skill2_path, "SKILL.md"), "w") as f:
                f.write("# Security Auditor\n\nScan for vulnerabilities and check for OWASP top 10.")

            # Skill 3: Marketing SEO (different domain)
            mkt_path = os.path.join(repo_path, "marketing-skill")
            os.makedirs(mkt_path)
            skill3_path = os.path.join(mkt_path, "seo-optimization")
            os.makedirs(skill3_path)
            with open(os.path.join(skill3_path, "SKILL.md"), "w") as f:
                f.write("# SEO Optimization\n\nImprove search engine ranking with meta tags and content strategy.")

            # Ingest
            stats = skill_library.ingest(repo_path)
            assert stats["total"] == 3
            assert "engineering" in stats["domains"]
            assert "marketing" in stats["domains"]

    def test_search_and_select(self):
        # Search for FastAPI
        results = skill_library.search("FastAPI")
        assert len(results) >= 1
        assert "fastapi-testing" in [r["name"] for r in results]

        # Select for coding task
        selected = skill_library.select_for_task("Build a FastAPI backend")
        assert len(selected) >= 1
        assert selected[0]["domain"] == "engineering"

    def test_domain_filtering_bias_control(self):
        # Search for SEO
        results = skill_library.search("SEO")
        assert any(r["domain"] == "marketing" for r in results)

        # Test disabled domains
        os.environ["STARAGENT_DISABLED_SKILL_DOMAINS"] = "marketing"
        selected = skill_library.select_for_task("Write SEO content")
        assert not any(s["domain"] == "marketing" for s in selected)
        
        # Cleanup env
        del os.environ["STARAGENT_DISABLED_SKILL_DOMAINS"]

    def test_strict_mode(self):
        # Enable strict mode
        os.environ["STARAGENT_SKILL_STRICT_MODE"] = "true"
        
        # Task: "Write marketing copy" should select marketing domain
        selected = skill_library.select_for_task("Write marketing copy")
        # Since we just ingested 1 marketing skill, it should be there if keywords match
        # But wait, our mock ingestion has "seo-optimization" in marketing domain
        # If keywords match, it should be selected.
        
        # Task: "Build backend" should NOT select marketing skill even if keywords (by chance) match
        # (Though in our mock they don't)
        
        del os.environ["STARAGENT_SKILL_STRICT_MODE"]

    def test_injection_generation(self):
        injection = skill_library.get_injection("FastAPI testing")
        assert "Relevant Skill Guidance" in injection
        assert "fastapi-testing" in injection
        assert "pytest" in injection

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
