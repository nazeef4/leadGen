#!/usr/bin/env python3
"""Thin wrapper: seeds the demo dataset from a source checkout.

The real implementation lives in ``leadgen/demo_data.py`` so that it ships
inside the package and ``leadgen demo`` works on an installed copy too.

    python scripts/seed_demo.py --leads 8
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from leadgen.demo_data import main  # noqa: E402

if __name__ == "__main__":
    main()
