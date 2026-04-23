import unittest

from app.research_mode import ResearchPipeline, ResearchInputs


class _FakeLLM:
    async def text(self, messages, *, model=None, temperature=0.2, num_predict=None):
        return ""


class ResearchDatasetPlanTests(unittest.TestCase):
    def test_plan_steps_dataset_mode(self):
        rp = ResearchPipeline(_FakeLLM())
        steps = rp.plan_steps(ResearchInputs(root_path="/tmp/x", input_type="json_dataset", dataset_path="/tmp/x/d.jsonl"))
        self.assertEqual(steps[0]["step_type"], "dataset_profile")
        self.assertEqual(steps[1]["step_type"], "expand_dataset_steps")
        self.assertEqual(steps[2]["step_type"], "dataset_synthesis")
        self.assertEqual(steps[3]["step_type"], "dataset_theme_extraction")
        self.assertEqual(steps[4]["step_type"], "final_report")


if __name__ == "__main__":
    unittest.main()
