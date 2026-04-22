from __future__ import annotations

# StarAgent is the product name. This module is a thin wrapper over the
# legacy-compatible CLI implementation in cli.macagent.

from cli.macagent import main


if __name__ == "__main__":
    raise SystemExit(main())

