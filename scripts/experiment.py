#!/usr/bin/env python3
"""Python implementation behind start-claude.sh --experiments; not another agent launcher."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.cli import main

try:
    main()
except (ValueError, OSError, RuntimeError, KeyError) as error:
    print('Workflow: ' + str(error)[:2000], file=sys.stderr)
    sys.exit(2)
