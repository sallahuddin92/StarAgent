# StarAgent Evaluation Pack (Compact)

This repo includes a compact, real end-to-end evaluation runner that re-checks the 4 active surfaces:

1. Direct API
2. Open WebUI-style request patterns (helper/meta prompts)
3. StarAgent CLI
4. Claude MCP

The runner is conservative and only writes into `sandbox_test/`.

## Run

1. Start StarAgent:
```bash
./scripts/start_staragent.sh
```

2. Run evaluation:
```bash
./scripts/eval_staragent.sh
```

## Notes

- Open WebUI is verified by simulating typical helper/meta prompt payloads that previously hijacked routing.
- Claude MCP verification requires `claude` CLI and an MCP server configured (see `docs/CLAUDE_CODE_MCP_SETUP.md`).
- API branding toggle can be enabled via `STARAGENT_BRAND_API=true` (see `.env.example`).

