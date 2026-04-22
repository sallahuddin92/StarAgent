# StarAgent Presets And Packs

StarAgent presets are operator-friendly wrappers over the existing bounded task engine profiles. They create task runs, write artifacts under `.runtime/tasks/<task_id>/`, and may pause for approval when a stateful export/write is requested.

Preset packs are curated flows that run one or more presets in sequence.

## Quick Usage

List presets and packs:

```bash
./scripts/staragent preset list
./scripts/staragent preset packs
```

Print copy-paste examples:

```bash
./scripts/staragent preset examples
```

## Packs (Recommended Entry Points)

### `repo_onboarding` (read-only)

- Runs: `quick_repo_audit` then `structured_memo`
- Primary artifact: `final_output.md` (from the memo task)

```bash
./scripts/staragent --project demo --conversation onboard-1 preset pack-run repo_onboarding --path . --question "What are the entry points and request flow?"
```

### `codebase_audit` (read-only)

- Runs: `deep_repo_audit`
- Primary artifact: `audit_report.md`

```bash
./scripts/staragent --project demo --conversation audit-1 preset pack-run codebase_audit --path . --question "Call out risks and unknowns."
```

### `bug_investigation` (read-only)

- Runs: `bug_triage`
- Primary artifact: `next_actions.md`

```bash
./scripts/staragent --project demo --conversation bug-1 preset pack-run bug_investigation --path . --issue "Describe the issue and the observed behavior."
```

### `docs_digest` (read-only)

- Runs: `docs_research` then `structured_memo`
- Primary artifact: `final_output.md`

```bash
./scripts/staragent --project demo --conversation docs-1 preset pack-run docs_digest --path docs --question "Summarize how to run StarAgent."
```

### `release_prep` (stateful; approval-gated export)

- Runs: `release_review`
- May pause for approval to write an export file under `sandbox_test/`

```bash
./scripts/staragent --project demo --conversation rel-1 preset pack-run release_prep --path . --output sandbox_test/release_prep.md
./scripts/staragent --project demo --conversation rel-1 task approve <task_id>
```

## Inspect Outputs

After a pack run, use the printed `task_id` values (or `staragent task list`) to inspect artifacts:

```bash
./scripts/staragent task artifacts <task_id>
./scripts/staragent task artifact <task_id> audit_report.md --tail-lines 80
```

