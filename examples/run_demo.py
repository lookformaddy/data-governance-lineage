"""Run the end-to-end governance demo on a fully synthetic data estate."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from data_governance_lineage.demo import run_demo  # noqa: E402


def main() -> int:
    print(run_demo())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
