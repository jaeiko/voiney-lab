"""Scoring and content-free tables; never export provider transcripts or responses."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

from scripts.sim.corpus import Case
from scripts.stt_compare import Score
from scripts.sim.measurements import number_units

EXPECTED_ACTIONS = {
    'current': ('current',), 'detail': ('full_detail', 'current'), 'repeat': ('repeat', 'current', 'full_detail'),
    'preview': ('next_information', 'preview_step'), 'previous': ('clarify_completion', 'clarify_reference'),
    'jump': ('clarify_completion', 'clarify_reference'), 'timer_start': ('start_timer', 'clarify_parameter'),
    'timer_pause': ('timer_pause',), 'timer_resume': ('timer_resume',), 'timer_cancel': ('timer_cancel',),
    'timer_restart': ('timer_restart',), 'timer_remaining': ('timer_status',),
    'source': ('change_value', 'clarify_parameter'), 'observation': ('record_observation', 'clarify_parameter'),
    'value_inside': ('change_value', 'clarify_parameter', 'clarify_completion'), 'value_outside': ('clarify_parameter',),
    'problem_report': ('report_anomaly', 'clarify_parameter'), 'problem_question': ('problem_help', 'related_question'),
    'ambiguous': ('clarify_reference', 'off_topic', 'transcript_unreliable'),
    'safety': ('question', 'protocol_query', 'current', 'related_question'),
    'purpose': ('protocol_query', 'related_question', 'question'), 'pause': ('pause',), 'resume': ('resume',),
    'chat': ('off_topic', 'agent_meta'), 'term': ('question', 'related_question', 'clarify_parameter'),
}

_ROUTER_USAGE_KEYS = (
    'prompt_tokens', 'completion_tokens', 'total_tokens',
    'cached_prompt_tokens', 'cache_write_tokens', 'reasoning_tokens',
)


def _content_free_router(result: dict) -> dict | None:
    raw = result.get('router')
    if not isinstance(raw, dict):
        return None
    usage = raw.get('usage')
    safe_usage = {
        key: value for key in _ROUTER_USAGE_KEYS
        if isinstance(usage, dict)
        and isinstance((value := usage.get(key)), int)
        and not isinstance(value, bool) and value >= 0
    }
    handled = raw.get('handled_by')
    fallback = raw.get('fallback_reason')
    return {
        'handled_by': handled if isinstance(handled, str) else None,
        'fallback_reason': fallback if isinstance(fallback, str) else None,
        # The model name is already present once in report.router_settings.
        # Rows keep only whether a call was made, never provider content.
        'model_called': isinstance(raw.get('model'), str),
        'usage': safe_usage,
    }


def protected_scientific_names(readings: list[str], source: str) -> tuple[str, ...]:
    """Source-matching proper names retained by checked Korean readings."""
    grammar = {'use', 'add', 'mix', 'wash', 'incubate', 'transfer', 'remove', 'prepare',
               'the', 'a', 'an', 'of', 'for', 'with', 'to', 'and', 'or', 'in', 'on'}
    names = set()
    normalized_source = ' '.join(source.split())
    for reading in readings:
        for match in re.finditer(r'(?<![A-Za-z])(?:[A-Z][A-Za-z0-9-]*\s+){3,}[A-Z][A-Za-z0-9-]*', reading):
            name = match.group().strip()
            if name in normalized_source and not grammar.intersection(name.casefold().split()):
                names.add(name)
    return tuple(sorted(names))


def english_copy(spoken: str, source: str, *, protected_names: tuple[str, ...] = ()) -> bool:
    # Exact runs of >=4 English words detect source sentences, not reagent names.
    runs = re.findall(r'(?:[A-Za-z][A-Za-z0-9-]*[ ,;:.!?]*){4,}', spoken)
    clean = lambda t: ' '.join(re.findall(r'[a-z0-9-]+', t.casefold()))
    if any(len(clean(r).split()) >= 4 and clean(r) in clean(source)
           and not any(clean(r) in clean(name) for name in protected_names if clean(name) in clean(source))
           for r in runs):
        return True
    short = re.findall(r'(?i)(?:add|mix|wash|incubate|centrifuge|transfer|prepare|remove|discard|keep|store|filter|measure|stir)\s+[A-Za-z][A-Za-z0-9 ,;-]*[.!?]',source)
    return any(clean(line) in clean(spoken) for line in short)


def score_case(case: Case, result: dict, *, source: str = '', mode: str = 'offline') -> dict:
    changed = set(result['changed_fields'])
    wrong = bool(changed - set(case.allowed) or result.get('invalid_state'))
    if changed and (not result['terminal'] or result['drop_reasons']):
        wrong = True
    if case.target is not None and 'step' in changed and result['step_after'] != case.target:
        wrong = True
    if case.target is None and result['step_after'] != result['step_before']:
        wrong = True
    actions = case.actions or EXPECTED_ACTIONS.get(case.family, ())
    action_ok = result['action'] in actions if actions else True
    if case.intents:
        action_ok=action_ok and result['intent_kind'] in case.intents
    if case.family == 'emergency' or case.family.startswith('emergency_'):
        action_ok = result['route'] == 'deterministic_emergency'
    if case.family == 'negative_control':
        action_ok = result['route'] != 'deterministic_emergency'
    if case.family == 'noise':
        action_ok = result['stt_calls'] == 0 and not changed
    if case.family == 'mixed':
        action_ok = result['intent_kind'] in case.intents
    understood = (action_ok and not wrong and (not case.must_change or bool(changed))
                  and (case.target is None or result['step_after'] == case.target) and result['terminal'])
    spoken = result['spoken']
    english = english_copy(spoken, source, protected_names=tuple(result.get('protected_names', ())))
    duplicate = bool(re.search(r'(\d+)\s*단계\s*[:：·.-]?\s*\1\s*단계', spoken))
    korean = bool(re.search('[가-힣]', spoken)) if spoken.strip() else None
    # Oracle STT input is not a measured recognizer. Keep it out of live STT tables.
    stt = Score()
    stt_attempted=mode=='live' and result.get('stt_calls',0)>0
    if stt_attempted:
        failed='stt_failed' in result['drop_reasons'] or (not result['transcript'] and bool(result.get('error_codes')))
        stt.add('', case.text, None if failed else result['transcript'], list(case.terms))
    counts = None
    if stt_attempted:
        ref = number_units(case.text)
        hyp = number_units(result['transcript'])
        counts = {'word_errors':stt.word_errors, 'reference_words':stt.reference_words,
                  'char_errors':stt.char_errors, 'reference_chars':stt.reference_chars,
                  'numbers_right':sum((Counter(n for n,u in ref) & Counter(n for n,u in hyp)).values()),
                  'numbers_total':len(ref),
                  'units_right':sum((Counter(u for n,u in ref if u) & Counter(u for n,u in hyp if u)).values()),
                  'units_total':sum(bool(u) for n,u in ref),
                  'pairs_right':sum((Counter(ref) & Counter(hyp)).values()), 'pairs_total':len(ref),
                  'terms_right':stt.terms_right,'terms_total':stt.terms_total}
    stt_summary=stt.summary() if stt_attempted else None
    if stt_summary is not None and counts is not None:
        stt_summary['number_units']=counts['pairs_total']
        stt_summary['number_unit_accuracy']=round(counts['pairs_right']/counts['pairs_total'],4) if counts['pairs_total'] else None
    row = {
        'case': case.code, 'family': case.family, 'understood': bool(understood), 'wrong_state': wrong,
        'emergency_miss':bool((case.family == 'emergency' or case.family.startswith('emergency_')) and result['route'] != 'deterministic_emergency'),
        'false_alarm':bool(case.family == 'negative_control' and result['route'] == 'deterministic_emergency'),
        'error_codes':result.get('error_codes',[]), 'action': result['action'], 'intent_kind': result['intent_kind'], 'route': result['route'],
        'changed_fields': sorted(changed), 'invalid_state': result.get('invalid_state',[]), 'english_copy': english, 'duplicate_label': duplicate,
        'korean': korean, 'drops': result['drop_reasons'], 'interrupted': result.get('interrupted', False),
        'stt_failures':result.get('stt_failures',[]),
        'cut_off': result.get('cut_off',False), 'incomplete': not result['terminal'], 'endpoints': result.get('endpoint_count', 0),
        'timings_ms': result['timings_ms'], 'stt_calls':result.get('stt_calls',0), 'stt_responses':result.get('stt_responses',0),
        'stt': stt_summary, 'stt_counts': counts,
    }
    router = _content_free_router(result)
    if router is not None:
        row['router'] = router
    return row


def aggregate(rows: list[dict]) -> dict:
    def summary(group: list[dict]) -> dict:
        timings: dict[str, list[float]] = defaultdict(list)
        for row in group:
            for key,val in row['timings_ms'].items():
                if isinstance(val, (int, float)) and math.isfinite(val):
                    timings[key].append(val)
        def percentiles(values: list[float]) -> dict:
            values = sorted(values)
            return {p: values[max(0, math.ceil(len(values)*q)-1)] for p,q in [('p50', .5),('p95', .95)]} | {'n':len(values)}
        counts = Counter()
        measured = 0
        for row in group:
            if row.get('stt_counts') is not None:
                counts.update(row['stt_counts'])
                measured += 1
        rate = lambda n,d: round(counts[n]/counts[d],4) if counts[d] else None
        stt = {'cases': measured, 'wer':rate('word_errors','reference_words'),
               'cer':rate('char_errors','reference_chars'), 'number_accuracy':rate('numbers_right','numbers_total'),
               'unit_accuracy':rate('units_right','units_total'), 'number_unit_accuracy':rate('pairs_right','pairs_total'),
               'term_accuracy':rate('terms_right','terms_total'), 'counts':dict(counts)} if measured else None
        routed = [r['router'] for r in group if isinstance(r.get('router'), dict)]
        router = None
        if routed:
            model_calls = sum(bool(r.get('model_called')) for r in routed)
            completed = sum(bool(r.get('usage')) for r in routed if r.get('model_called'))
            handled_by = Counter(r['handled_by'] for r in routed if r.get('handled_by'))
            fallback_reasons = Counter(
                r['fallback_reason'] for r in routed if r.get('fallback_reason'))
            timeouts = fallback_reasons['timeout']
            usage = Counter()
            for routed_row in routed:
                usage.update(routed_row.get('usage') or {})
            router = {
                'model_calls': model_calls,
                'model_completed_with_usage': completed,
                'handled_by': dict(sorted(handled_by.items())),
                'fallback_reasons': dict(sorted(fallback_reasons.items())),
                'fallbacks': handled_by['fallback_rules'],
                'timeouts': timeouts,
                'timeout_rate': round(timeouts / model_calls, 4) if model_calls else None,
                'usage': dict(usage),
            }
        failure_counts=Counter(
            (failure.get('status'),failure.get('code'))
            for row in group for failure in row.get('stt_failures',[])
        )
        stt_failures=[
            {'status':status,'code':code,'count':count}
            for (status,code),count in sorted(
                failure_counts.items(),key=lambda item:(str(item[0][0]),str(item[0][1])))
        ]
        return {
            'stt':stt, 'cases': len(group), 'understood': sum(r['understood'] for r in group),
            'accuracy': round(sum(r['understood'] for r in group)/len(group), 4) if group else None,
            'wrong_state': sum(r['wrong_state'] for r in group), 'english_copy': sum(r['english_copy'] for r in group),
            'emergency_miss':sum(r.get('emergency_miss', False) for r in group),
            'false_alarm':sum(r.get('false_alarm', False) for r in group),
            'duplicate_label': sum(r['duplicate_label'] for r in group), 'incomplete': sum(r['incomplete'] for r in group),
            'cut_off':sum(r.get('cut_off',False) for r in group), 'interrupted': sum(r['interrupted'] for r in group),
            'non_korean': sum(r['korean'] is False for r in group),
            'drops': dict(Counter(d for r in group for d in r['drops'])),
            'stt_failures':stt_failures,
            'latency_ms': {k:percentiles(v) for k,v in timings.items()},
            'router': router,
        }
    tables = {}
    for dimension in ('protocol', 'family', 'protocol_family', 'noise', 'snr', 'effective_snr_db', 'distance_cm', 'denoise', 'voice', 'speed'):
        groups = defaultdict(list)
        for row in rows:
            key = f'{row.get("protocol")}/{row.get("family")}' if dimension == 'protocol_family' else str(row.get(dimension, 'not_measured'))
            groups[key].append(row)
        tables[dimension] = {k:summary(v) for k,v in sorted(groups.items())}
    return {'overall':summary(rows), 'tables':tables}
