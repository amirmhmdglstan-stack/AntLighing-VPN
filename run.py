#!/usr/bin/env python3
"""Launch AntLighting VPN from a source checkout.

    python3 run.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from antlighting.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
