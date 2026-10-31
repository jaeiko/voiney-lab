#!/usr/bin/env python3
"""Score a person's reading recordings on the product path (lane STT1); a person runs it.

Input: folders under ``data/runtime/voice-eval/`` (or ``--recordings``) each
holding the ``manifest.json`` and ``wav/`` the recording page
(``scripts/sim/record_kit/index.html``) bundled. Every item is one sentence
of the fixed reading list (``scripts/sim/manifests/stt1_record_60.json``);
its WAV goes through the real ``ListenerSession`` -> Chirp 3 -> correction
stage -> understanding boundary (``scripts/sim/runner.run_case``), once per
denoiser treatment, and the correction treatments share each recognizer
response (``off`` costs no call). Replies are not synthesized.

Scores are the SIM's (understanding, wrong state, WER/CER, number/unit and
term accuracy before and after the correction stage, correction codes) by
condition, microphone, denoiser, correction, field and family. No audio,
transcript or reply leaves the machine; the output is aggregates and
content-free rows under ``~/reports/sim/``.

    python scripts/sim/recordings.py --recordings data/runtime/voice-eval \\
        --output ~/reports/sim/stt1-recordings.json           # plan only, 0 calls
    python scripts/sim/recordings.py --recordings data/runtime/voice-eval --execute \\
        --denoise on,off --correction on,off --max-calls 150 --max-usd 15 \\
        --output ~/reports/sim/stt1-recordings.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from scripts.sim.audio import read_wav
from scripts.sim.diagnostic_manifest import development_pairs
from scripts.sim.live import Budget, failure_settings
from scripts.sim.metrics import aggregate, score_case
from scripts.sim.record_kit.build import CONDITIONS, MANIFEST_NAME, MICROPHONES
from scripts.sim.run import output_dir, save_report
from scripts.sim.runner import run_case

_SAFE_FILE = re.compile(r'^wav/[A-Za-z0-9._-]{1,120}\.wav$')
_SAFE_SPEAKER = re.compile(r'^[A-Za-z0-9_-]{1,12}$')
MAX_WAV_BYTES = 16000 * 2 * 60 + 1024


def load_bundles(root: Path) -> list[dict]:
    """Every item of every manifest under ``root``, validated at ingress."""

    items: list[dict] = []
    manifests = sorted(root.rglob('manifest.json'))
    if not manifests:
        raise ValueError('no manifest.json under the recordings folder')
    for manifest_path in manifests:
        raw = manifest_path.read_bytes()
        if len(raw) > 512 * 1024:
            raise ValueError('manifest too large')
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('schema') != 1 or data.get('kit') != MANIFEST_NAME:
            raise ValueError('unsupported recording manifest')
        if not isinstance(data.get('items'), list) or not data['items']:
            raise ValueError('manifest without items')
        for item in data['items']:
            if not isinstance(item, dict):
                raise ValueError('invalid item')
            file = str(item.get('file', ''))
            if not _SAFE_FILE.match(file):
                raise ValueError('invalid recording file name')
            condition = str(item.get('condition', ''))
            microphone = str(item.get('microphone', ''))
            speaker = str(item.get('speaker', ''))
            if condition not in CONDITIONS or microphone not in MICROPHONES or not _SAFE_SPEAKER.match(speaker):
                raise ValueError('invalid condition, microphone or speaker')
            wav_path = manifest_path.parent / file
            if not wav_path.is_file() or wav_path.stat().st_size > MAX_WAV_BYTES:
                raise ValueError('missing or oversized recording')
            items.append({
                'id': str(item.get('id', '')), 'wav': wav_path, 'condition': condition,
                'microphone': microphone, 'speaker': speaker, 'bundle': manifest_path.parent.name,
            })
    return items


def match_cases(items: list[dict]) -> list[tuple[dict, object, object]]:
    by_code = {case.code: (protocol, case) for protocol, case in development_pairs()}
    matched = []
    for item in items:
        if item['id'] not in by_code:
            raise ValueError('recording names an unknown sentence id')
        protocol, case = by_code[item['id']]
        matched.append((item, protocol, case))
    return matched


async def evaluate(args: argparse.Namespace, matched: list, budget: Budget, rows: list[dict]) -> None:
    from voiney_lab import voice_providers as vp
    from voiney_lab.configuration import GOOGLE_STT_V2_DENOISE_ENV
    settings = {
        denoise: vp.SttProviderSettings.from_environment({
            **os.environ, vp.STT_PROVIDER_ENV: 'google_cloud_v2', vp.STT_MODEL_ENV: '',
            GOOGLE_STT_V2_DENOISE_ENV: 'true' if denoise == 'on' else 'false',
        })
        for denoise in args.denoise
    }
    silent = lambda text, *a, **k: bytes(640)  # noqa: E731 - replies are not synthesized here
    with ExitStack() as stack:
        budget.guard(stack)
        for item, protocol, case in matched:
            pcm = read_wav(item['wav'].read_bytes())
            for denoise in args.denoise:
                memo: dict[str, object] = {}

                def transcribe(data, *a, _settings=settings[denoise], _memo=memo, **kw):
                    key = hashlib.sha256(data).hexdigest()
                    if key not in _memo:
                        _memo[key] = vp.transcribe(data, _settings, language=kw.get('language', 'ko'), keyterms=kw.get('keyterms', ()))
                    return _memo[key]

                for correction in args.correction:
                    if budget.stopped:
                        return
                    result = await run_case(
                        protocol, case, pcm=pcm, transcriber=transcribe, synthesizer=silent,
                        offline=False, stt_provider='google_cloud_v2',
                        transcript_correction=correction == 'on')
                    row = score_case(case, result, source=' '.join(page.text for page in protocol.fixture.draft.extraction.pages), mode='live')
                    row.update(
                        protocol=protocol.code, field=protocol.field, noise=item['condition'],
                        condition=item['condition'], microphone=item['microphone'],
                        speaker=hashlib.sha256(item['speaker'].encode()).hexdigest()[:8],
                        snr='human', effective_snr_db='human', distance_cm='human',
                        denoise=denoise, correction=correction, voice='human', speed='human',
                    )
                    rows.append(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--recordings', type=Path, default=ROOT / 'data/runtime/voice-eval')
    parser.add_argument('--execute', action='store_true', help='authorize real Chirp 3 requests; otherwise plan only')
    parser.add_argument('--denoise', default='on,off', help='Chirp 3 denoiser treatments: on,off')
    parser.add_argument('--correction', default='on,off', help='STT1 correction stage treatments: on,off (off reuses the same STT responses)')
    parser.add_argument('--max-calls', type=int, default=150)
    parser.add_argument('--max-usd', type=float, default=15.0)
    parser.add_argument('--output', type=Path, default=Path.home() / 'reports/sim/stt1-recordings.json')
    args = parser.parse_args(argv)
    try:
        args.denoise = args.denoise.split(',')
        args.correction = args.correction.split(',')
        for values, allowed in ((args.denoise, {'on', 'off'}), (args.correction, {'on', 'off'})):
            if not values or len(set(values)) != len(values) or any(v not in allowed for v in values):
                raise ValueError('treatments must be on, off or on,off')
        if args.max_calls < 1 or not args.max_usd > 0:
            raise ValueError('positive caps required')
        items = load_bundles(args.recordings)
        matched = match_cases(items)
        plan = {
            'recordings': len(items), 'bundles': len({item['bundle'] for item in items}),
            'cases': len(items) * len(args.denoise) * len(args.correction),
            'stt_requests_at_most': len(items) * len(args.denoise),
            'conditions': sorted({item['condition'] for item in items}),
            'microphones': sorted({item['microphone'] for item in items}),
            'speakers': len({item['speaker'] for item in items}),
            'call_cap': args.max_calls, 'reserved_usd_cap': args.max_usd,
            'external_requests_this_dry_run': 0,
        }
        if not args.execute:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        if args.output.exists() or args.output.with_suffix('.md').exists():
            raise ValueError('output already exists')
        import fcntl
        output_dir(args.output.parent)
        with args.output.with_suffix('.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            budget = Budget(args.max_calls, args.max_usd, .10, args.output.with_suffix('.ledger.jsonl'))
            rows: list[dict] = []
            failure = None
            missing = []
            try:
                asyncio.run(evaluate(args, matched, budget, rows))
            except Exception as exc:  # provider text never reaches the report
                failure = type(exc).__name__
                missing = failure_settings(exc)
                print(json.dumps({'error': failure, 'settings': missing}), file=sys.stderr)
            report = {
                'schema': 1, 'mode': 'live', 'kind': 'human_recordings', 'stt_measured': bool(rows),
                'utc': datetime.now(timezone.utc).isoformat(), 'provider': 'google_cloud_v2', 'model': 'chirp_3',
                'router': 'off', 'plan': plan, 'provider_requests_including_auth_and_retries': budget.calls,
                'reserved_usd': round(budget.usd, 6), 'run_failure_code': failure, 'run_failure_settings': missing,
                'budget_stopped': budget.stopped, 'completed_cases': len(rows),
                **aggregate(rows), 'rows': rows, 'rows_suppressed': False,
            }
            save_report(args.output, report)
            return 3 if budget.stopped else 2 if failure else 0
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__, 'settings': failure_settings(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
