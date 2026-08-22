# Evaluation strategy

## Release gates

| Area | Required evidence | Target |
|---|---|---:|
| Routing | A–G replay plus real `run_turn` production boundary | 100% expected route; zero accidental mutation |
| Workflow | State checkpoint comparison on every read-only family | zero unauthorized transitions |
| Grounding | Exact number/unit/timer/source preservation fixtures | 100% for supported facts; zero invented values |
| PDF onboarding | Simple, multi-step, ambiguous, conditional, corrupt, encrypted, and long-document cases | fail closed unless guidance-ready and approved |
| Voice | STT admission, VAD, interruption, stale generation, TTS contract | no stale audio; p95 reported, not hidden |
| External research | domain policy, citations, one image-search maximum, proxy/rights gate | no provider content as protocol authority; no hotlinks |
| Privacy | log/event/admin projection tests | no secrets, transcript/audio/free text/IDs in aggregates |
| UX | actual browser at desktop and narrow viewport | no blocking console errors; upload/review/recovery usable |
| Regression | complete offline suite, compilation, JS parse, diff check | all pass |

## Canonical acceptance replay

The replay must cover:

A. “왜 이 시약을 넣나요?” → grounded learning, no mutation.
B. “프로토콜 번호와 해시를 알려줘.” → audit identity, no mutation.
C. “이전 실험 기록에서 이어서 하자.” → honest no-history limitation.
D. “잘 모르겠어.” → bounded uncertainty support, no invented state.
E. “왜 이 단계를 하고 다음으로 넘어가.” → rationale + preview + explicit
   completion gate; the first turn does not advance.
F. “장비 사진 보여줘.” → visual route, optional bounded provider job.
G. “현재 단계 완료했어.” → deterministic completion gate and at most one
   authorized transition.

Run `python scripts/replay_turns.py` and the matching integration test. Helper-only
classification tests do not prove production routing.

## Runtime metrics

Canonical events feed a bounded content-free in-process registry. Report p50/p95
where sufficient samples exist for STT, first token, first sentence, first audio,
tool time, total turn, and playback completion. Persisted experiment events supply
workflow completion, timer, anomaly, and blocker aggregates.

Latency targets are product hypotheses until measured in the target facility:

- status feedback: under 250 ms after endpoint;
- first playable audio: p95 under 1.5 s for deterministic/local turns and under
  3 s for provider-backed turns;
- barge-in to silence: p95 under 300 ms;
- no provider call on deterministic completion-only turns.

Never silently drop slow/error samples to improve metrics.

## Live validation

Offline tests use fakes and are required for every commit. Provider smoke tests are
opt-in, credential-gated, use fictional content, cap calls/results/time, and record
only sanitized timing/status evidence. Browser verification uses the actual served
app, checks the accessibility tree and responsive layout, and exercises recoverable
errors—not just HTML string assertions.
