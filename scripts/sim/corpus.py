"""Immutable cases with expectations independent of the production classifier."""
from __future__ import annotations

import hashlib
from functools import cache
import json
import re
from dataclasses import dataclass
from pathlib import Path

from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import CuratedProtocolFixture, CuratedProtocolSession
from voiney_lab.experiment_protocol_analysis import ProtocolAnalysisDraft, verify_step_timers, validate_protocol_analysis_evidence
from voiney_lab.experiment_protocol_store import deserialize_analysis

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / 'data/fixtures/evaluation/sim/corpus.json'


@dataclass(frozen=True)
class Protocol:
    code: str
    field: str
    fixture: CuratedProtocolFixture


@dataclass(frozen=True)
class Case:
    code: str
    family: str
    text: str
    index: int = 0
    setup: str = 'active'
    actions: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    allowed: tuple[str, ...] = ()
    target: int | None = None
    must_change: bool = False
    terms: tuple[str, ...] = ()
    seed_change: tuple[str,str,str] | None = None


@cache
def load_corpus(path: Path = CORPUS) -> tuple[Protocol, ...]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or data.get('schema') != 1 or not isinstance(data.get('protocols'),list):
        raise ValueError('unsupported corpus schema')
    result = []
    for item in data['protocols']:
        if not isinstance(item,dict) or not re.fullmatch(r'[a-z0-9_]+',str(item.get('code',''))):
            raise ValueError('invalid public corpus code')
        license=str(item.get('license','')).upper()
        if not any(t in license for t in ('CC BY','CC0','PUBLIC DOMAIN')) or any(t in license for t in ('-NC','-ND','-SA')):
            raise ValueError('public reusable licence required')
        payload = item['analysis']
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        p, ready, policy, version, _coverage = deserialize_analysis(encoded)
        # Deserialization checks the domain and every source-evidence quote.
        if p.metadata.pdf.sha256 != item['source_sha256'] or policy != domain.P1_CAPABILITY_POLICY.profile_id:
            raise ValueError('source identity or capability policy mismatch')
        p, evidence_count = validate_protocol_analysis_evidence(p, p.metadata.pdf)
        from voiney_lab.protocol_catalog import protocol_with_display_step_labels
        p = protocol_with_display_step_labels(p)
        timers = verify_step_timers(p, p.metadata.pdf)
        draft = ProtocolAnalysisDraft(p.metadata.pdf, p, ready, domain.P1_CAPABILITY_POLICY, version, evidence_count)
        fixture = CuratedProtocolFixture(
            draft, 'evaluation_only_source_excerpt', tuple(s.source_label for section in p.sections for s in section.steps),
            hashlib.sha256(encoded.encode()).hexdigest(), revision_id='sim-source-v1', development_only=True,
            source_pdf_sha256=item['source_sha256'], timer_manifest=timers.manifest(),
            timer_choices=timers.choices() or None, timer_table=timers,
        )
        if len(fixture.steps) < 3:
            raise ValueError('evaluation protocol needs at least three source steps')
        result.append(Protocol(item['code'], item['field'], fixture))
    if len({p.code for p in result}) != len(result):
        raise ValueError('duplicate protocol codes')
    return tuple(result)


# These are requested behaviour, not outputs sampled from the implementation.
TEMPLATES = (
    ('current', ('현재 단계 알려줘', '지금 뭐 해야 해?', '지금 단계 읽어 줘'), ('current','full_detail'), (), (), None, False),
    ('detail', ('이 단계 자세히 알려줘', '지금 할 일 구체적으로 설명해 줘'), (), (), (), None, False),
    ('repeat', ('다시 들려줘', '방금 안내 다시 말해 줘', '현재 단계 다시 읽어 줘'), (), (), (), None, False),
    ('preview', ('다음 단계 미리 알려줘', '다음 단계에서 뭘 해?', '다음 단계 보여줘'), (), (), (), None, False),
    ('complete', ('다 했어', '현재 단계 완료했어', '다음', '다음 단계로 넘어가'), ('next',), (), ('step', 'revision', 'completed', 'notebook', 'workflow', 'timer', 'confirmation'), 1, True),
    ('previous', ('이전 단계로', '앞 단계로 돌아가', '한 단계 뒤로'), (), (), ('confirmation',), None, False),
    ('jump', ('3단계로 가 줘', '3단계로 이동', '3단계로 돌아가 줘'), (), (), ('confirmation',), None, False),
    ('timer_start', ('타이머 시작', '현재 단계 타이머 켜 줘', '타이머 맞춰 줘'), ('timer', 'start_timer'), (), ('timer', 'revision', 'confirmation'), None, False),
    ('timer_pause', ('타이머 일시정지', '타이머 잠깐 멈춰', '타이머 정지해 줘'), ('timer_pause',), (), ('timer', 'revision'), None, True),
    ('timer_resume', ('타이머 재개', '타이머 다시 시작', '타이머 계속 돌려 줘'), ('timer_resume',), (), ('timer', 'revision'), None, True),
    ('timer_cancel', ('타이머 취소', '타이머 꺼 줘', '타이머 없애 줘'), ('timer_cancel',), (), ('timer', 'revision'), None, True),
    ('timer_restart', ('타이머 처음부터', '타이머 처음부터 다시', '타이머 리셋해 줘'), ('timer_restart',), (), ('timer', 'revision'), None, True),
    ('timer_remaining', ('타이머 얼마나 남았어?', '남은 시간 알려줘', '타이머 남은 시간'), (), (), (), None, False),
    ('source', ('원문대로 할게', '바꾼 값 원문대로 되돌려 줘'), (), (), ('values', 'confirmation', 'revision'), None, False),
    ('problem_report', ('문제가 생겼어', '시료가 이상해졌어', '침전이 생겼어'), (), (), ('confirmation', 'anomaly', 'revision'), None, False),
    ('problem_question', ('침전이 생기면 어떻게 해?', '문제가 생기면 다음으로 가도 돼?', '시료가 이상하면 어떻게 하지?'), (), (), (), None, False),
    ('ambiguous', ('네', '음', '그거', '알겠어'), (), (), (), None, False),
    ('mixed', ('이 단계 설명하고 다음으로 넘어가', '왜 하는지 알려주고 다음 단계로 가 줘'), (), ('next_step_confirmation_required',), ('confirmation',), None, False),
    ('safety', ('지금 단계 주의사항 알려줘', '원문에 있는 안전 주의 읽어줘', '지금 단계에서 조심할 게 뭐야?'), (), (), (), None, False),
    ('purpose', ('이 실험 목적이 뭐야?', '이 단계 왜 하는 거야?', '이 과정 목적 알려줘'), (), (), (), None, False),
    ('pause', ('실험 일시정지', '잠깐 멈춰 줘', '실험 잠깐 멈춰'), ('pause',), (), ('pause', 'workflow', 'revision', 'confirmation'), None, True),
    ('resume', ('실험 재개', '실험 다시 시작', '이어서 할게'), ('resume',), (), ('pause', 'workflow', 'revision', 'confirmation'), None, True),
    ('emergency', ('불이 났어', '눈에 시약이 튀었어', '숨을 못 쉬겠어'), (), (), (), None, False),
    ('chat', ('오늘 날씨 어때?', '안녕', '점심 뭐 먹지?'), (), (), (), None, False),
    ('noise', ('', ''), (), (), (), None, False),
)


def generate_cases(protocol: Protocol) -> tuple[Case, ...]:
    cases = []
    session = CuratedProtocolSession(protocol.fixture)
    timed = next((i for i in range(len(protocol.fixture.steps)) if session.timer_seconds_for_step(i)), None)
    # A missing source timer is tested too; never invent a duration for it.
    for family, texts, actions, intents, allowed, target, must in TEMPLATES:
        for n, text in enumerate(texts):
            case_actions,case_intents,case_allowed,case_target,case_must=actions,intents,allowed,target,must
            if family=='complete' and text=='다 했어':
                case_actions=('clarify_completion',)
                case_intents=('next_step_confirmation_required',)
                case_allowed=('confirmation',)
                case_target=None
                case_must=False
            if family=='source':
                # This case starts with no run overrides: a source-specific no-op is correct.
                case_actions=('decline_completion',)
                case_intents=('run_value_change_refused',)
                case_allowed=()
            if family=='jump' and '돌아가' in text:
                # At step 1, step 3 is ahead: this cannot be a return.
                case_actions=('decline_completion',)
                case_intents=('repeat_return_refused',)
                case_allowed=()
            index = 1 if family == 'previous' else 0
            setup = 'paused' if family == 'resume' else 'active'
            if family.startswith('timer_'):
                index = timed if timed is not None else 0
                setup = 'timer_paused' if family == 'timer_resume' else 'timer_running' if family not in {'timer_start'} else 'active'
            cases.append(Case(f'{protocol.code}.{family}.{n}', family, text, index, setup,
                              case_actions if timed is not None or not family.startswith('timer_') else (), case_intents,
                              case_allowed, case_target, case_must if timed is not None or not family.startswith('timer_') else False))
    # Numeric observations and value requests use the source's own scientific strings.
    from scripts.sim.measurements import source_values
    for index, step in enumerate(protocol.fixture.steps):
        values = [(n,u) for n,u in source_values(step.instruction_source_text) if u]
        terms = tuple(dict.fromkeys(re.findall(r'[A-Za-z][A-Za-z0-9-]{2,}', step.instruction_source_text)))[:5]
        if values:
            value, unit = values[0]
            shown = f'{value} {unit}'.strip()
            for family, text, allowed in (
                ('observation', f'관찰 기록해 줘: 측정값 {shown}', ('record', 'confirmation', 'revision')),
                ('value_inside', f'현재 단계 값을 {shown}로 바꿔 줘', ('confirmation',)),
                ('value_outside', f'현재 단계 값을 {float(value.replace('−','-').replace(',','.')) * 1000 + 999:g} {unit}로 바꿔 줘', ('confirmation',)),
                ('term', f'{terms[0] if terms else "시약"} 이 단계에서 얼마나 써?', ()),
            ):
                expected_actions=('clarify_parameter','clarify_completion','decline_completion') if family in {'value_outside','value_inside'} else ()
                expected_intents=('operational_value_clarification_required','run_value_change_confirmation_required','run_value_change_refused') if family in {'value_outside','value_inside'} else ()
                cases.append(Case(f'{protocol.code}.{family}.{index}', family, text, index=index,
                                  actions=expected_actions,intents=expected_intents,allowed=allowed, terms=terms))
    # Source-typed positive requests complement the deliberately vague variants.
    from collections import Counter
    from decimal import Decimal, InvalidOperation
    from voiney_lab.start_check import check_run_change, RunChangeError
    candidates=[]
    for value in session.changeable_values():
        try:
            number=Decimal(value.number)
        except InvalidOperation:
            continue
        if number.is_finite():
            try:
                check_run_change(value,str(number*2 or Decimal(1)),value.unit)
            except RunChangeError:
                continue
            candidates.append(value)
    keys=Counter((v.step_id,v.kind,v.number,v.unit) for v in candidates)
    selected=next((v for v in candidates if keys[(v.step_id,v.kind,v.number,v.unit)]==1),None)
    if selected is not None:
        index=next(i for i,step in enumerate(protocol.fixture.steps) if step.step_id==selected.step_id)
        literal=f'{selected.number} {selected.unit}'
        user_number=str(Decimal(selected.number)*2 or Decimal(1))
        user_literal=f'{user_number} {selected.unit}'
        text=f'이번엔 {selected.step_label}단계 {literal} 대신 {user_literal}로 할게'
        cases.append(Case(f'{protocol.code}.value_change.typed','value_change',text,index=index,
                          actions=('change_value','clarify_completion'),
                          intents=('run_value_changed','run_value_change_confirmation_required'),
                          allowed=('values','revision','confirmation'),must_change=True))
        # Explicit synthetic experimenter-confirmed override; never source data.
        synthetic_number=str(Decimal(selected.number)*2 or Decimal(1))
        check_run_change(selected,synthetic_number,selected.unit)
        cases.append(Case(f'{protocol.code}.source.restore','source',f'{selected.step_label}단계 원문대로',
                          index=index,setup='run_changed',actions=('change_value',),intents=('run_value_reverted',),
                          allowed=('values','revision'),must_change=True,
                          seed_change=(selected.item_id,synthetic_number,selected.unit)))
    previous = json.loads((ROOT/'data/fixtures/evaluation/candidate_a_real_voice_hardening.json').read_text())
    for row in previous['cases']:
        if row['id'] in {'explain-current','explain-current-bare','completion-ambiguous'}:
            cases.append(Case(f"{protocol.code}.repo.{row['id']}", 'repository_eval', row['text'],
                              actions=(row['action'],), allowed=('confirmation',) if row['id']=='completion-ambiguous' else ()))
    return tuple(cases)
