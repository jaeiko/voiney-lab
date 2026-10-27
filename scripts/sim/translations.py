"""Attach source-identified, checked stored readings without calling a model."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from scripts.sim.corpus import Protocol
from voiney_lab.curated_protocol import ProtocolKnowledgeView, PURPOSE_FACT_KEY
from voiney_lab.protocol_translation import statement_issue


def attach_translations(protocols: tuple[Protocol, ...], path: Path) -> tuple[tuple[Protocol, ...], dict]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 1 or not isinstance(data.get('readings'), list):
        raise ValueError('invalid stored reading schema')
    from voiney_lab.protocol_translation import STYLE_VERSION
    style_version = data.get('style_version', 0)
    if type(style_version) is not int or not 0 <= style_version <= STYLE_VERSION:
        raise ValueError('unsupported stored reading style version')
    by_code = {p.code: p for p in protocols}
    attached: dict[str, dict[str, str]] = {}
    for row in data['readings']:
        if not isinstance(row, dict) or set(row) != {'protocol', 'source_sha256', 'key', 'source', 'korean'}:
            raise ValueError('invalid stored reading')
        p = by_code.get(row['protocol'])
        if p is None:
            continue  # a selected catalog/corpus may be a subset of the readings
        if row['source_sha256'] != p.fixture.source_pdf_sha256:
            raise ValueError('stored reading source identity mismatch')
        facts = {f'{step.step_id}/{fact.fact_id}': fact.text
                 for i, step in enumerate(p.fixture.steps) for fact in p.fixture.facts_for_step(i)}
        facts[PURPOSE_FACT_KEY] = ProtocolKnowledgeView.from_fixture(p.fixture).purpose.text
        key, korean = row['key'], row['korean']
        if (key not in facts or row['source'] != facts[key] or not isinstance(korean, str)
                or statement_issue(row['source'], korean)):
            raise ValueError('stored reading failed source/translation checks')
        readings = attached.setdefault(p.code, {})
        if key in readings:
            raise ValueError('duplicate stored reading')
        readings[key] = korean
    if not attached:
        raise ValueError('no matching stored readings')
    result = tuple(replace(p, fixture=replace(p.fixture,
                   machine_localizations={**(p.fixture.machine_localizations or {}), **attached.get(p.code, {})},
                   translation_versions={**(p.fixture.translation_versions or {}),
                                         **dict.fromkeys(attached.get(p.code, {}), style_version)})) for p in protocols)
    step_count = sum(len(p.fixture.steps) for p in protocols)
    step_readings = sum(key.endswith('/current_step') for readings in attached.values() for key in readings)
    return result, {'attached': sum(map(len, attached.values())), 'protocols': len(attached),
                    'step_readings':step_readings, 'missing_step_readings':step_count - step_readings,
                    'protocols_without_readings': len(protocols) - len(attached)}
