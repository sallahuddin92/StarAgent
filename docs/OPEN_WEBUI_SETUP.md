# Open WebUI Setup (Use MacAgent Instead of Ollama Directly)

MacAgent exposes an OpenAI-compatible API. Open WebUI can point to MacAgent as its “OpenAI” backend, and MacAgent will call Ollama locally.

## Preconditions

- Ollama reachable at `http://127.0.0.1:11434`
- MacAgent running (recommended port `8095`)
- Model installed in Ollama: `gemma4:e2b`

## What to Configure in Open WebUI

You want Open WebUI to call MacAgent’s OpenAI-compatible API:

- Base URL: `http://host.docker.internal:8095/v1` (Open WebUI running in Docker on macOS)
- API Key: `local-dev-key` (or your `PROXY_API_KEY`)
- Model: `gemma4:e2b`

If Open WebUI is not running in Docker (native), use:

- Base URL: `http://127.0.0.1:8095/v1`

## Connectivity Tests

From host:
```bash
curl -sS http://127.0.0.1:8095/health
```

If Open WebUI is running in a Docker container on macOS, test reachability from inside the container:
```bash
curl -sS http://host.docker.internal:8095/health
```

Test OpenAI-compatible endpoint directly:
```bash
curl -sS http://127.0.0.1:8095/v1/chat/completions \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer local-dev-key' \\
  -d '{\"model\":\"gemma4:e2b\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly MACAGENT_OK\"}]}' \\
  | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"choices\"][0][\"message\"][\"content\"])'
```

## Recommended Operator Defaults

- Start MacAgent with `HOST=0.0.0.0` so Docker containers can reach it.
- Keep MacAgent on `PORT=8095` to match recovered runtime validation.
- Keep `PROXY_API_KEY` enabled (do not run unauthenticated if Open WebUI is exposed beyond localhost).

