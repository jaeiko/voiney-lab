#!/usr/bin/env python3
"""Human-only live Google speech matrix. Dry-run estimates never load credentials."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from dataclasses import asdict, dataclass
import json
import math
import os
import re
import sys
import threading
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'src'))
from scripts.sim.audio import attenuate, change_speed, distance_gain, effective_snr_db, mix, noise, NOISES
from scripts.sim.corpus import CORPUS, generate_cases, load_corpus
from scripts.sim.metrics import aggregate, score_case
from scripts.sim.run import output_dir, save_report, prepare_cases
from scripts.sim.split import add_split_arguments
from scripts.sim.runner import run_case
from voiney_lab.audio import pcm_to_wav, samples_to_pcm16
from voiney_lab import voice_providers as vp
from voiney_lab.configuration import GOOGLE_STT_V2_DENOISE_ENV


def failure_settings(exc: Exception) -> list[str]:
    """Only canonical setting names may leave an exception; no body/URL/value."""
    from voiney_lab.setting_names import BY_NAME
    return sorted(set(re.findall(r'\b[A-Z][A-Z0-9_]+\b',str(exc))) & set(BY_NAME))


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class Reservation:
    transport: str
    reserved_usd: float
    utc: str

    @classmethod
    def parse(cls, row: object) -> 'Reservation':
        if not isinstance(row,dict) or set(row)!={'transport','reserved_usd','utc'}:
            raise ValueError('invalid reservation schema')
        amount=row['reserved_usd']
        if row['transport'] not in {'requests','httpx','httpx_async','httpx2','httpx2_async'} or isinstance(amount,bool) or not isinstance(amount,(int,float)) or not math.isfinite(amount) or amount<=0:
            raise ValueError('invalid reservation')
        if not isinstance(row['utc'],str) or datetime.fromisoformat(row['utc']).tzinfo is None:
            raise ValueError('invalid reservation time')
        return cls(row['transport'],float(amount),row['utc'])


class Budget:
    """Reserve before transport, including failures/auth/retries. Never store a URL/body."""
    def __init__(self, max_calls: int, max_usd: float, reserve: float, ledger: Path) -> None:
        if max_calls < 1 or not all(math.isfinite(x) and x > 0 for x in (max_usd, reserve)):
            raise ValueError('positive finite caps required')
        self.max_calls, self.max_usd, self.reserve, self.ledger = max_calls, max_usd, reserve, ledger
        old = [Reservation.parse(json.loads(line)) for line in ledger.read_text().splitlines()] if ledger.exists() else []
        self.calls = len(old)
        self.usd = sum(row.reserved_usd for row in old)
        self.lock = threading.Lock()
        self.stopped = False

    def take(self, transport: str) -> None:
        with self.lock:
            if self.calls >= self.max_calls or self.usd + self.reserve > self.max_usd + 1e-9:
                self.stopped = True
                raise BudgetExceeded('live budget exhausted')
            with self.ledger.open('a', encoding='utf-8') as f:
                f.write(json.dumps(asdict(Reservation(transport,self.reserve,datetime.now(timezone.utc).isoformat())))+'\n')
                f.flush()
                os.fsync(f.fileno())
            self.calls += 1
            self.usd += self.reserve

    def guard(self, stack: ExitStack) -> None:
        import requests
        import httpx
        request_send = requests.Session.send
        def requests_send(client, *args, **kwargs):
            self.take('requests')
            kwargs['allow_redirects'] = False
            return request_send(client, *args, **kwargs)
        stack.enter_context(patch.object(requests.Session, 'send', requests_send))
        modules = [('httpx', httpx)]
        try:
            import httpx2
        except ImportError:
            pass
        else:
            modules.append(('httpx2', httpx2))
        for name, module in modules:
            def sync_guard(client, *args, _send=module.Client.send, _name=name, **kwargs):
                self.take(_name)
                kwargs['follow_redirects'] = False
                return _send(client, *args, **kwargs)
            async def async_guard(client, *args, _send=module.AsyncClient.send, _name=name, **kwargs):
                self.take(_name + '_async')
                kwargs['follow_redirects'] = False
                return await _send(client, *args, **kwargs)
            stack.enter_context(patch.object(module.Client, 'send', sync_guard))
            stack.enter_context(patch.object(module.AsyncClient, 'send', async_guard))


class CachedSpeech:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(text: str, voice: str) -> str:
        return hashlib.sha256(json.dumps(['google_cloud','LINEAR16',16000,voice,text],ensure_ascii=False).encode()).hexdigest()

    def speak(self, text: str, voice: str) -> bytes:
        import fcntl
        key = self.key(text,voice)
        path = self.folder / f'{key}.wav'
        # Different result ledgers may share this cache; serialize each key.
        with path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if path.exists():
                self.hits += 1
                return vp.wav_to_pcm16(path.read_bytes())
            if not text or len(text) > 2000:
                raise ValueError('TTS text exceeds reserved request bounds')
            self.misses += 1
            pcm = vp.synthesize(text,'ko',vp.TtsProviderSettings(provider='google_cloud',voice=voice,timeout_seconds=30))
            temporary = path.with_suffix(f'.{os.getpid()}.partial')
            temporary.write_bytes(pcm_to_wav(pcm))
            temporary.replace(path)
            return pcm


async def evaluate(args: argparse.Namespace, cases: list, budget: Budget, speech: CachedSpeech, rows: list[dict]) -> list[dict]:
    from scripts.sim.runner import import_server
    from voiney_lab.llm_router import LlmRouterSettings
    server = import_server()
    router = LlmRouterSettings.from_environment() if args.configured_models else LlmRouterSettings()
    if args.configured_models and not router.enabled:
        raise ValueError(router.unavailable_reason or 'VOINEY_LAB_LLM_ROUTER_ENABLED must be true for configured models')
    stt_settings = {
        denoise: vp.SttProviderSettings.from_environment({
            **os.environ,
            vp.STT_PROVIDER_ENV: args.stt_provider,
            vp.STT_MODEL_ENV: args.stt_model,
            GOOGLE_STT_V2_DENOISE_ENV: 'true' if denoise == 'on' else 'false',
        })
        for denoise in args.denoise
    }
    if args.stt_provider != 'google_cloud_v2':
        vp._key(vp.GOOGLE_KEY_ENV if args.stt_provider == 'google_cloud' else vp.ELEVENLABS_KEY_ENV)
    def reply(text, *a, **kw):
        return speech.speak(text,args.reply_voice)
    with ExitStack() as stack:
        budget.guard(stack)
        if not args.configured_models:
            stack.enter_context(patch.object(server,'_role_client', side_effect=RuntimeError('sim_role_unavailable')))
        for p,c in cases:
            for voice in args.voices:
                try:
                    original = speech.speak(c.text, voice) if c.text else samples_to_pcm16(4000*v for v in noise('fan',16000))
                except BudgetExceeded:
                    return rows
                for speed in args.speeds:
                    clean = change_speed(original,speed)
                    for kind, snr in args.conditions:
                        for distance_cm in args.distances:
                            signal_gain = distance_gain(distance_cm)
                            pcm = mix(clean,kind,snr,tail_samples=16000,signal_gain=signal_gain)[0] if kind != 'quiet' else attenuate(clean,signal_gain)
                            for denoise in args.denoise:
                                if budget.stopped:
                                    return rows
                                settings = stt_settings[denoise]
                                def transcribe(pcm, *a, _settings=settings, **kw):
                                    return vp.transcribe(pcm,_settings,language=kw.get('language','ko'),keyterms=kw.get('keyterms',()))
                                result = await run_case(p,c,pcm=pcm,transcriber=transcribe,synthesizer=reply,router_settings=router,offline=False,stt_provider=args.stt_provider)
                                row = score_case(c,result,source=' '.join(page.text for page in p.fixture.draft.extraction.pages),mode='live')
                                row.update(
                                    protocol=p.code, noise=kind, snr=snr if kind!='quiet' else 'quiet',
                                    effective_snr_db=round(effective_snr_db(snr,distance_cm),4) if kind!='quiet' else 'quiet',
                                    distance_cm=distance_cm, denoise=denoise, voice=voice, speed=speed,
                                )
                                rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='authorize real requests; otherwise estimate only')
    parser.add_argument('--corpus',type=Path,default=CORPUS)
    parser.add_argument('--catalog-copy',type=Path,help='append executable uploads from the same snapshot as offline mode')
    parser.add_argument('--protocol',action='append')
    parser.add_argument('--family',action='append')
    parser.add_argument('--limit',type=int)
    parser.add_argument(
        '--case-ids', type=Path,
        help='versioned JSON manifest selecting exact case ids in manifest order',
    )
    parser.add_argument('--voices',default='ko-KR-Chirp3-HD-Charon,ko-KR-Chirp3-HD-Kore')
    parser.add_argument('--speeds',default='0.85,1,1.15')
    parser.add_argument('--conditions',default='quiet,fan:20,centrifuge:10,ultrasonic:10,babble:10,keyboard:5,cleanbench_bsc_fan:10,fume_hood_exhaust:10,glovebox_circulation_pump:10,fan_centrifuge:10,fan_adjacent_speech:10')
    parser.add_argument('--distances',default='30',help='comma-separated microphone distances in cm (30 and 100 for the hood comparison)')
    parser.add_argument('--denoise',default='on',help='comma-separated Chirp 3 denoiser treatments: on,off')
    parser.add_argument('--stt-provider',choices=('google_cloud_v2','google_cloud','elevenlabs'),default='google_cloud_v2')
    parser.add_argument('--stt-model',default='',help='chirp_3 default on V2; latest_long on google_cloud')
    parser.add_argument('--configured-models',action='store_true',help='use shell router/translation roles, within request caps')
    parser.add_argument('--translations', type=Path, help='attach checked stored Korean readings')
    add_split_arguments(parser)
    parser.add_argument('--reply-voice',default='ko-KR-Chirp3-HD-Charon')
    parser.add_argument('--max-calls',type=int,default=200)
    parser.add_argument('--max-usd',type=float,default=5)
    parser.add_argument('--reserve-usd-per-request',type=float,default=.10)
    parser.add_argument('--tts-usd-per-million',type=float,default=30,help='planning assumption, not a verified price')
    parser.add_argument('--stt-usd-per-minute',type=float,default=.024,help='planning assumption, not a verified price')
    parser.add_argument('--cache',type=Path,default=Path.home()/'reports/sim/audio-cache')
    parser.add_argument('--output',type=Path,default=Path.home()/'reports/sim/live.json')
    args = parser.parse_args(argv)
    try:
        if args.limit is None and args.case_ids is None:
            args.limit = 20
        if (args.limit is not None and args.limit < 1) or args.max_calls < 1:
            raise ValueError('positive limits required')
        args.voices = args.voices.split(',')
        known = {v.voice_id for v in vp.KOREAN_VOICES['google_cloud']}
        if not args.voices or any(v not in known for v in (*args.voices,args.reply_voice)):
            raise ValueError('use a known Korean Chirp 3 HD voice')
        args.speeds = [float(v) for v in args.speeds.split(',')]
        if not args.speeds or any(not math.isfinite(v) or not .5<=v<=2 for v in args.speeds):
            raise ValueError('invalid speeds')
        args.conditions = [('quiet',None) if v=='quiet' else (v.split(':')[0],float(v.split(':')[1])) for v in args.conditions.split(',')]
        if any(k!='quiet' and (k not in NOISES or not math.isfinite(s) or not -20<=s<=80) for k,s in args.conditions):
            raise ValueError('invalid noise/SNR')
        args.distances = [float(value) for value in args.distances.split(',')]
        if not args.distances or len(set(args.distances)) != len(args.distances):
            raise ValueError('invalid microphone distances')
        for distance in args.distances:
            distance_gain(distance)
        args.distances = [int(value) if value.is_integer() else value for value in args.distances]
        args.denoise = args.denoise.split(',')
        if not args.denoise or len(set(args.denoise)) != len(args.denoise) or any(value not in {'on','off'} for value in args.denoise):
            raise ValueError('invalid denoise treatments')
        if args.stt_provider != 'google_cloud_v2' and args.denoise != ['on']:
            raise ValueError('denoise comparison requires google_cloud_v2')
        if not all(math.isfinite(v) and v>0 for v in (args.max_usd,args.reserve_usd_per_request,args.tts_usd_per_million,args.stt_usd_per_minute)):
            raise ValueError('invalid prices/caps')
        # 2000 characters/request <= $0.10 under the default planning rate.
        if args.reserve_usd_per_request < max(2000*args.tts_usd_per_million/1e6,15/60*args.stt_usd_per_minute):
            raise ValueError('per-request reserve is below assumed speech cost')
        cases, split, catalog_counts, translation_coverage = prepare_cases(args)
        n = len(cases)*len(args.voices)*len(args.speeds)*len(args.conditions)*len(args.distances)*len(args.denoise)
        chars = sum(len(c.text) for _,c in cases)*len(args.voices)
        matrix = {
            'conditions': [{'noise': kind, 'snr': snr if kind != 'quiet' else 'quiet'} for kind, snr in args.conditions],
            'distance_cm': args.distances,
            'denoise': args.denoise,
            'voices': args.voices,
            'speeds': args.speeds,
        }
        estimate = {'cases':n,'catalog':catalog_counts,'split':split,'translation_coverage':translation_coverage,
                    'matrix':matrix,
                    'input_tts_cache_misses_at_most':len(cases)*len(args.voices),
                    'input_characters':chars,'stt_requests_at_most':n,
                    'input_tts_usd_assumed':round(chars*args.tts_usd_per_million/1e6,4),
                    'stt_usd_assumed_at_15_seconds':round(n*.25*args.stt_usd_per_minute,4),
                    'reply_tts_and_optional_models':'additional, reserved per HTTP attempt',
                    'call_cap':args.max_calls,'reserved_usd_cap':args.max_usd,
                    'rates_verified':False,'external_requests_this_dry_run':0}
        if not args.execute:
            print(json.dumps(estimate,ensure_ascii=False,indent=2))
            return 0
        if args.output.exists() or args.output.with_suffix('.md').exists():
            raise ValueError('output already exists')
        import fcntl
        cache=output_dir(args.cache)
        output_dir(args.output.parent)
        # One cumulative ledger per output, serialised across resumed runs.
        with args.output.with_suffix('.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            ledger=args.output.with_suffix('.ledger.jsonl')
            budget=Budget(args.max_calls,args.max_usd,args.reserve_usd_per_request,ledger)
            speech=CachedSpeech(cache)
            rows=[]
            failure=None
            missing_settings=[]
            try:
                asyncio.run(evaluate(args,cases,budget,speech,rows))
            except Exception as exc:
                failure=type(exc).__name__
                missing_settings=failure_settings(exc)
                print(json.dumps({"error":failure,"settings":missing_settings}),file=sys.stderr)
            report={'schema':1,'mode':'live','stt_measured':any(r.get('stt_responses',0)>0 for r in rows),'acoustics_measured':bool(rows),
                    'latency_measured':'server_endpoint_to_bytes_not_device_playback_cache_included',
                    'utc':datetime.now(timezone.utc).isoformat(),'corpus_sha256':hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
                    'workload_sha256':hashlib.sha256(json.dumps({'cases':[(p.fixture.fixture_sha256,asdict(c)) for p,c in cases],'matrix':matrix},sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                    'router':'configured' if args.configured_models else 'off','catalog':catalog_counts,
                    'provider':args.stt_provider,'model':args.stt_model or vp.DEFAULT_STT_MODELS[args.stt_provider],
                    'provider_requests_including_auth_and_retries':budget.calls,'reserved_usd':round(budget.usd,6),
                    'run_failure_code':failure,'run_failure_settings':missing_settings,'budget_stopped':budget.stopped,'planned_cases':n,'completed_cases':len(rows),
                    'cache_hits':speech.hits,'cache_misses':speech.misses,'estimate':estimate,
                    'matrix':matrix,
                    'split':split, 'translations_sha256':hashlib.sha256(args.translations.read_bytes()).hexdigest() if args.translations else None,
                    'translation_coverage':translation_coverage,
                    **aggregate(rows),'rows':[] if args.split == 'holdout' else rows,
                    'rows_suppressed':args.split == 'holdout'}
            save_report(args.output,report)
            return 3 if budget.stopped else 2 if failure else 0
    except Exception as exc:
        # Provider exception messages can contain credentials, URLs or transcripts.
        print(json.dumps({'error':type(exc).__name__,'settings':failure_settings(exc)}),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
