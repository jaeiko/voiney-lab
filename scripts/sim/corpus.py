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
    ('problem_question', ('침전이 생기면 어떻게 해?', '문제가 생기면 다음으로 가도 돼?', '시료가 이상하면 어떻게 하지?'), ('problem_help',), ('grounded_problem_question',), (), None, False),
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


def generate_development_cases(protocol: Protocol) -> tuple[Case, ...]:
    """Known field regressions kept outside the outcome-blind holdout.

    These utterances come from an observed pilot failure, so they belong to
    the development workload rather than the concealed evaluation split.
    Every public protocol receives the same cases; no source-specific rule is
    introduced.
    """
    session = CuratedProtocolSession(protocol.fixture)
    timed = next(
        (
            index
            for index in range(len(protocol.fixture.steps))
            if session.timer_seconds_for_step(index)
        ),
        None,
    )
    timer_index = timed if timed is not None else 0
    timer_setup = 'timer_running' if timed is not None else 'active'
    return (
        Case(
            f'{protocol.code}.voice1_completion_reask.0',
            'completion_reask',
            '다음',
            0,
            'completion_pending',
            ('clarify_completion',),
            ('pending_completion_reasked',),
            ('confirmation',),
        ),
        Case(
            f'{protocol.code}.voice1_timer_remaining.0',
            'timer_remaining',
            '타임 얼마나 남았어? 어',
            timer_index,
            timer_setup,
            ('timer_status',),
        ),
        Case(
            f'{protocol.code}.voice1_timer_remaining.1',
            'timer_remaining',
            '시간 얼마나 남았어 음',
            timer_index,
            timer_setup,
            ('timer_status',),
        ),
        *(
            Case(
                f'{protocol.code}.voice2_timer_remaining.{at}',
                'timer_remaining',
                text,
                timer_index,
                timer_setup,
                ('timer_status',),
            )
            for at, text in enumerate((
                '타임 얼마?', '타임 얼마나?', '타이머 얼마?',
                '어, 그럼 타이머 얼마나 남았지?', '어... 타임, 얼마 남았어?',
                '시간 몇 남았어?',
            ))
        ),
        *(
            Case(
                f'{protocol.code}.voice2_step_question.{at}',
                'step_question',
                text,
                0,
                'active',
                (action,),
            )
            for at, (text, action) in enumerate((
                ('지금 몇 단계?', 'protocol_query'),
                ('어, 지금 단계 몇이야?', 'protocol_query'),
                ('다음 뭐야?', 'next_information'),
                ('그럼 다음에 뭐 해?', 'next_information'),
            ))
        ),
        Case(
            f'{protocol.code}.safe1_purpose.0',
            'safe1_purpose',
            '편. 편.',
            0,
            'purpose_question',
            ('clarify_completion',),
            ('notebook_confirmation_required',),
            ('notebook',),
        ),
        Case(
            f'{protocol.code}.safe1_result.0',
            'safe1_result',
            '1.7 100',
            0,
            'result_question',
            ('clarify_completion',),
            ('notebook_confirmation_required',),
            ('notebook',),
        ),
        Case(
            f'{protocol.code}.safe1_observation.0',
            'safe1_observation',
            '관찰 기록해 줘: 측정값 37 °C',
            0,
            'active',
            ('clarify_completion',),
            ('note_confirmation_required',),
            ('confirmation',),
        ),
    )


#: Lane GEN1 (2026-10-31): a problem supposed, not reported, in each field's
#: words. Both sides are written here once and fixed; rules are tuned on the
#: development wording only and the holdout is scored, not read. The same
#: words go to every protocol of the field; nothing names one document.
SUPPOSED_QUESTIONS: dict[str, dict[str, tuple[str, ...]]] = {
    'biology': {
        'development': ('세포가 안 떨어지면 어떡해?', '만약 배지 색이 노랗게 변하면 어떻게 해?', '여기서 거품이 생기면'),
        'holdout': ('혹시 세포가 뭉치면 어떻게 돼?', '웰에 기포가 생기면 어떡하지', '만약 밴드가 안 보이면'),
    },
    'chemistry': {
        'development': ('용액이 탁해지면 어떻게 해?', '혹시 침전이 생기면', '만약 색이 변하면 어떻게 돼?'),
        'holdout': ('시약이 넘치면 어떡해?', '만약 온도가 너무 높아지면', '혹시 용액에 결정이 생기면 어떻게 하지?'),
    },
    'materials': {
        'development': ('필름에 금이 가면 어떡해?', '만약 기판이 깨지면', '혹시 막이 뿌옇게 되면 어떻게 해?'),
        'holdout': ('코팅이 벗겨지면 어떡하지?', '만약 표면에 얼룩이 생기면 어떻게 돼?', '혹시 결정이 안 생기면'),
    },
    'soil': {
        'development': ('여과가 너무 느리면 어떡해?', '만약 추출액이 탁해지면', '혹시 시료가 덜 마르면 어떻게 해?'),
        'holdout': ('흙이 다 안 풀리면 어떡하지?', '만약 거품이 넘치면 어떻게 돼?', '혹시 침전이 생기면 어떻게 하지?'),
    },
}


#: Lane SAFE2 (2026-10-31): a problem said as having happened and then asked
#: about, in each field's bench words -- a past predicate before the question.
#: Development wording is what the rules are tuned on; the holdout is scored
#: only. Nothing names one document.
HAPPENED_QUESTIONS: dict[str, dict[str, tuple[str, ...]]] = {
    'biology': {
        'development': ('배지 색이 노랗게 변했는데 어떡해?', '웰에 거품이 생겼어 어떻게 해?', '세포가 다 떨어져 나갔는데 이러면 어떡하지?'),
        'holdout': ('젤이 녹았는데 어떻게 하지?', '기포가 생겼어 어떡해?', '밴드가 번졌는데 어떡해?'),
    },
    'chemistry': {
        'development': ('침전이 생겼는데 이러면 어떡해?', '용액이 탁해졌어 어떻게 해?', '용액 색이 갈색으로 변했는데 어떡하지?'),
        'holdout': ('결정이 생겼는데 어떻게 하지?', '용액이 굳었는데 어떡해?', '덩어리가 생겼어 어떡하지?'),
    },
    'materials': {
        'development': ('막이 뿌옇게 됐는데 어떡해?', '표면에 균열이 생겼어 어떻게 해?', '얼룩이 생겼는데 이러면 어떡하지?'),
        'holdout': ('막이 갈색으로 변했는데 어떻게 하지?', '기판에 이물질이 들어갔어 어떡해?', '덩어리가 생겼는데 어떡해?'),
    },
    'soil': {
        'development': ('상층액이 탁해졌는데 어떡해?', '거품이 생겼어 어떻게 해?', '덩어리가 생겼는데 이러면 어떡하지?'),
        'holdout': ('시료가 굳었는데 어떡하지?', '침전이 생겼어 어떻게 해?', '용액에 이물질이 들어갔는데 어떻게 하지?'),
    },
}


def generate_happened_cases(protocol: Protocol, split: str) -> tuple[Case, ...]:
    """The field's problems said as having happened and asked about, at the second step (lane SAFE2).

    The anomaly is recorded (as "침전이 생겼어" is) and the same reply carries
    the grounded answer; a step, a completion or a timer that changes is the
    error, and so is an event left unrecorded or an answer not grounded.
    """

    texts = HAPPENED_QUESTIONS.get(protocol.field, HAPPENED_QUESTIONS['chemistry'])[split]
    index = min(1, len(protocol.fixture.steps) - 1)
    return tuple(
        Case(f'{protocol.code}.happened_{split}.{n}', 'happened_question', text, index,
             'active', ('report_anomaly',), (), ('anomaly', 'confirmation', 'record', 'revision'))
        for n, text in enumerate(texts)
    )


def development_fixture_protocol() -> Protocol:
    """The in-gel development fixture as one more SIM protocol (lane GEN1).

    Development material only: it needs the externally licensed source PDF
    (condition A) and is never part of the public partition.
    """

    from voiney_lab.curated_protocol import load_curated_protocol_fixture

    data = ROOT / 'data/fixtures/development_protocols'
    fixture = load_curated_protocol_fixture(
        data / 'candidate_a_curated_analysis.json',
        data / 'candidate_a_curated_analysis.provenance.json',
        ROOT / 'data/runtime/candidate-a-source/in-gel-digestion.pdf',
    )
    return Protocol('in_gel_development', 'biology', fixture)


def generate_supposed_cases(protocol: Protocol, split: str) -> tuple[Case, ...]:
    """The field's supposed problems at the protocol's second step (lane GEN1).

    Read-only grounded help is expected and nothing may change: a supposition
    that is recorded as an anomaly is the error this family counts.
    """

    texts = SUPPOSED_QUESTIONS.get(protocol.field, SUPPOSED_QUESTIONS['chemistry'])[split]
    index = min(1, len(protocol.fixture.steps) - 1)
    return tuple(
        Case(f'{protocol.code}.supposed_{split}.{n}', 'supposed_question', text, index,
             'active', ('problem_help',), ('grounded_problem_question',), ())
        for n, text in enumerate(texts)
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
    from scripts.sim.measurements import source_values,source_terms
    for index, step in enumerate(protocol.fixture.steps):
        values = [(n,u) for n,u in source_values(step.instruction_source_text) if u]
        terms = source_terms(step.instruction_source_text)
        if values:
            value, unit = values[0]
            shown = f'{value} {unit}'.strip()
            for family, text, allowed in (
                ('observation', f'관찰 기록해 줘: 측정값 {shown}', ('confirmation',)),
                ('value_inside', f'현재 단계 값을 {shown}로 바꿔 줘', ('confirmation',)),
                ('value_outside', f'현재 단계 값을 {float(value.replace('−','-').replace(',','.')) * 1000 + 999:g} {unit}로 바꿔 줘', ('confirmation',)),
            ):
                expected_actions=(
                    ('clarify_completion',)
                    if family == 'observation' else
                    ('clarify_parameter','clarify_completion','decline_completion')
                )
                expected_intents=(
                    ('note_confirmation_required',)
                    if family == 'observation' else
                    ('operational_value_clarification_required','run_value_change_confirmation_required','run_value_change_refused')
                )
                cases.append(Case(f'{protocol.code}.{family}.{index}', family, text, index=index,
                                  actions=expected_actions,intents=expected_intents,allowed=allowed, terms=terms))
        if terms:
            cases.append(Case(f'{protocol.code}.term.{index}','term',f'{terms[0]} 이 단계에서 얼마나 써?',
                              index=index,terms=terms))
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
