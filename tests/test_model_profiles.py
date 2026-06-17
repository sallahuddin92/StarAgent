import os
import unittest
from app.model_profiles import get_active_profile, GENERIC_SMALL_LOCAL, GEMMA4_E2B_BASELINE, GENERIC_API_CAPABLE

class TestModelProfiles(unittest.TestCase):
    def test_gemma_baseline(self):
        profile = get_active_profile("ollama", "gemma4:e2b")
        self.assertEqual(profile.name, "gemma4_e2b_baseline")
        self.assertTrue(profile.small_model_mode)
        self.assertEqual(profile.preferred_tool_protocol, "json")

    def test_openai_capable(self):
        profile = get_active_profile("openai", "gpt-4o")
        self.assertEqual(profile.name, "generic_api_capable")
        self.assertFalse(profile.small_model_mode)
        self.assertEqual(profile.preferred_tool_protocol, "native")

    def test_anthropic_capable(self):
        profile = get_active_profile("anthropic", "claude-3-5-sonnet")
        self.assertEqual(profile.name, "generic_api_capable")

    def test_unknown_model_fallback(self):
        # Unknown provider/model should fallback to generic_small_local or generic_api_capable depending on provider
        profile = get_active_profile("unknown_provider", "some_model")
        self.assertEqual(profile.name, "generic_small_local")
        
        profile = get_active_profile("ollama", "mystery_model")
        self.assertEqual(profile.name, "generic_small_local")

    def test_longcat_profile(self):
        profile = get_active_profile("longcat", "LongCat-Flash-Thinking")
        self.assertEqual(profile.name, "generic_api_capable")

if __name__ == "__main__":
    unittest.main()
