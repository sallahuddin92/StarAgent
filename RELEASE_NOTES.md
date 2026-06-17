# StarAgent Release Notes v0.5.1 (v0.5.1-groq-stable)

## Milestone Summary

StarAgent v0.5.1 stabilizes the runtime when powered by Groq API providers, introduces a self-healing SQLite database connection wrapper, and adds cross-process doctor/evaluation locking alongside other robustness features.

## Highlights

- **Stable Groq Provider Integration**:
  - Validated and hardened model profile routing (`llama-3.1-8b-instant`) under Groq.
  - Implemented fast paths for known/simple tasks to avoid rate-limiting limits.

- **Self-Healing SQLite Connection**:
  - Built retry mechanism inside database decorators to dispose of stale pools, auto-recreate tables/columns, and recover gracefully from disk deletion conflicts.

- **Fast-Path Verification & Artifact Tracking**:
  - Automated fast paths for simple scripts, FastAPI backends, and full-stack React calculators.
  - Tracked and registered generated files/artifacts in database stage checkpoints correctly.

- **CLI doctor & Locking Robustness**:
  - Implemented cross-process lock (`.runtime/doctor.lock`) using `fcntl` to prevent concurrent baseline runs.
  - Full evaluation logging output captured under `.runtime/doctor_baseline_last.log`.
  - Added clean teardown of isolated, run-specific scratch directories and trace files matching `*{run_id}*` on exit.

---

# StarAgent Release Notes v0.4.0

## Milestone Summary

StarAgent v0.4.0 transforms the agent architecture into an ICM-inspired Agent Operating System. It separates workflow definitions, stage logic, model routing, tools, contexts, verifier gates, and checkpoints into independent modular layers.

## Highlights

- **Workflow Engine & Runtime**:
  - Manages stage progressions and pauses for human approval.
  - Initialized with 9 preloaded workflow configurations under `.staragent/workflows/`.
  
- **Stage-Based Model Routing**:
  - Automatically routes execution stages (`inspect`, `analyze`, `plan`, `execute`, `verify`, `finalize`) to optimized model configurations.
  - Fully configurable and overridable.
  
- **Context Loading & Token Budgeting**:
  - Implements layered context resolution (Workflow -> Stage -> Project -> Task -> Docs).
  - Truncates lower-priority layers when token budgets are exceeded.

- **Checkpoints & Resumption**:
  - Saves durable snapshots of stage variables, traces, reports, and produced files.
  - Allows seamless resumption from any stage.

- **MCP Permission Layer & Tool Profiles**:
  - Restricts available tools and external MCP server access on a per-stage basis, preventing tool leakage.

- **Unified Migration**:
  - Transparently routes all legacy pipeline commands (`repo-audit`, `research`, etc.) onto the new Workflow Engine.

---

# StarAgent Release Notes v0.3.0

## Milestone Summary

StarAgent v0.3.0 focuses on reliability for project-aware implementation with docs grounding and clearer operator ergonomics.

## Highlights

- Model-agnostic profiles:
  - Added profile-driven behavior for local and API-backed models.
  - Preserves compatibility with Gemma/Ollama baseline while enabling stronger API models.

- Blueprint injection:
  - Improved blueprint-driven execution so agents receive explicit required files, semantics, and run commands.
  - Added stronger completion/repair guards for implementation subtasks.

- Docs/RAG grounding:
  - Added project-scoped docs ingestion/search/ask flow with evidence and citations.
  - Enforced docs-grounded verifier behavior and no-evidence refusal path.

- Compact streaming:
  - Added `--stream full|compact|quiet` with backward-compatible `--stream` defaulting to compact.
  - Keeps JSONL traces detailed while improving terminal readability.

- Tiered evals:
  - Split eval suite into `baseline`, `medium`, and `stress`.
  - Added tiered result parsing and expected-fail handling for stress scenarios when diagnostics are present.

## Operational Additions

- Added `./scripts/staragent doctor` release-readiness diagnostics:
  - server health
  - active model profile
  - docs API route presence
  - trace directory writability
  - eval baseline quick pass
