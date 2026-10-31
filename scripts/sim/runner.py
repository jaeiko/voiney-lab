"""Drive the existing ListenerSession -> run_turn boundary without another router."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import socket
import time
from contextlib import ExitStack
from typing import Callable, Any
from unittest.mock import patch

from scripts.sim.audio import oracle_audio
from scripts.sim.corpus import Case, Protocol


def import_server() -> Any:
    # server imports the developer environment at module load; tools must never do that.
    with patch('dotenv.load_dotenv', return_value=False):
        from voiney_lab import server
    return server


# Audit the authority itself, not just a plan's state_changed flag. Discourse,
# replay caches, last-answer context and warning-display caches are not workflow.
STATE_FIELDS = {
    'step': ('current_index',), 'revision': ('_revision',), 'active': ('active',),
    'workflow': ('_workflow_status', '_block_reason', '_experiment_started_at', '_experiment_ended_at'),
    'completed': ('_completed_step_ids', '_endpoint_observations', '_repeat_intervals', '_repeat_returns', '_operator_repetition_counts'),
    'timer': ('_timer_started_at', '_timer_duration_seconds', '_timer_step_index', '_timer_paused_at'),
    'pause': ('_pause_state', '_paused_at', '_total_paused_seconds', '_pause_intervals', '_status_before_pause'),
    'confirmation': ('_pending_completion_confirmation', '_pending_observation_confirmation', '_pending_transcript_confirmation',
                     '_pending_stop_confirmation', '_frozen_question', '_pending_timer_confirmation', '_pending_anomaly_confirmation',
                     '_pending_step_move', '_pending_record_fix', '_pending_note_confirmation', '_pending_run_change'),
    'values': ('_run_changes',), 'record': ('_last_record', '_pending_note_capture', '_report_review'),
    'anomaly': ('_pending_anomaly',), 'notebook': ('_notebook_question', '_notebook_said'),
    'branches': ('_branch_answers', '_registered_repetitions', '_declined_round'),
    'source_ack': ('_acknowledged_unread_pages',),
    'settings': ('confirm_mode','question_timing','ambient_mode','speaker_sensitivity'),
}


def grounded_answer_measurement(
    primary_text: str,
    source_texts: tuple[str, ...],
    protocol_source: str,
) -> dict[str, bool]:
    """Content-safe QA1 measures from admitted evidence, never model traces."""

    from voiney_lab.answer_checks import (
        introduces_numbers,
        ungrounded_safety_instructions,
    )

    primary = str(primary_text or "")
    evidence = "\n".join(source_texts)
    exact = bool(source_texts) and all(
        text in protocol_source and text in primary for text in source_texts
    )
    explicit_absence = not source_texts and "원문에 없음" in primary
    invented_values = introduces_numbers(primary, evidence)
    generated_safety = bool(ungrounded_safety_instructions(primary, evidence))
    return {
        "grounded": bool((exact or explicit_absence) and not invented_values and not generated_safety),
        "invented_values": invented_values,
        "generated_safety": generated_safety,
    }


def snapshot(workflow: Any) -> dict[str, Any]:
    result = {group: copy.deepcopy(tuple(getattr(workflow, name, None) for name in names)) for group,names in STATE_FIELDS.items()}
    return result


class Capture:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.spoken: list[str] = []
        self.first_audio_at: float | None = None
        self.frames = 0

    async def send_text(self, data: str) -> None:
        self.events.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        if data and self.first_audio_at is None:
            self.first_audio_at = time.perf_counter()
        self.frames += len(data)//640


def cut_off_detected(committed: list[Any], *, stt_calls: int) -> bool:
    """Whether endpointing forced or split the audio that reached STT.

    A continuation may create two endpoint events and then prepend the first
    utterance to the second before making one STT call. Keep that split in
    ``endpoint_count`` without calling the joined STT input cut off.
    """

    return any(event.result.forced for event in committed) or (
        len(committed) > 1 and stt_calls > 1
    )


async def run_case(protocol: Protocol, case: Case, *, pcm: bytes | None = None,
                   transcriber: Callable[..., Any] | None = None, synthesizer: Callable[..., bytes] | None = None,
                   router_settings: Any = None, offline: bool = True, classifier: Callable | None = None, stt_provider: str = 'google_cloud', role_client: Callable | None = None,
                   oracle_environment: bool = False,
                   transient_noise_gate: bool = False,
                   transcript_correction: bool = True) -> dict:
    """Only provider boundaries are replaced; emergency/front rules/gates are production code."""
    from voiney_lab.curated_protocol import CuratedProtocolSession
    from voiney_lab.vad import EndpointDetector, VadConfig
    from voiney_lab.language import Transcription
    from voiney_lab.tools import ToolContext
    from pathlib import Path
    with ExitStack() as stack:
        if offline or oracle_environment:
            # Oracle comparisons differ only in their explicit router treatment.
            # Networked oracle runs keep provider authentication, but no other
            # inherited product switches. Their router client is already built.
            safe = ({k:v for k,v in os.environ.items() if k in {'PATH', 'HOME', 'LANG', 'PYTEST_VERSION'}}
                    if offline else {k:v for k,v in os.environ.items()
                                     if not k.startswith('VOINEY_LAB_')})
            stack.enter_context(patch.dict(os.environ, safe, clear=True))
        if offline:
            stack.enter_context(patch.object(socket.socket, 'connect', side_effect=RuntimeError('offline_network_forbidden')))
        # Disable persistence/diagnostic capture; neither mode writes lab runtime data.
        stack.enter_context(patch.dict(os.environ, {
            'VOINEY_LAB_STT_PROVIDER':stt_provider, 'VOINEY_LAB_TTS_PROVIDER':'google_cloud',
            'VOINEY_LAB_WORKSPACE_ENABLED':'false', 'VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED':'false',
            'VOINEY_LAB_STT_DIAGNOSTICS_ENABLED':'false', 'VOINEY_LAB_PUBLIC_KNOWLEDGE_ENABLED':'false',
        }))
        server = import_server()
        workflow = CuratedProtocolSession(protocol.fixture)
        from voiney_lab.configuration import VoiceVadSettings
        vad_config=VadConfig.from_settings(VoiceVadSettings.from_environment().cascade)
        detector = EndpointDetector(vad_config, classifier=classifier or ((lambda frame: any(frame)) if offline else None), listening_onset=True)
        detector.enable_transient_noise_gate(transient_noise_gate)
        session = server.ListenerSession(detector, curated_protocol_session=workflow,
                                         tool_context=ToolContext(Path('/unused/sim'), None, 'ko', 'test_only'),
                                         llm_router_settings=router_settings)
        session.start()
        session.accept_configuration(1, 'cascade', 'ko', protocol.fixture.protocol_id)
        workflow.active = True
        workflow._workflow_status = 'running'
        workflow.current_index = case.index
        if case.setup == 'purpose_question':
            workflow.open_notebook_question('purpose')
        if case.setup == 'result_question':
            workflow.active = False
            workflow._workflow_status = 'completed'
            workflow.open_notebook_question('result')
        if case.setup == 'completion_pending':
            workflow.confirm_mode = 'confirm'
            primed = workflow.front_plan(
                '다음', turn_id=0, language='ko', configuration_id=1,
                generation=1,
            )
            if primed is None or workflow.pending_completion_confirmation is None:
                raise AssertionError('completion_pending setup did not open the real gate')
        if case.setup == 'paused':
            workflow.pause_workflow(now=time.time()-5)
        if case.setup in ('timer_running', 'timer_paused'):
            workflow.start_timer(now=time.time()-2)
            if case.setup == 'timer_paused':
                workflow.pause_timer(now=time.time()-1)
        if case.seed_change is not None:
            from voiney_lab.start_check import check_run_change
            item,number,unit=case.seed_change
            value=next(v for v in workflow.changeable_values() if v.item_id==item)
            workflow.set_run_changes((check_run_change(value,number,unit),))
        before = snapshot(workflow)
        capture = Capture()
        stt_calls = 0
        hypotheses: list[str] = []
        provider_ms: dict[str, float] = {'stt': 0, 'tts': 0}

        def stt(data: bytes, *args: Any, **kwargs: Any) -> Any:
            nonlocal stt_calls
            stt_calls += 1
            start = time.perf_counter()
            try:
                result = transcriber(data, *args, **kwargs) if transcriber else Transcription(case.text, 'ko')
                hypotheses.append(result if isinstance(result, str) else result.text)
                return result
            finally:
                provider_ms['stt'] += (time.perf_counter()-start)*1000

        def tts(text: str, *args: Any, **kwargs: Any) -> bytes:
            capture.spoken.append(text)
            start = time.perf_counter()
            try:
                return synthesizer(text, *args, **kwargs) if synthesizer else bytes(640)
            finally:
                provider_ms['tts'] += (time.perf_counter()-start)*1000

        if not transcript_correction:
            # STT1 'off' treatment: the recognizer's words reach understanding as they are.
            from voiney_lab.transcript_correction import TranscriptCorrection
            stack.enter_context(patch.object(
                server, '_correct_transcript_for_session',
                side_effect=lambda _session, text: TranscriptCorrection(spoken=text, text=text)))
        stack.enter_context(patch.object(server, 'transcribe', side_effect=stt))
        stack.enter_context(patch.object(server, 'synthesize', side_effect=tts))
        # Provider text is never printed or persisted, including exception tracebacks.
        stack.enter_context(patch.object(server.log, 'info'))
        stack.enter_context(patch.object(server.log, 'warning'))
        error_codes=[]
        def error(*args: Any, **kw: Any) -> None:
            import sys
            kind,_,tb=sys.exc_info()
            while tb and tb.tb_next:
                tb=tb.tb_next
            error_codes.append(f'{kind.__name__}:{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}' if kind and tb else 'server_error')
        stack.enter_context(patch.object(server.log, 'exception', side_effect=error))
        if offline or role_client is not None:
            # Missing roles remain visibly unavailable, with no fallback provider.
            stack.enter_context(patch.object(server, '_role_client', side_effect=role_client or RuntimeError('sim_role_unavailable')))
        source = oracle_audio(case.family == 'noise') if pcm is None else pcm
        # Endpoint silence is part of the microphone stream, not the STT payload.
        stream = source + bytes(640 * (detector.config.endpoint_silence_frames + 15))
        endpoints = []
        endpoint_stream_frames = []
        for offset in range(0, len(stream), 640):
            chunk_events = session.accept_chunk(stream[offset:offset+640])
            endpoints.extend(chunk_events)
            # LIVE3: the stream frame at which each end was found, so the
            # delay from the wearer's last frame to the end can be measured.
            endpoint_stream_frames.extend(offset // 640 for e in chunk_events if e.kind == 'speech.end')
        drops = [e.result.rejection_reason or e.kind for e in endpoints if e.kind in {'speech.rejected', 'speech.ignored'}]
        committed = [e for e in endpoints if e.kind == 'speech.end']
        continuation_joins = sum(
            1 for e in endpoints
            if e.kind == 'assistant.interrupted' and e.reason == 'continued_speech')
        started = time.perf_counter()
        for e in committed:
            result = e.result
            await server.run_turn_safely(capture, session, result.utterance, e.turn_id,
                                  result.total_frames, result.voiced_frames, result.prefix_frames_retained)
        pending = tuple(session.visual_tasks)
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=35 if not offline else 2)
            except TimeoutError:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        after = snapshot(workflow)
        invalid_state=[]
        from scripts.sim.measurements import number_units
        if before['timer'] != after['timer'] and case.family.startswith('timer_'):
            duration=workflow._timer_duration_seconds
            source_duration=workflow.timer_seconds_for_step(case.index)
            if duration is not None and duration != source_duration:
                invalid_state.append('timer_duration')
            if case.family=='timer_pause' and duration and workflow._timer_paused_at is None:
                invalid_state.append('timer_pause')
            if case.family=='timer_resume' and workflow._timer_paused_at is not None:
                invalid_state.append('timer_resume')
            if case.family=='timer_cancel' and duration is not None:
                invalid_state.append('timer_cancel')
        if before['values'] != after['values']:
            for change in workflow._run_changes.values():
                if change.step_id != protocol.fixture.steps[case.index].step_id or not set(number_units(f'{change.number} {change.unit}')) <= set(number_units(case.text)):
                    invalid_state.append('value_or_step')
        if case.setup=='run_changed' and workflow._run_changes:
            invalid_state.append('source_not_restored')
        if before['record'] != after['record'] and workflow._last_record:
            record=workflow._last_record
            if str(record.get('text','')) not in case.text or record.get('step_id') != protocol.fixture.steps[case.index].step_id:
                invalid_state.append('record_or_step')
        if before['completed'] != after['completed'] and case.family=='complete':
            expected=set(before['completed'][0]) | {protocol.fixture.steps[case.index].step_id}
            if workflow._completed_step_ids != expected:
                invalid_state.append('completed_steps')
        if before['pause'] != after['pause'] and case.family in {'pause','resume'}:
            if workflow._pause_state != ('paused' if case.family=='pause' else 'active'):
                invalid_state.append('pause_state')
        events = capture.events
        done = next((e for e in reversed(events) if e['type'] == 'turn.done'), {})
        # STT1: what the correction stage made of the recognizer's words
        # (content-free codes; the text leaves only through score_case's rates).
        interpretations = [e for e in events if e['type'] == 'turn.interpretation']
        corrected = ' '.join(str(e.get('corrected_transcript') or e.get('raw_transcript') or '') for e in interpretations)
        correction_codes = sorted({str(code) for e in interpretations for code in (e.get('correction_codes') or ())})
        decision = next((e for e in reversed(events) if e['type'] == 'turn.route_decision'), {})
        reply = next((e for e in reversed(events) if e['type'] == 'reply.delta'), {})
        router_stopped = next((e for e in reversed(events) if e['type'] == 'router.stopped'), {})
        router_measurement = decision.get('router')
        if not isinstance(router_measurement, dict) and router_stopped:
            router_measurement = {
                'handled_by': 'model_unavailable',
                'fallback_reason': router_stopped.get('fallback_reason'),
                'model': getattr(router_settings, 'model', None),
                'usage': {},
            }
        elif (
            not isinstance(router_measurement, dict)
            and done.get('route') == 'model_unavailable'
            and getattr(router_settings, 'enabled', False)
        ):
            # A 429 deliberately stops before turn.route_decision. The public
            # turn.done route is enough to retain that content-free call in
            # SIM; rate_limited is currently the only unavailable router kind.
            router_measurement = {
                'handled_by': 'model_unavailable',
                'fallback_reason': 'model_unavailable:rate_limited',
                'model': getattr(router_settings, 'model', None),
                'usage': {},
            }
        drops += [str(e.get('reason', 'unknown')) for e in events if e['type'] == 'speech.dropped']
        stt_failures=[]
        for event in events:
            if event.get('type')!='speech.dropped' or event.get('reason')!='stt_failed':
                continue
            status=event.get('status')
            if not isinstance(status,int) or isinstance(status,bool) or not 100<=status<=599:
                status=None
            code=event.get('code')
            if not isinstance(code,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',code):
                code='provider_error'
            stt_failures.append({'status':status,'code':code})
        if not committed and not drops:
            drops = ['no_vad_endpoint']
        timings = done.get('timings_ms', {})
        from scripts.sim.metrics import protected_scientific_names
        protected = protected_scientific_names(
            list((protocol.fixture.localizations or {}).values()) + list((protocol.fixture.machine_localizations or {}).values()),
            ' '.join(page.text for page in protocol.fixture.draft.extraction.pages))
        grounding = None
        if case.family == 'problem_question':
            raw_sources = reply.get('source_texts')
            source_texts = tuple(
                item for item in raw_sources if isinstance(item, str)
            ) if isinstance(raw_sources, list) else ()
            grounding = grounded_answer_measurement(
                str(reply.get('primary_text') or ''), source_texts,
                '\n'.join(workflow.problem_knowledge().source_texts),
            )
        return {
            'protected_names':protected,
            'invalid_state':invalid_state, 'error_codes': error_codes, 'action': done.get('result_kind', decision.get('action')), 'intent_kind': done.get('intent_kind'),
            'route': done.get('route'), 'runtime_router':decision.get('runtime_router'),
            'front_rule':workflow.last_front_rule,
            'router_handled':decision.get('router',{}).get('handled_by'), 'changed_fields': sorted(k for k in before if before[k] != after[k]),
            'router': router_measurement,
            'step_before': before['step'][0], 'step_after': after['step'][0],
            'transcript': ' '.join(hypotheses), 'spoken': ' '.join(capture.spoken),
            'corrected_transcript': corrected if interpretations else ' '.join(hypotheses),
            'correction_codes': correction_codes,
            'drop_reasons': sorted(set(drops)), 'stt_failures':stt_failures,
            'stt_calls': stt_calls, 'stt_responses':len(hypotheses),
            'terminal': bool(done or drops), 'endpoint_count': len(committed),
            'endpoint_audio_frames': [e.result.total_frames for e in committed],
            'endpoint_stream_frames': endpoint_stream_frames,
            'continuation_joins': continuation_joins,
            'source_frames': len(source) // 640,
            'cut_off': cut_off_detected(committed, stt_calls=stt_calls),
            'interrupted': any(e['type'] in {'turn.cancelled', 'audio.cancelled'} for e in events),
            'grounding': grounding,
            'timings_ms': {**timings, 'provider_stt_ms': round(provider_ms['stt'], 3),
                           'provider_tts_ms': round(provider_ms['tts'], 3),
                           'boundary_ms': round((time.perf_counter()-started)*1000, 3)},
        }
