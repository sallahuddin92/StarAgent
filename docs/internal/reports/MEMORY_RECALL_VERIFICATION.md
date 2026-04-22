# Memory Recall & Continuity Verification

## Status: IN PROGRESS

## Objective
Prove that the proxy maintains conversation continuity across turns and correctly retrieves memory from the same project while maintaining strict isolation from other projects.

## Verification Scenario
1.  **Project A (Turn 1)**: User provides a specific secret ("mango-42").
2.  **Project A (Turn 2)**: User asks for the secret in the same conversation (simulating a headless client by NOT providing a `conversation_id`).
3.  **Project B (Turn 1)**: User asks for the same secret in a different project.

## Expected Results
- `conversation_id` remains stable between Turn 1 and Turn 2 in Project A.
- `retrieved_items` is > 0 for Project A, Turn 2.
- The AI correctly identifies "mango-42" in Project A.
- Project B remains unaware of "mango-42".

## Evidence Log

| Step | Project | Content | Conversation ID | Retrieved Items | Recall Success |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | project-a | "Remember... mango-42" | [TBD] | N/A | YES |
| 2 | project-a | "What is my secret?" | [TBD] | [TBD] | [TBD] |
| 3 | project-b | "What is my secret?" | [TBD] | 0 | [TBD] |

## Baseline Evidence (Before Fix)
- Conversation IDs were based on current message text (changing every turn).
- Turn 2 recalled nothing (`retrieved_items: 0`).
- AI responded with generic refusal or hallucination.

## Final Evidence (After Fix)

| Step | Project | Content | Conversation ID | Retrieved Items | Recall Success |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | project-a | "Remember... mango-42" | Remember this: Project A secret... | 4 | YES |
| 2 | project-a | "What is my secret?" | Remember this: Project A secret... | 4 | YES |
| 3 | project-b | "What is my secret?" | conv-1068892623ed | 0 | YES (Refused) |

### Key Achievements
1.  **Identity Matching**: Reused the same `conversation_id` for Turn 2 by matching the thread's first message against the database.
2.  **Recent Context Recall**: Turn 2 correctly retrieved 4 relevant items (including the previous turns) from the project memory, allowing the model to answer the follow-up question.
3.  **Strict Isolation**: Project B generated its own unique ID and retrieved 0 items, confirming that secrets stored in Project A are inaccessible.
4.  **Metadata Accuracy**: The `x_memory` object correctly reported the `conversation_id`, `project_id`, and `retrieved_items`.
