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
        transcript='10 mg AMBIC 기록해',stt_calls=1),mode='live')
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


def test_hood_noise_profiles_are_deterministic_and_distinct():
    from scripts.sim.audio import NOISES, noise
    expected = {
        'cleanbench_bsc_fan',
        'fume_hood_exhaust',
        'glovebox_circulation_pump',
        'fan_centrifuge',
        'fan_adjacent_speech',
    }
    assert expected <= set(NOISES)
    samples = {kind: noise(kind, 800, seed=1024) for kind in expected}
    assert all(values == noise(kind, 800, seed=1024) for kind, values in samples.items())
    assert len({tuple(round(value, 6) for value in values[:100]) for values in samples.values()}) == len(expected)


def test_microphone_distance_attenuates_voice_and_reports_effective_snr():
    import math
    from scripts.sim.audio import distance_gain, effective_snr_db, mix, oracle_audio
    from voiney_lab.audio import pcm16_to_samples

    assert distance_gain(30) == pytest.approx(1)
    assert distance_gain(100) == pytest.approx(.3)
    assert effective_snr_db(10, 100) == pytest.approx(10 + 20 * math.log10(.3))

    source = pcm16_to_samples(oracle_audio())
    heard, output_gain = mix(oracle_audio(), 'cleanbench_bsc_fan', 10, signal_gain=.3)
    values = pcm16_to_samples(heard)
    speech_power = sum((value * .3 * output_gain) ** 2 for value in source)
    noise_power = sum((mixed - clean * .3 * output_gain) ** 2 for mixed, clean in zip(values, source))
    assert 10 * math.log10(speech_power / noise_power) == pytest.approx(effective_snr_db(10, 100), abs=.05)


def test_live_plan_has_distance_and_chirp3_denoise_axes_without_requests(monkeypatch, capsys):
    from scripts.sim import live
    from voiney_lab import voice_providers as vp

    def forbidden(*args, **kwargs):
        raise AssertionError('dry run crossed provider boundary')

    monkeypatch.setattr(vp.SttProviderSettings, 'from_environment', forbidden)
    monkeypatch.setattr(vp, 'synthesize', forbidden)
    assert live.main([
        '--limit', '1', '--voices', 'ko-KR-Chirp3-HD-Charon', '--speeds', '1',
        '--conditions', 'cleanbench_bsc_fan:10', '--distances', '30,100', '--denoise', 'on,off',
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['cases'] == 4
    assert result['matrix']['distance_cm'] == [30, 100]
    assert result['matrix']['denoise'] == ['on', 'off']
    assert result['external_requests_this_dry_run'] == 0


def test_english_copy_finds_short_instructions_but_allows_term_names():
    from scripts.sim.metrics import english_copy
    assert english_copy('Mix well.','Mix well.')
    assert english_copy('Add buffer to the sample.','Add buffer to the sample.')
    assert not english_copy('HPLC water를 사용해요.','Use HPLC water.')


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


def test_live_configuration_failure_names_setting_without_exposing_message(tmp_path,monkeypatch,capsys):
    from scripts.sim import live
    from voiney_lab.configuration import ConfigurationError
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    async def failed(*args,**kwargs):
        raise ConfigurationError('VOINEY_LAB_STT_PROVIDER unset; do-not-export-provider-message')
    monkeypatch.setattr(live,'evaluate',failed)
    out=tmp_path/'reports/sim/failed.json'
    assert live.main(['--execute','--limit','1','--output',str(out)])==2
    report=json.loads(out.read_text())
    assert report['run_failure_settings']==['VOINEY_LAB_STT_PROVIDER']
    assert report['stt_measured'] is False
    assert report['acoustics_measured'] is False
    assert 'do-not-export' not in out.read_text()
    captured=capsys.readouterr()
    assert 'VOINEY_LAB_STT_PROVIDER' in captured.err
    assert 'do-not-export' not in captured.out+captured.err


def test_live_vad_drop_is_not_an_asr_measurement_and_stt_failure_is_counted():
    from dataclasses import replace
    p=load_corpus()[0]
    c=replace(next(c for c in generate_cases(p) if c.family=='current'),text='현재 단계 알려줘')
    result=dict(action=None,intent_kind=None,route=None,changed_fields=[],step_before=0,step_after=0,
                spoken='',drop_reasons=['no_vad_endpoint'],terminal=True,timings_ms={},transcript='',stt_calls=0)
    row=score_case(c,result,mode='live')
    assert row['stt'] is None
    assert row['stt_counts'] is None
    result.update(stt_calls=1,drop_reasons=['stt_failed'])
    row=score_case(c,result,mode='live')
    assert row['stt']['failed_requests']==1
    assert row['stt_counts']['reference_chars']>0


def test_live_stt_failure_keeps_only_provider_status_and_code():
    from dataclasses import replace

    from voiney_lab.voice_providers import SpeechProviderError

    protocol=load_corpus()[0]
    case=replace(
        next(case for case in generate_cases(protocol) if case.family=='current'),
        text='현재 단계 알려 줘',
    )

    def refused(*args,**kwargs):
        raise SpeechProviderError(
            'private provider reply must not be exported',status=404,code='not_found')

    result=asyncio.run(run_case(protocol,case,transcriber=refused))
    assert result['stt_failures']==[{'status':404,'code':'not_found'}]
    assert 'private provider reply' not in json.dumps(result,ensure_ascii=False)
    row=score_case(case,result,mode='live')
    assert row['stt_failures']==[{'status':404,'code':'not_found'}]
    overall=aggregate([row])['overall']
    assert overall['stt_failures']==[{'status':404,'code':'not_found','count':1}]


@pytest.mark.parametrize('family,text',[
    ('current','지금 단계 읽어 줘'),
    ('repeat','현재 단계 다시 읽어 줘'),
    ('complete','다 했어'),
    ('source','원문대로 할게'),
])
def test_scoring_accepts_canonical_readback_and_confirmation_results(family,text):
    p=load_corpus()[0]
    c=next(c for c in generate_cases(p) if c.family==family and c.text==text)
    result=asyncio.run(run_case(p,c))
    assert result['changed_fields']==([] if family!='complete' else ['confirmation'])
    assert score_case(c,result)['understood'] is True


def test_source_outside_value_can_be_refused_or_staged_but_never_applied():
    p=next(p for p in load_corpus() if p.field=='chemistry')
    c=next(c for c in generate_cases(p) if c.family=='value_outside' and c.index==1)
    result=asyncio.run(run_case(p,c))
    assert 'values' not in result['changed_fields']
    assert result['intent_kind'] in {'run_value_change_refused','run_value_change_confirmation_required'}
    assert score_case(c,result)['understood'] is True


def test_source_restoration_after_confirmed_override_uses_real_server():
    p=next(p for p in load_corpus() if p.field=='chemistry')
    c=next(c for c in generate_cases(p) if c.family=='source' and c.setup=='run_changed')
    result=asyncio.run(run_case(p,c))
    assert 'values' in result['changed_fields']
    assert result['intent_kind']=='run_value_reverted'
    assert score_case(c,result)['understood'] is True


def test_unambiguous_source_value_request_changes_only_the_requested_value():
    p=next(p for p in load_corpus() if p.field=='chemistry')
    c=next(c for c in generate_cases(p) if c.code.endswith('.value_change.typed'))
    result=asyncio.run(run_case(p,c))
    assert 'values' in result['changed_fields']
    assert score_case(c,result)['understood'] is True
    assert result['invalid_state']==[]


def test_scientific_unit_case_is_not_erased_in_metrics():
    from dataclasses import replace
    p=load_corpus()[0]
    c=replace(next(c for c in generate_cases(p) if c.family=='current'),text='1 mM 기록해')
    result=dict(action='current',intent_kind=None,route='curated_protocol',changed_fields=[],
                step_before=0,step_after=0,spoken='',drop_reasons=[],terminal=True,timings_ms={},
                transcript='1 mm 기록해',stt_calls=1)
    row=score_case(c,result,mode='live')
    assert aggregate([row])['overall']['stt']['unit_accuracy']==0
    assert row['stt']['number_unit_accuracy']==0


def test_source_quantities_preserve_units_and_do_not_take_verbs_for_units():
    from scripts.sim.measurements import source_values
    assert source_values('1 Mix the sample.')[0]==('1','')
    assert ('2','mM') in source_values('Use 2 mM stock.')
    assert ('10','minutes') in source_values('Mix for 10 minutes.')


def test_signed_decimal_and_exponent_quantities_keep_source_spelling():
    from scripts.sim.measurements import source_values
    values=source_values('Use -20 °C, .5 mL and 1e-3 M.')
    assert ('-20','°C') in values
    assert ('.5','mL') in values
    assert ('1e-3','M') in values


def test_live_missing_stt_key_is_named_before_input_tts(tmp_path,monkeypatch,capsys):
    from scripts.sim import live
    from voiney_lab import voice_providers as vp
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    monkeypatch.delenv(vp.ELEVENLABS_KEY_ENV,raising=False)
    monkeypatch.setattr(vp.SttProviderSettings,'from_environment',lambda *a,**kw:vp.SttProviderSettings('elevenlabs','scribe_v2'))
    def forbidden(*args,**kwargs):
        raise AssertionError('input TTS was attempted before STT key validation')
    monkeypatch.setattr(live.CachedSpeech,'speak',forbidden)
    out=tmp_path/'reports/sim/no-stt-key.json'
    assert live.main(['--execute','--stt-provider','elevenlabs','--limit','1','--output',str(out)])==2
    result=json.loads(out.read_text())
    assert result['run_failure_settings']==['ELEVENLABS_API_KEY']
    assert result['provider_requests_including_auth_and_retries']==0


def test_comparison_cli_reports_asr_and_latency_deltas_without_gating_noise(tmp_path):
    import subprocess,sys
    from copy import deepcopy
    row=dict(case='a',protocol='p',noise='quiet',snr='quiet',voice='v',speed=1,
             understood=True,wrong_state=False,english_copy=False,duplicate_label=False,incomplete=False,interrupted=False)
    before=dict(schema=1,mode='live',corpus_sha256='same',router='off',provider='google_cloud_v2',model='chirp_3',rows=[row],
        overall=dict(understood=1,wrong_state=0,english_copy=0,duplicate_label=0,incomplete=0,interrupted=0,
                     stt={'wer':.2,'cer':.1},latency_ms={'stt':{'p50':500,'p95':1000,'n':1}}))
    after=deepcopy(before)
    after['overall']['stt']['wer']=.1
    after['overall']['latency_ms']['stt']['p95']=1200
    a,b=tmp_path/'before.json',tmp_path/'after.json'
    a.write_text(json.dumps(before)); b.write_text(json.dumps(after))
    result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/sim/compare.py'),str(a),str(b)],capture_output=True,text=True)
    assert result.returncode==0
    report=json.loads(result.stdout)
    assert report['metrics']['stt.wer']['delta']==pytest.approx(-.1)
    assert report['metrics']['latency_ms.stt.p95']['delta']==200
    assert report['metrics']['latency_ms.stt.n']['after']==1


def test_failed_turn_cannot_authorize_even_an_expected_step_change():
    p=load_corpus()[0]
    c=next(c for c in generate_cases(p) if c.family=='complete' and c.target==1)
    result=dict(action='next',intent_kind='next_step',route='curated_protocol',changed_fields=['step','revision'],
                step_before=0,step_after=1,spoken='',drop_reasons=[],terminal=False,timings_ms={},transcript=c.text)
    assert score_case(c,result)['wrong_state'] is True


@pytest.mark.parametrize('enabled,expected',[
    ('false',['VOINEY_LAB_LLM_ROUTER_ENABLED']),
    ('true',['VOINEY_LAB_ROUTER_MODEL','VOINEY_LAB_ROUTER_PROVIDER']),
])
def test_configured_live_router_names_missing_settings_before_speech(tmp_path,monkeypatch,capsys,enabled,expected):
    from scripts.sim import live
    from voiney_lab import llm_router
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    monkeypatch.setenv(llm_router.LLM_ROUTER_ENABLED_ENV,enabled)
    monkeypatch.setenv(llm_router.ROUTER_FIRST_ENV,'false')
    for name in (llm_router.ROUTER_MODEL_ENV,llm_router.ROUTER_PROVIDER_ENV):
        monkeypatch.delenv(name,raising=False)
    def forbidden(*args,**kwargs):
        raise AssertionError('speech attempted before router configuration validation')
    monkeypatch.setattr(live.CachedSpeech,'speak',forbidden)
    out=tmp_path/'reports/sim/router-settings.json'
    assert live.main(['--execute','--configured-models','--limit','1','--output',str(out)])==2
    result=json.loads(out.read_text())
    assert result['run_failure_settings']==expected
    assert result['provider_requests_including_auth_and_retries']==0
    assert result['completed_cases']==0
    captured=capsys.readouterr()
    assert all(name in captured.err for name in expected)


@pytest.mark.parametrize('field',['biology','chemistry','materials'])
def test_return_to_future_step_is_a_valid_refusal_without_any_state_change(field):
    p=next(p for p in load_corpus() if p.field==field)
    c=next(c for c in generate_cases(p) if c.family=='jump' and '돌아가' in c.text)
    assert c.index==0
    result=asyncio.run(run_case(p,c))
    assert result['action']=='decline_completion'
    assert result['intent_kind']=='repeat_return_refused'
    assert result['changed_fields']==[]
    assert score_case(c,result)['understood'] is True


def test_term_cases_use_verbatim_scientific_names_instead_of_english_instruction_words():
    ordinary={'incubate','use','carefully','cells','create','block','transfer','weigh','preparation','the','for','are'}
    for p in load_corpus():
        terms=[c for c in generate_cases(p) if c.family=='term']
        assert terms
        for c in terms:
            source=p.fixture.steps[c.index].instruction_source_text
            assert c.terms and c.terms[0] in c.text
            assert all(t in source and t.casefold() not in ordinary for t in c.terms)
    from scripts.sim.measurements import source_terms
    assert source_terms('Use 5 mL BSA. Incubate IN THE DARK with Mini-PROTEAN.')==('BSA','Mini-PROTEAN')


def test_live_plan_selects_uploaded_protocol_from_same_executable_catalog_loader(tmp_path,monkeypatch,capsys):
    from dataclasses import replace
    from scripts.sim import live,catalog
    from voiney_lab import voice_providers as vp
    p=replace(load_corpus()[0],code='catalog_contract')
    snapshot=tmp_path/'reports/sim/catalog-copy'
    seen=[]
    def load(path):
        seen.append(path)
        return (p,),{'loaded':1,'not_executable':2,'too_short':0}
    def forbidden(*args,**kwargs):
        raise AssertionError('plan crossed provider boundary')
    monkeypatch.setattr(catalog,'load_catalog',load)
    monkeypatch.setattr(vp.SttProviderSettings,'from_environment',forbidden)
    monkeypatch.setattr(vp,'synthesize',forbidden)
    assert live.main(['--catalog-copy',str(snapshot),'--protocol',p.code,'--limit','1',
                      '--voices','ko-KR-Chirp3-HD-Charon','--speeds','1','--conditions','quiet'])==0
    result=json.loads(capsys.readouterr().out)
    assert seen==[snapshot]
    assert result['cases']==1
    assert result['catalog']=={'loaded':1,'not_executable':2,'too_short':0}
    assert result['external_requests_this_dry_run']==0
