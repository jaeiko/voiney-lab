"""Shared pytest configuration.

Some tests validate byte-exact identity guarantees (SHA-256, byte size, page
count) against the real "Candidate A" in-gel digestion source PDF. That PDF
is externally licensed and is intentionally not committed to this repository
(see scripts/run_candidate_a.sh's header). On the maintainer's own machine it
lives at a fixed local path; in any other environment - including CI - it is
absent by design, not by mistake.

Rather than faking that file (which would defeat the exact point of those
tests) or silently ignoring their failures (which would also hide a genuine
regression in the same modules), skip only the specific test modules that
require it, with an explicit, honest reason, whenever it is not present.
"""

import os
from pathlib import Path

CANDIDATE_A_SOURCE_PDF = (Path(__file__).resolve().parents[1] / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf")

MODULES_REQUIRING_CANDIDATE_A_SOURCE_PDF = {
    "test_candidate_a_acceptance_phase2.py",
    "test_candidate_a_final_hardening.py",
    "test_candidate_a_live_voice_generalization.py",
    "test_candidate_a_research_hardening.py",
    "test_candidate_a_websocket_integration.py",
    "test_curated_protocol_cascade.py",
    "test_experiment_reports.py",
    "test_phase3_acceptance.py",
    "test_protocol_catalog.py",
    "test_runtime_intent_routing.py",
    "test_safety_pack.py",
    "test_stability_and_semantic_hardening.py",
    "test_transcript_admission.py",
}


def pytest_collection_modifyitems(config, items):
    if CANDIDATE_A_SOURCE_PDF.is_file():
        return
    import pytest

    skip = pytest.mark.skip(
        reason=(
            f"requires the externally licensed Candidate A source PDF at "
            f"{CANDIDATE_A_SOURCE_PDF}, which is not committed to this "
            f"repository (see scripts/run_candidate_a.sh)"
        )
    )
    for item in items:
        if os.path.basename(str(item.fspath)) in MODULES_REQUIRING_CANDIDATE_A_SOURCE_PDF:
            item.add_marker(skip)
