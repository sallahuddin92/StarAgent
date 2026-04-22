from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class StdioMessage:
    payload: Dict[str, Any]


class StdioTransport:
    """
    MCP stdio transport using LSP-style framing:
      Content-Length: <n>\\r\\n
      \\r\\n
      <json bytes>

    Many MCP clients use this framing to allow JSON with newlines.
    For robustness, we also accept newline-delimited JSON if no headers are present.
    """

    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer
        self._mode: Optional[str] = None  # "lsp" or "ndjson"
        self._trace_path = os.getenv("STARAGENT_MCP_TRACE_PATH") or os.getenv("MACAGENT_MCP_TRACE_PATH")
        self._trace_fp = None
        if self._trace_path:
            try:
                # Write raw bytes and JSON payloads for debugging MCP handshake issues.
                # Never write anything to stdout besides framed JSON-RPC responses.
                self._trace_fp = open(self._trace_path, "ab", buffering=0)
            except Exception:
                self._trace_fp = None

    def _trace(self, chunk: bytes) -> None:
        if not self._trace_fp:
            return
        try:
            self._trace_fp.write(chunk)
        except Exception:
            pass

    def read(self) -> Optional[StdioMessage]:
        """
        Read the next message from stdin.

        Important: a malformed frame or a stray blank line must NOT terminate the server.
        We keep reading until we parse a valid message or hit EOF.
        """
        while True:
            line = self.stdin.readline()
            if not line:
                return None

            # Skip stray blank lines (some clients may emit keepalive newlines).
            if not line.strip():
                self._trace(b"<< (blank line)\n")
                continue

            # Newline-delimited JSON fallback
            if line.lstrip().startswith(b"{"):
                if self._mode is None:
                    self._mode = "ndjson"
                self._trace(b"<< " + line.rstrip(b"\r\n") + b"\n")
                try:
                    return StdioMessage(payload=json.loads(line.decode("utf-8")))
                except Exception:
                    continue

            # Header-based framing (LSP style)
            if self._mode is None:
                self._mode = "lsp"
            headers: Dict[str, str] = {}
            raw_headers = [line]
            while line and line.strip():
                try:
                    k, v = line.decode("utf-8").split(":", 1)
                    headers[k.strip().lower()] = v.strip()
                except Exception:
                    pass
                line = self.stdin.readline()
                if not line:
                    return None
                raw_headers.append(line)

            if "content-length" not in headers:
                # Malformed frame; keep scanning rather than exiting.
                self._trace(b"<< (malformed headers)\n" + b"".join(raw_headers))
                continue

            try:
                n = int(headers["content-length"])
            except Exception:
                self._trace(b"<< (invalid content-length)\n" + b"".join(raw_headers))
                continue

            body = self.stdin.read(n)
            if not body or len(body) < n:
                return None
            self._trace(b"<< " + b"".join(raw_headers) + body + b"\n")
            try:
                return StdioMessage(payload=json.loads(body.decode("utf-8")))
            except Exception:
                continue

    def write(self, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        # Claude Code's MCP client currently uses newline-delimited JSON-RPC
        # (no LSP Content-Length framing). Other MCP clients use LSP framing.
        # Auto-detect from the first inbound message and respond in-kind.
        if self._mode == "ndjson":
            self._trace(b">> " + data + b"\n")
            self.stdout.write(data + b"\n")
            self.stdout.flush()
            return

        header = f"Content-Length: {len(data)}\r\n\r\n".encode("utf-8")
        self._trace(b">> " + header + data + b"\n")
        self.stdout.write(header)
        self.stdout.write(data)
        self.stdout.flush()
