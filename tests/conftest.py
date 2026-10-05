"""Shared pytest configuration.

Some tests validate byte-exact identity guarantees (SHA-256, byte size, page
count) against the real "Candidate A" in-gel digestion source PDF. That PDF
is externally licensed and is intentionally not committed to this repository
(see scripts/run_dev.sh's header). On the maintainer's own machine it
lives at a fixed local path; in any other environment - including CI - it is
absent by design, not by mistake.

Rather than faking that file (which would defeat the exact point of those
tests) or silently ignoring their failures (which would also hide a genuine
regression in the same modules), skip only the specific test modules that
require it, with an explicit, honest reason, whenever it is not present.
"""

import ipaddress
import os
import socket
from pathlib import Path

VOINEY_LAB_CANDIDATE_A_SOURCE_PDF = (Path(__file__).resolve().parents[1] / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf")

# --- No test reaches a provider (lane M1, decision 6) ------------------------
# Real keys for xAI, OpenAI, Anthropic, Gemini and the OCR services may sit in
# the shell or in the repository .env. The server and the worker do not read
# the .env under pytest (PYTEST_VERSION is set), the keys are taken out of
# this process's environment before any test module is imported, and a
# connection to anything but this machine fails at once. A test that needs a
# key sets a fake one itself; provider calls stay fake-backed.
PROVIDER_SECRET_NAMES = (
    "XAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
    "GOOGLE_API_KEY", "VOINEY_LAB_CLOVA_OCR_SECRET",
    "VOINEY_LAB_CLOVA_OCR_INVOKE_URL", "VOINEY_LAB_GOOGLE_VISION_API_KEY",
    "VOINEY_LAB_MOSS_PROJECT_KEY",
    # Vertex AI (google-genai's own names): no test may switch to it or use ADC.
    "GOOGLE_GENAI_USE_ENTERPRISE", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION", "GOOGLE_APPLICATION_CREDENTIALS",
)
for _name in PROVIDER_SECRET_NAMES:
    os.environ.pop(_name, None)


class OutsideNetworkBlocked(ConnectionRefusedError):
    """A test tried to connect somewhere other than this machine."""


def _is_local(address) -> bool:
    if not isinstance(address, tuple) or not address:
        return True  # AF_UNIX paths and the like
    host = str(address[0])
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _guarded_connect(self, address):
    if not _is_local(address):
        raise OutsideNetworkBlocked(f"tests may not connect outside this machine: {address[0]}")
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    if not _is_local(address):
        raise OutsideNetworkBlocked(f"tests may not connect outside this machine: {address[0]}")
    return _real_connect_ex(self, address)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex

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
    "test_semantic_intent_fallback.py",
    "test_stability_and_semantic_hardening.py",
    "test_transcript_admission.py",
}


def pytest_sessionstart(session):
    """Refuse to run while an old setting name is set (decision of 2026-10-04).

    The server loads the repository .env when it is imported, so the names in
    that file count as well as the process environment. Names only are shown.
    """
    import pytest

    from voiney_lab.setting_names import (
        OldSettingNamesError,
        refuse_old_setting_names,
    )

    try:
        refuse_old_setting_names(
            dotenv_path=Path(__file__).resolve().parents[1] / ".env"
        )
    except OldSettingNamesError as exc:
        pytest.exit(str(exc), returncode=pytest.ExitCode.USAGE_ERROR)


def pytest_collection_modifyitems(config, items):
    if VOINEY_LAB_CANDIDATE_A_SOURCE_PDF.is_file():
        return
    import pytest

    skip = pytest.mark.skip(
        reason=(
            f"requires the externally licensed Candidate A source PDF at "
            f"{VOINEY_LAB_CANDIDATE_A_SOURCE_PDF}, which is not committed to this "
            f"repository (see scripts/run_dev.sh)"
        )
    )
    for item in items:
        if os.path.basename(str(item.fspath)) in MODULES_REQUIRING_CANDIDATE_A_SOURCE_PDF:
            item.add_marker(skip)
