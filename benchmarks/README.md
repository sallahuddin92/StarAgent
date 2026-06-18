# StarAgent Research Benchmark Suite

This directory contains benchmark cases for evaluating deep research quality.

## Structure

```
benchmarks/
  <case_name>/
    question.md       # Research question to ask
    sources/           # Local source files (.md) used as input
    expected.json     # Expected claims, forbidden claims, required sections
    gold_report.md    # Reference report (informational, not scored)
```

## Adding a New Case

1. Create a new directory under `benchmarks/`
2. Add `question.md` with the research question
3. Add source `.md` files to `sources/`
4. Create `expected.json` with the schema below
5. Optionally add `gold_report.md` for reference

## expected.json Schema

```json
{
  "required_claims": ["claim text that must appear in report"],
  "forbidden_claims": ["claim text that must NOT appear"],
  "required_citations": true,
  "min_sources": 2,
  "min_evidence": 2,
  "required_sections": ["Summary", "Key Findings", "Evidence Table", "Limitations", "Citations", "Source List"]
}
```

## Running

```bash
./scripts/staragent benchmark list
./scripts/staragent benchmark run <case_name>
./scripts/staragent benchmark score <run_id>
./scripts/staragent benchmark history
./scripts/staragent benchmark compare <run_id_a> <run_id_b>
```
