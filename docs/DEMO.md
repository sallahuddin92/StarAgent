# StarAgent Demo Flows

This doc is designed for quick live demos using the existing StarAgent runtime and preset packs.

Prereqs:

- Ollama running at `http://127.0.0.1:11434`
- Model installed (default: `gemma4:e2b`)
- StarAgent running on `http://127.0.0.1:8095`

Start:

```bash
./scripts/bootstrap_staragent.sh
./scripts/start_staragent.sh
```

Optional: open the dashboard:

```bash
open http://127.0.0.1:8095/dashboard
```

## Flow 1: repo_onboarding (read-only)

Purpose: quickly understand a repo and get an operator-friendly memo.

- Entry: preset pack `repo_onboarding`
- Expected primary artifact: `final_output.md`
- Approval required: no

CLI:

```bash
./scripts/staragent --project demo --conversation onboard-1 preset pack-run repo_onboarding --path . --question "What are the entry points and request flow?"
```

Then inspect artifacts (CLI):

```bash
./scripts/staragent --project demo --conversation onboard-1 task list --status completed
./scripts/staragent task artifacts <task_id>
./scripts/staragent task artifact <task_id> final_output.md --tail-lines 120
```

Dashboard:

- Open `http://127.0.0.1:8095/dashboard`
- Filter project to `demo`
- Select the completed task
- Click “Open primary”

## Flow 2: docs_digest (read-only)

Purpose: research a folder of docs and produce a short structured memo grounded in artifacts.

- Entry: preset pack `docs_digest`
- Expected primary artifact: `final_output.md`
- Approval required: no

CLI:

```bash
./scripts/staragent --project demo --conversation docs-1 preset pack-run docs_digest --path docs --question "Summarize how to operate StarAgent."
```

Inspect:

```bash
./scripts/staragent --project demo --conversation docs-1 task list
./scripts/staragent task artifact <task_id> final_output.md --tail-lines 160
```

## Flow 3: release_prep (stateful; approval-gated export)

Purpose: run a release review and export a report to `sandbox_test/` via approval.

- Entry: preset pack `release_prep`
- Expected output: `sandbox_test/<file>.md` (export), plus task artifacts under `.runtime/tasks/<task_id>/`
- Approval required: yes (for the export/write)

CLI:

```bash
./scripts/staragent --project demo --conversation rel-1 preset pack-run release_prep --path . --output sandbox_test/release_review_demo.md
```

The run should pause for approval. Then:

```bash
./scripts/staragent --project demo --conversation rel-1 task list --status paused
./scripts/staragent --project demo --conversation rel-1 task approve <task_id>
```

Confirm the export exists:

```bash
ls -la sandbox_test/release_review_demo.md
```

Dashboard:

- Filter project to `demo`
- Select the paused task to see the grounded approval banner
- After approving, open the exported file and the task’s primary artifact

Stop:

```bash
./scripts/stop_staragent.sh
```
