"""Provider failures before commitment leave the real workflow checkpoint equal."""
from __future__ import annotations

import asyncio

import pytest

from scripts.sim.corpus import Case, load_corpus
from scripts.sim.runner import run_case
from voiney_lab.voice_providers import SpeechProviderError


@pytest.mark.parametrize('field', ['biology', 'chemistry', 'materials'])
@pytest.mark.parametrize('boundary', ['stt', 'first_tts'])
def test_failed_turn_preserves_every_workflow_group(field: str, boundary: str) -> None:
    p = next(p for p in load_corpus() if p.field == field)
    def fail(*args, **kwargs):
        raise SpeechProviderError('contract failure', status=503, code='unreachable')
    kwargs = {'transcriber': fail} if boundary == 'stt' else {'synthesizer': fail}
    result = asyncio.run(run_case(p, Case('failed', 'complete', '현재 단계 완료했어'), **kwargs))
    assert result['changed_fields'] == []
    assert result['step_after'] == result['step_before']
    assert result['drops'] if 'drops' in result else result['drop_reasons'] or result['error_codes']
