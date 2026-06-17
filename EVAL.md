# StarAgent Eval Tiers

This repository uses tiered evals to separate regressions from capability limits.

## Tier Policy

- `baseline` = **must pass**. Any failure is a regression.
- `medium` = **should pass**. Failures indicate capability regressions that should be fixed before release.
- `stress` = **expected-fail allowed only with diagnostics**. Incomplete outcomes are acceptable only when verifier output clearly reports missing files/commands/checks.

## How To Run

- Baseline:
  ```bash
  ./scripts/staragent eval baseline
  ```
- Medium:
  ```bash
  ./scripts/staragent eval medium
  ```
- Stress:
  ```bash
  ./scripts/staragent eval stress
  ```
- All tiers:
  ```bash
  ./scripts/staragent eval all
  ```

## Interpretation

- Release blocking:
  - `baseline` must be green.
  - `medium` should be green for milestone quality.
- `stress` can report `EXPECTED_FAIL` when diagnostics are explicit and actionable.
- `eval all` provides a tiered summary instead of a single flat pass/fail signal.
