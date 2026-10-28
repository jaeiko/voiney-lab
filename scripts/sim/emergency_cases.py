"""Synthetic current incidents and negative controls, never safety instructions."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.sim.corpus import Case, Protocol, ROOT

PHRASES = ROOT / 'data/fixtures/evaluation/sim/emergencies.json'


def generate_emergency_cases(protocol: Protocol, path: Path = PHRASES) -> tuple[Case, ...]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 1 or not isinstance(data.get('categories'), dict):
        raise ValueError('invalid emergency phrase schema')
    result = []
    for category, texts in data['categories'].items():
        if not isinstance(category, str) or not isinstance(texts, list) or not texts:
            raise ValueError('invalid emergency category')
        for n, text in enumerate(texts):
            if not isinstance(text, str) or not text.strip():
                raise ValueError('invalid emergency phrase')
            family = 'negative_control' if category == 'negative_control' else f'emergency_{category}'
            result.append(Case(f'{protocol.code}.{family}.{n}', family, text,
                               allowed=('anomaly', 'confirmation') if family == 'negative_control' else ()))
    return tuple(result)
