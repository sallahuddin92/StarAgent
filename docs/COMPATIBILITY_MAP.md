# StarAgent Compatibility Map (MacAgent -> StarAgent)

This repo is mid-transition from MacAgent to StarAgent. Backward compatibility is preserved where possible.

## Commands

| Old | New | Supported Now | Notes |
| --- | --- | --- | --- |
| `macagent` | `staragent` | Yes | `macagent` remains as a legacy alias. |
| `macagent-mcp` | `staragent-mcp` | Yes | Same MCP server binary; different name. |
| `./scripts/start_macagent.sh` | `./scripts/start_staragent.sh` | Yes | `start_staragent.sh` wraps the legacy script. |
| `./scripts/stop_macagent.sh` | `./scripts/stop_staragent.sh` | Yes | Wrapper. |
| `./scripts/smoke_test_macagent.sh` | `./scripts/smoke_test_staragent.sh` | Yes | Wrapper. |
| `./scripts/validate_macagent.sh` | `./scripts/validate_staragent.sh` | Yes | Wrapper. |

## Env Vars

| Old | New | Supported Now | Notes |
| --- | --- | --- | --- |
| `MACAGENT_BASE_URL` | `STARAGENT_BASE_URL` | Yes | New preferred, old accepted. |
| `MACAGENT_API_KEY` | `STARAGENT_API_KEY` | Yes | New preferred, old accepted. |
| `MACAGENT_DEFAULT_MODEL` | `STARAGENT_DEFAULT_MODEL` | Yes | New preferred, old accepted. |
| `MACAGENT_DEFAULT_PROJECT` | `STARAGENT_DEFAULT_PROJECT` | Yes | New preferred, old accepted. |
| `MACAGENT_DEFAULT_CONVERSATION_PREFIX` | `STARAGENT_DEFAULT_CONVERSATION_PREFIX` | Yes | New preferred, old accepted. |

## MCP Tools

| Old | New | Supported Now | Notes |
| --- | --- | --- | --- |
| `macagent_status` | `staragent_status` | Yes | `staragent_*` is canonical; `macagent_*` is compatibility. |
| `macagent_ask` | `staragent_ask` | Yes |  |
| `macagent_agent` | `staragent_agent` | Yes |  |
| `macagent_approve` | `staragent_approve` | Yes |  |

## API Identity Strings

| Old | New | Supported Now | Notes |
| --- | --- | --- | --- |
| `/health.service = "macagent-proxy"` | `"staragent-proxy"` | Toggle | Controlled by `STARAGENT_BRAND_API` (default `false`). |
| `/v1/models.data[].owned_by = "macagent"` | `"staragent"` | Toggle | Controlled by `STARAGENT_BRAND_API` (default `false`). |

