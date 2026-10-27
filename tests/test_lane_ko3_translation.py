"""Korean lab vocabulary and explicit unit equivalences; no external calls."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from tests.test_lane_safe_questions import workflow_checkpoint

import pytest

from voiney_lab import protocol_translation as pt
from voiney_lab.curated_protocol import CuratedProtocolSession, reader_translation_issue
from voiney_lab.runtime_routing import route_curated_runtime_turn
from tests.test_lane_ko2_screen_korean import soil_fixture
from tests.test_revision_translations import rich_fixture, FakeTranslator


@pytest.mark.parametrize(('source', 'korean'), [
    ('Wait 15min.', '15분 동안 기다려요.'),
    ('Wait 2 h.', '2시간 동안 기다려요.'),
    ('Wait 30 s.', '30초 동안 기다려요.'),
    ('Store for >6 months.', '>6개월 동안 보관해요.'),
    ('Store for 3 days.', '3일 동안 보관해요.'),
    ('Store for 2 weeks.', '2주 동안 보관해요.'),
    ('Store overnight at 4°C.', '4°C에서 하룻밤 동안 보관해요.'),
    ('Use 16 samples.', '시료 16개를 사용해요.'),
    ('Mix 2 parts water and 1 part solvent.', '물 2부와 용매 1부를 섞어요.'),
    ('Use 2 volumes of water.', '물 2배 부피를 사용해요.'),
])
def test_explicit_unit_equivalence(source: str, korean: str) -> None:
    assert pt.statement_issue(source, korean) is None
    assert reader_translation_issue(source, korean) is None


@pytest.mark.parametrize(('source', 'korean'), [
    ('Wait 15min.', '900초 동안 기다려요.'),
    ('Wait 2 h.', '2분 동안 기다려요.'),
    ('Store for 6 months.', '6일 동안 보관해요.'),
    ('Store overnight.', '보관해요.'),
    ('Store at 4°C.', '4°C에서 하룻밤 보관해요.'),
    ('Use 1.0 mL.', '1 mL를 사용해요.'),
    ('Use 0.50 mL.', '0.5 mL를 사용해요.'),
    ('Use 50,000 cells.', '세포 50000개를 사용해요.'),
    ('Use 15 min.', '15초 동안 기다려요.'),
])
def test_unlisted_equivalence_and_changed_digits_fail(source: str, korean: str) -> None:
    assert pt.statement_issue(source, korean) == 'quantities_changed'


def test_v5_prompts_distinguish_names_common_words_and_purpose() -> None:
    assert pt.STYLE_VERSION == 5
    for text in ('밴드', '젤', '바이알', '튜브', '비즈', '상층액', '피펫', '이 프로토콜은', '개월'):
        assert text in pt.STYLE_GUIDE
    assert 'same unit spelling' not in pt.STYLE_GUIDE
    assert "'2 parts' stays '2 parts'" not in pt.STYLE_GUIDE
    assert '파이펫' in pt.STYLE_GUIDE
    for prompt in (pt.REVISION_TRANSLATION_PROMPT,):
        assert pt.STYLE_GUIDE in prompt


def test_glossary_common_nouns_are_not_required_as_english_names() -> None:
    unit = pt.TranslationUnit('step-1/current_step', 'step',
                              'Move the Band into the GC vial and Porapak tube.',
                              ('Band', 'GC vial', 'Porapak tube'))
    glossary = (pt.GlossaryEntry('Band', 'Band', True),)
    assert pt.check_translation(unit, '밴드를 GC 바이알과 Porapak 튜브로 옮겨요.', glossary) == 'passed'


def test_purpose_answer_uses_two_short_sentences_without_changing_checkpoints() -> None:
    from voiney_lab.curated_protocol import PURPOSE_FACT_KEY, ProtocolKnowledgeView
    fixture = soil_fixture()
    source = ProtocolKnowledgeView.from_fixture(fixture).purpose.text
    # The actual source and its full reading are retained. Speech can stop at a sentence boundary.
    korean = '이 프로토콜은 토양의 pH를 측정하는 방법이에요. 유리 전극으로 측정해요. 원문에서 측정 방법을 확인해요.'
    fixture = replace(fixture, localizations={PURPOSE_FACT_KEY: korean})
    session = CuratedProtocolSession(fixture)
    session.activate_configured()
    before = workflow_checkpoint(session)
    plan = route_curated_runtime_turn(session, '실험 목적이 뭐야', turn_id=1, language='ko',
                                      configuration_id=1, generation=1).plan
    assert plan.speech_text.startswith('이 프로토콜은')
    assert plan.speech_text.count('요.') in (2, 3)
    assert korean in plan.display_text
    assert source in plan.display_text
    assert workflow_checkpoint(session) == before


def test_generated_spellings_follow_the_protocol_and_reviewed_text_is_untouched() -> None:
    from tests.test_revision_translations import good_korean
    fixture = rich_fixture()
    reviewed_key = f'{fixture.steps[0].step_id}/current_step'
    fixture = replace(fixture, localizations={reviewed_key: '파이펫으로 넣어요.'})
    async def translate(batch, context):
        return pt.BatchResult({unit.fact_key: good_korean(unit.source_text) + ' 피펫과 파이펫을 써요.'
                               for unit in batch})
    report = asyncio.run(pt.generate_revision_translations(fixture, translate, model='fake'))
    assert reviewed_key not in {row.fact_key for row in report.records}
    assert fixture.localizations[reviewed_key] == '파이펫으로 넣어요.'
    assert all('피펫' not in row.translated_text for row in report.records)
    assert all('파이펫과 파이펫' in row.translated_text for row in report.records)


@pytest.mark.parametrize(('source', 'korean'), [
    ('Store at -20°C.', '20°C에서 보관해요.'),
    ('Use >5 µL.', '<5 µL를 사용해요.'),
    ('Use ~50 µL.', '50 µL를 사용해요.'),
])
def test_quantity_signs_are_not_lost_or_reversed(source: str, korean: str) -> None:
    assert pt.statement_issue(source, korean) == 'quantities_changed'


def test_stored_reviewed_spelling_guides_new_machine_rows() -> None:
    # A stored reviewed row is as authoritative for wording as a fixture sidecar.
    fixture = rich_fixture()
    unit = pt.translation_units(fixture)[0]
    from voiney_lab.workspace_store import FactTranslationRecord
    row = FactTranslationRecord(pt.translation_revision_key(fixture), unit.fact_key, 'ko',
                                unit.source_sha256, '파이펫으로 넣어요.', 'reviewed', 'passed',
                                'person', 'person', '2026-10-27T00:00:00+00:00')
    async def translate(batch, context):
        return pt.BatchResult({item.fact_key: '피펫과 파이펫을 써요.' for item in batch})
    report = asyncio.run(pt.generate_revision_translations(fixture, translate, model='fake', stored=[row]))
    assert unit.fact_key not in {item.fact_key for item in report.records}
    assert all('파이펫과 파이펫' in item.translated_text for item in report.records)


def test_common_word_spelling_does_not_rewrite_a_longer_name() -> None:
    assert pt.lab_korean('겔라틴 비드플렉스 파이펫팅 젤을 써요.') == '겔라틴 비드플렉스 피펫팅 젤을 써요.'


def test_reviewed_longer_name_does_not_choose_a_common_word_spelling() -> None:
    assert pt.lab_korean('비즈와 피펫을 써요.', {'fact': '비드플렉스와 파이펫맥스'}) == '비즈와 피펫을 써요.'


def test_generation_glossary_translates_common_nouns_inside_names() -> None:
    fixture = rich_fixture()
    entries = tuple(pt.GlossaryEntry(source, source, True) for source in (
        'GC vial', 'Porapak tubes', 'gel band', 'Band100', 'Gel-100'))
    async def glossary(context):
        return pt.GlossaryResult(entries=entries)
    contexts = []
    async def translate(batch, context):
        contexts.append(context)
        return pt.BatchResult({})
    report = asyncio.run(pt.generate_revision_translations(
        fixture, translate, model='fake', make_glossary=glossary))
    saved = {row.source: row for row in pt.glossary_entries(report.glossary_record)}
    assert saved['GC vial'].korean == 'GC 바이알'
    assert saved['Porapak tubes'].korean == 'Porapak 튜브'
    assert saved['gel band'].korean == '젤 밴드'
    assert saved['gel band'].keep_english is False
    assert saved['Band100'].korean == 'Band100'
    assert saved['Gel-100'].korean == 'Gel-100'
    assert 'GC 바이알' in str(contexts[0])
