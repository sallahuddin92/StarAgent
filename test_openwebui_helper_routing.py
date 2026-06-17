import unittest
import asyncio

from app.models import ChatCompletionRequest, ChatMessage, MemoryState
from app.routing import determine_execution_route, FAST_PATH, AGENT_PATH


def _req(text: str) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="gemma4:e2b",
        stream=False,
        project_id="test",
        conversation_id="test",
        messages=[ChatMessage(role="user", content=text)],
    )


def _route(req: ChatCompletionRequest, memory: MemoryState) -> str:
    return asyncio.run(determine_execution_route(req, memory))


class OpenWebUIHelperRoutingTests(unittest.TestCase):
    def test_helper_prompt_bypass_test(self):
        memory = MemoryState(conversation_id="c1", project_id="p1")
        helper = """### Task: Generate a concise title

### Conversation
User: Create a file at sandbox_test/x.txt
Assistant: awaiting approval
"""
        self.assertEqual(_route(_req(helper), memory), FAST_PATH)

    def test_title_generation_no_agent_test(self):
        memory = MemoryState(conversation_id="c2", project_id="p1")
        prompt = "### Task: Generate a concise title\n\nReturn only the title."
        self.assertEqual(_route(_req(prompt), memory), FAST_PATH)

    def test_followup_generation_no_agent_test(self):
        memory = MemoryState(conversation_id="c3", project_id="p1")
        prompt = "### Task: Suggest 3-5 relevant follow-up questions\n\nConversation: ..."
        self.assertEqual(_route(_req(prompt), memory), FAST_PATH)

    def test_tag_generation_no_agent_test(self):
        memory = MemoryState(conversation_id="c4", project_id="p1")
        prompt = "### Task: Generate 1-3 broad tags\n\nConversation: ..."
        self.assertEqual(_route(_req(prompt), memory), FAST_PATH)

    def test_embedded_write_history_no_agent_test(self):
        memory = MemoryState(conversation_id="c5", project_id="p1")
        prompt = """### Task: Generate a concise title

### Conversation
User: Inspect the app folder
Assistant: Main API entry file: app/main.py
User: Create a file at sandbox_test/webui_check_2.txt with exact content: hi
Assistant: awaiting approval
User: yes
"""
        self.assertEqual(_route(_req(prompt), memory), FAST_PATH)

    def test_approval_yes_not_hijacked_by_helper_test(self):
        memory = MemoryState(conversation_id="c6", project_id="p1")
        memory.pending_approval = {"tool_call": {"function": {"name": "write_file"}}}

        helper = "### Task: Generate a concise title\n\nConversation includes: yes create file"
        self.assertEqual(_route(_req(helper), memory), FAST_PATH)

        # Real operator approval should still resume.
        self.assertEqual(_route(_req("yes"), memory), AGENT_PATH)


if __name__ == "__main__":
    unittest.main()
