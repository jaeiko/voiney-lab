"""The suite starts from one deterministic Voiney Lab setting surface."""

from __future__ import annotations

import os


def test_external_voiney_lab_settings_are_cleared_before_collection() -> None:
    assert "VOINEY_LAB_WORKSPACE_ENABLED" not in os.environ
