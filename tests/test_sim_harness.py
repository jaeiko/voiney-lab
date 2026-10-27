"""Provider-free tests of the synthesis regression bench, at the server boundary."""
import asyncio
import json
from pathlib import Path

import pytest

from scripts.sim.corpus import load_corpus, generate_cases
from scripts.sim.runner import run_case
from scripts.sim.metrics import score_case, aggregate


def test_corpus_has_diverse_evidence_and_all_command_families():
    corpus = load_corpus()
    assert len(corpus) >= 8
    assert {'chemistry', 'soil', 'materials'} <= {p.field for p in corpus}
    cases = [c for p in corpus for c in generate_cases(p)]
    assert len(cases) >= 500
    assert {'noise', 'emergency', 'timer_restart', 'value_outside', 'mixed', 'resume'} <= {c.family for c in cases}
    assert all(p.fixture.draft.verified_evidence_count > 0 for p in corpus)


def test_real_server_read_only_and_completion_confirmation():
    protocol = load_corpus()[0]
    cases = {c.family: c for c in generate_cases(protocol)}
    read = asyncio.run(run_case(protocol, cases['current']))
    assert read['route'] == 'curated_protocol'
    assert read['changed_fields'] == []
    assert score_case(cases['current'], read)['wrong_state'] is False


@pytest.mark.xfail(reason='Existing explicit explain+next wording returns current_step_learning; SIM records this miss; no product behaviour change in this lane', strict=False)
def test_existing_mixed_command_must_stage_completion_confirmation():
    protocol=load_corpus()[0]
    case=next(c for c in generate_cases(protocol) if c.family=='mixed' and '왜' in c.text)
    combined=asyncio.run(run_case(protocol,case))
    assert combined['step_after']==combined['step_before']
    assert combined['intent_kind']=='next_step_confirmation_required'


def test_wrong_timer_or_step_is_a_wrong_state_even_on_mutating_case():
    protocol = load_corpus()[0]
    case = next(c for c in generate_cases(protocol) if c.family == 'complete')
    result = {'action': 'next', 'intent_kind': 'next_step', 'route':'curated_protocol', 'step_before': 0,
              'step_after': 99, 'changed_fields': ['step'], 'spoken': '',
              'drop_reasons': [], 'terminal': True, 'timings_ms': {}, 'transcript': case.text}
    assert score_case(case, result)['wrong_state'] is True


def test_noise_never_reaches_stt_or_mutates():
    p = load_corpus()[0]
    c = next(c for c in generate_cases(p) if c.family == 'noise')
    result = asyncio.run(run_case(p, c))
    assert result['stt_calls'] == 0
    assert result['changed_fields'] == []
    assert result['drop_reasons'] == ['no_vad_endpoint']


def test_snr_and_speed_are_deterministic_and_do_not_clip():
    import math
    from voiney_lab.audio import pcm16_to_samples
    from scripts.sim.audio import oracle_audio,mix,change_speed,noise
    pcm=oracle_audio()
    a,gain=mix(pcm,'fan',10)
    b,_=mix(pcm,'fan',10)
    assert a==b
    signal=pcm16_to_samples(pcm)
    mixed=pcm16_to_samples(a)
    sp=sum((s*gain)**2 for s in signal)
    np=sum((m-s*gain)**2 for s,m in zip(signal,mixed))
    assert 10*math.log10(sp/np)==pytest.approx(10,abs=.05)
    assert max(map(abs,mixed)) <=30001
    assert len(change_speed(pcm,1.15))==pytest.approx(len(pcm)/1.15,abs=2)
    with pytest.raises(ValueError):
        mix(pcm,'fan',float('nan'))
    with pytest.raises(ValueError):
        noise('unknown',10)


def test_live_adapter_contract_through_listener_and_server(monkeypatch):
    import base64
    from types import SimpleNamespace
    from scripts.sim.audio import oracle_audio
    from voiney_lab import voice_providers as vp
    from voiney_lab.audio import pcm_to_wav
    protocol=load_corpus()[0]
    case=next(c for c in generate_cases(protocol) if c.family=='current')
    calls=[]
    def post(url, **kw):
        calls.append(url)
        if url==vp.GOOGLE_TTS_URL:
            payload={'audioContent':base64.b64encode(pcm_to_wav(bytes(640))).decode()}
        else:
            payload={'results':[{'alternatives':[{'transcript':case.text}],'languageCode':'ko-KR'}]}
        return SimpleNamespace(ok=True,status_code=200,json=lambda:payload)
    monkeypatch.setattr(vp.requests,'post',post)
    monkeypatch.setenv(vp.GOOGLE_KEY_ENV,'contract-only')
    stt=lambda pcm,**kw:vp.transcribe(pcm,vp.SttProviderSettings('google_cloud','latest_long'),language='ko')
    tts=lambda text,*a,**kw:vp.synthesize(text,'ko',vp.TtsProviderSettings(provider='google_cloud',voice='ko-KR-Chirp3-HD-Kore'))
    result=asyncio.run(run_case(protocol,case,pcm=oracle_audio(),transcriber=stt,synthesizer=tts,offline=False,classifier=lambda frame:any(frame)))
    assert result['stt_calls']==1
    assert result['transcript']==case.text
    assert result['changed_fields']==[]
    assert len(calls)>=2


def test_budget_reserves_failed_attempt_and_never_overruns(tmp_path):
    from scripts.sim.live import Budget,BudgetExceeded
    budget=Budget(2,.2,.1,tmp_path/'ledger.jsonl')
    budget.take('requests')
    budget.take('requests')
    with pytest.raises(BudgetExceeded):
        budget.take('requests')
    resumed=Budget(2,.2,.1,tmp_path/'ledger.jsonl')
    with pytest.raises(BudgetExceeded):
        resumed.take('requests')
    assert len((tmp_path/'ledger.jsonl').read_text().splitlines())==2


def test_comparison_rejects_changed_workloads_and_reports_regression():
    from scripts.sim.compare import compare
    from copy import deepcopy
    row=dict(case='a',protocol='p',noise='quiet',snr='quiet',voice='v',speed=1,understood=True,
             wrong_state=False,english_copy=False,duplicate_label=False,incomplete=False,interrupted=False)
    before={'schema':1,'mode':'offline','corpus_sha256':'s','router':'off','rows':[row],
            'overall':{'understood':1,'wrong_state':0,'english_copy':0,'duplicate_label':0,'incomplete':0,'interrupted':0}}
    after=deepcopy(before)
    after['rows'][0]['wrong_state']=True
    assert compare(before,after)['regressions'][0]['metrics']==['wrong_state']
    after['rows']=[]
    with pytest.raises(ValueError):
        compare(before,after)


def test_reference_alignment_never_uses_recorded_transcript(tmp_path):
    from scripts.sim.references import inventory,align
    for n in (10,2):
        (tmp_path/f'turn-{n}.json').write_text(json.dumps({'turn_id':n,'raw_transcript':'NOT GROUND TRUTH'}))
        (tmp_path/f'turn-{n}.wav').write_bytes(b'wav')
    records=inventory(tmp_path)
    assert all(r['reference']=='' for r in records)
    with pytest.raises(ValueError):
        align(records,[])
    references=align(records,[{'ordinal':1,'reference':'첫 문장'},{'ordinal':2,'reference':'둘째 문장'}])
    assert references[0]['reference']=='첫 문장'
    assert 'NOT GROUND TRUTH' not in json.dumps(references)


def test_score_export_contains_no_transcript_reply_or_path():
    p=load_corpus()[0]
    c=next(c for c in generate_cases(p) if c.family=='current')
    row=score_case(c,{'action':'current','intent_kind':'current_step_information','route':'curated_protocol',
                      'step_before':0,'step_after':0,'changed_fields':[], 'spoken':'민감한 대답',
                      'drop_reasons':[],'terminal':True,'timings_ms':{},'transcript':'민감한 받아쓰기'})
    assert not {'transcript','spoken','reference','path'} & row.keys()
    assert '민감한' not in json.dumps(row,ensure_ascii=False)


@pytest.mark.parametrize('field',['biology','chemistry','materials'])
def test_read_only_state_preservation_across_fields(field):
    protocol=next(p for p in load_corpus() if p.field==field)
    case=next(c for c in generate_cases(protocol) if c.family=='current')
    result=asyncio.run(run_case(protocol,case))
    assert result['route']=='curated_protocol'
    assert result['error_codes']==[]
    assert result['changed_fields']==[]


def test_wrong_timer_duration_is_counted_even_when_timer_change_is_allowed():
    p=load_corpus()[0]
    c=next(c for c in generate_cases(p) if c.family=='timer_start')
    row=score_case(c,dict(action='start_timer',intent_kind='timer_start',route='curated_protocol',
        changed_fields=['timer'],invalid_state=['timer_duration'],step_before=c.index,step_after=c.index,
        spoken='타이머를 시작했어요',drop_reasons=[],terminal=True,timings_ms={},transcript=c.text))
    assert row['wrong_state'] is True


def test_number_unit_and_term_scores_have_separate_denominators():
    from dataclasses import replace
    p=load_corpus()[0]
    c=replace(next(c for c in generate_cases(p) if c.family=='current'),text='10 mL AMBIC 기록해',terms=('AMBIC',))
    row=score_case(c,dict(action='current',intent_kind=None,route='curated_protocol',
        changed_fields=[],step_before=0,step_after=0,spoken='',drop_reasons=[],terminal=True,timings_ms={},
        transcript='10 mg AMBIC 기록해'),mode='live')
    result=aggregate([row])['overall']['stt']
    assert result['number_accuracy']==1
    assert result['unit_accuracy']==0
    assert result['term_accuracy']==1
    assert result['number_unit_accuracy']==0


def test_corrupt_evidence_is_refused_at_load_boundary(tmp_path):
    from scripts.sim.corpus import CORPUS
    data=json.loads(CORPUS.read_text())
    data['protocols'][0]['source_sha256']='0'*64
    target=tmp_path/'corpus.json'
    target.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_corpus(target)


def test_live_dry_run_does_not_load_credentials_or_make_requests(monkeypatch,capsys):
    from scripts.sim import live
    from voiney_lab import voice_providers as vp
    def forbidden(*args,**kwargs):
        raise AssertionError('dry run crossed provider boundary')
    monkeypatch.setattr(vp.SttProviderSettings,'from_environment',forbidden)
    monkeypatch.setattr(vp,'synthesize',forbidden)
    assert live.main(['--limit','1','--voices','ko-KR-Chirp3-HD-Charon','--speeds','1','--conditions','quiet'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['external_requests_this_dry_run']==0
    assert result['cases']==1


def test_transport_cap_reserves_before_a_failed_send(tmp_path,monkeypatch):
    from contextlib import ExitStack
    from scripts.sim.live import Budget,BudgetExceeded
    import requests
    calls=[]
    def failed(*args,**kwargs):
        calls.append(1)
        raise RuntimeError('contract transport failure')
    monkeypatch.setattr(requests.Session,'send',failed)
    budget=Budget(1,.1,.1,tmp_path/'budget.jsonl')
    with ExitStack() as stack:
        budget.guard(stack)
        with pytest.raises(RuntimeError):
            requests.Session().send(None)
        with pytest.raises(BudgetExceeded):
            requests.Session().send(None)
    assert calls==[1]
    assert budget.calls==1


def test_tts_cache_is_voice_specific_and_cached_wav_is_checked(tmp_path,monkeypatch):
    from scripts.sim.live import CachedSpeech
    from voiney_lab import voice_providers as vp
    calls=[]
    def synthesize(*args,**kwargs):
        calls.append(1)
        return bytes(640)
    monkeypatch.setattr(vp,'synthesize',synthesize)
    cache=CachedSpeech(tmp_path)
    for _ in range(2):
        assert cache.speak('현재 단계','ko-KR-Chirp3-HD-Charon')==bytes(640)
    cache.speak('현재 단계','ko-KR-Chirp3-HD-Kore')
    assert len(calls)==2
    assert cache.hits==1
    assert cache.key('현재 단계','ko-KR-Chirp3-HD-Kore')!=cache.key('현재 단계','ko-KR-Chirp3-HD-Charon')
    path=tmp_path/(cache.key('현재 단계','ko-KR-Chirp3-HD-Charon')+'.wav')
    path.write_bytes(b'broken')
    with pytest.raises(vp.SpeechProviderError):
        cache.speak('현재 단계','ko-KR-Chirp3-HD-Charon')


def test_configured_router_proposal_uses_server_confirmation_gate():
    from dataclasses import replace
    from voiney_lab.llm_router import LlmRouterSettings
    from tests.router_fakes import FakeRouterClient,tool_reply
    p=load_corpus()[0]
    c=replace(next(c for c in generate_cases(p) if c.family=='current'),
              text='이제 다음 거 하자',family='router_proposal',actions=('clarify_completion',),allowed=('confirmation',))
    client=FakeRouterClient(tool_reply(('change_state',{'action':'next','evidence':'다음 거 하자'})))
    settings=LlmRouterSettings(enabled=True,provider='xai',model='fake-router-model',timeout_seconds=10)
    result=asyncio.run(run_case(p,c,router_settings=settings,role_client=lambda role:client))
    assert len(client.requests)==1
    assert result['runtime_router']=='llm_router'
    assert result['step_after']==result['step_before']
    assert 'confirmation' in result['changed_fields']
    assert score_case(c,result)['wrong_state'] is False


def test_noise_tail_does_not_dilute_speech_snr():
    import math
    from scripts.sim.audio import oracle_audio,mix
    from voiney_lab.audio import pcm16_to_samples
    source=pcm16_to_samples(oracle_audio())
    pcm,gain=mix(oracle_audio(),'keyboard',10,tail_samples=16000)
    values=pcm16_to_samples(pcm)
    speech_power=sum((v*gain)**2 for v in source)
    noise_power=sum((h-r*gain)**2 for h,r in zip(values,source))
    assert 10*math.log10(speech_power/noise_power)==pytest.approx(10,abs=.05)
    assert len(values)==len(source)+16000
    assert any(values[len(source):])


def test_english_copy_finds_short_instructions_but_allows_term_names():
    from scripts.sim.metrics import english_copy
    assert english_copy('Mix well.','Mix well.')
    assert english_copy('Add buffer to the sample.','Add buffer to the sample.')
    assert not english_copy('HPLC water를 사용해요.','Use HPLC water.')


@pytest.mark.xfail(reason='Existing emergency gate misses eye chemical splash; source server unchanged in SIM; score retains forbidden anomaly mutation',strict=False)
def test_existing_eye_splash_emergency_must_never_record_anomaly():
    p=load_corpus()[0]
    c=next(c for c in generate_cases(p) if c.family=='emergency' and '눈' in c.text)
    result=asyncio.run(run_case(p,c))
    assert result['route']=='deterministic_emergency'
    assert result['changed_fields']==[]


@pytest.mark.parametrize('amount',[float('nan'),-1,True])
def test_corrupt_budget_ledger_fails_closed(tmp_path,amount):
    from scripts.sim.live import Budget
    ledger=tmp_path/'ledger.jsonl'
    ledger.write_text(json.dumps({'transport':'requests','reserved_usd':amount,'utc':'2026-10-27T00:00:00+00:00'})+'\n')
    with pytest.raises(ValueError):
        Budget(10,5,.1,ledger)
