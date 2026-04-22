# StarAgent Dashboard

StarAgent includes a lightweight local dashboard at:

- `http://127.0.0.1:8095/dashboard`

This UI is intentionally thin: it calls the existing authenticated `/v1/*` endpoints and does not implement parallel business logic.

## Screenshots

![Dashboard Task List](assets/dashboard_tasks.png)

![Dashboard Approval Required](assets/dashboard_approval.png)

![Dashboard Task Detail + Artifact Preview](assets/dashboard_detail.png)

## What You Can Do

- Browse tasks (completed/partial/paused/failed) and see clear status badges.
- Filter tasks by:
  - status
  - project
  - task type
  - pack/preset (when tasks were created via preset packs)
- Inspect task detail:
  - current step and progress
  - retry count
  - final verdict/summary (when present)
  - primary artifact (quick-open)
- Browse artifacts (with primary artifact highlighted).
- Preview artifacts (markdown/text; JSON pretty-prints via the API).
- Preview logs (tail of step summaries/verifier results).
- Take action on tasks using existing runtime endpoints:
  - Continue (bounded)
  - Approve / Reject (only when approval is required)
- Launch preset packs from a small form (read-only packs and stateful packs).

## Authentication

The dashboard requires an API key to call `/v1/*`.

- It stores the key in your browser local storage.
- StarAgent still enforces the API key on every request; the dashboard does not bypass auth.

## Approvals (Grounded)

When a task is paused for approval, the dashboard shows a grounded “Approval required” banner derived from the pending tool call payload, typically including:

- action type (e.g., `write_file`)
- target path/file
- a short summary of what will happen (for common write/export actions)

Approving or rejecting calls the existing `/v1/tasks/{task_id}/continue` endpoint with `action: approve|reject`.

## Safety Notes

- Any stateful export/write should remain scoped to `sandbox_test/` by default.
- If you expose StarAgent beyond localhost, harden transport and auth before using the dashboard on untrusted networks.
