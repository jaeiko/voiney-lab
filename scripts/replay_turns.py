#!/usr/bin/env python3
"""Compatibility wrapper for the package-native replay command.

Install the project first, then prefer ``voiney-replay`` or
``python -m voiney_lab.replay_turns``.
"""

from __future__ import annotations

from voiney_lab.replay_turns import main


if __name__ == "__main__":
    raise SystemExit(main())
