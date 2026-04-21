from __future__ import annotations

import json
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

    def read(self) -> Optional[StdioMessage]:
        # Attempt header-based framing first.
        headers = {}
        line = self.stdin.readline()
        if not line:
            return None

        # Newline-delimited JSON fallback
        if line.lstrip().startswith(b"{"):
            try:
                return StdioMessage(payload=json.loads(line.decode("utf-8")))
            except Exception:
                return None

        # Parse headers (first line already read)
        while line and line.strip():
            try:
                k, v = line.decode("utf-8").split(":", 1)
                headers[k.strip().lower()] = v.strip()
            except Exception:
                pass
            line = self.stdin.readline()

        if "content-length" not in headers:
            return None

        n = int(headers["content-length"])
        body = self.stdin.read(n)
        if not body:
            return None
        return StdioMessage(payload=json.loads(body.decode("utf-8")))

    def write(self, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("utf-8")
        self.stdout.write(header)
        self.stdout.write(data)
        self.stdout.flush()

