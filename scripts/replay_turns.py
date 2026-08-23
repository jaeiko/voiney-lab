#!/usr/bin/env python3
"""Compatibility wrapper for the package-native replay command.

Install the project first, then prefer ``voice-workflow-replay`` or
``python -m voice_workflow_agent.replay_turns``.
"""

from __future__ import annotations

from voice_workflow_agent.replay_turns import main


if __name__ == "__main__":
    raise SystemExit(main())
