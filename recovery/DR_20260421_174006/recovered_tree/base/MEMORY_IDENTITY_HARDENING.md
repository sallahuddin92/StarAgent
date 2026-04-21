# Hardening Audit: Conversation Identity Resolution

## Current Implementation Analysis
The current `_resolve_conversation_id` logic uses the following priority:
1. `request.conversation_id` (Explicit)
2. `request.user` (Implicit/Fallback)
3. `db.find_conversation_by_initial_message` (Heuristic/History match)
4. New UUID (Generation)

### Vulnerabilities Identified
1. **Initial Message Collision**: If two separate conversations in the same project start with "Hello", the second one will be matched to the first one's `conversation_id`. This merges the memory of two distinct user sessions.
2. **User ID Overload**: Using `request.user` as the primary key for the conversation means a single user cannot have multiple parallel threads in the same project without them sharing the exact same recent history and memory state.
3. **Stateless Resumption**: If a client restarts and loses its issued `conversation_id`, but re-sends history starting with a common message like "Hi", it will collide with the first thread it finds in the project that also started with "Hi".

## Proposed Hardening Strategy
1. **Fingerprint Depth**: Match against both the first user message **and** the first assistant response. This significantly reduces collision probability.
2. **Phase-Aware Resolution**: 
   - **Initial Turns**: Always generate a new ID if it's the first message of a thread (no assistant history).
   - **Continuation Turns**: Only reuse IDs if the incoming history matches a known starting point.
3. **Isolation Consistency**: Ensure `project_id` is always a component of the lookup to prevent cross-project leakage.

## Implementation Details
- Update `DatabaseManager.find_conversation_by_history_fingerprint(m1, a1, project_id)`.
- Update `app/main.py` to calculate the fingerprint from `request.messages`.
- Add collision-specific tests.
