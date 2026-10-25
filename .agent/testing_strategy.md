# Testing and evaluation strategy

Rewritten 2026-10-20 (lane DOC4); it now also holds what the old
`evaluation_strategy.md` and the voice field evaluation plan said that is
still true (lane DOC4 moved both to `docs/archive/`). The commands and the two
pass-count conditions are in `AGENTS.md`, "Verification"; this file says what
the tests must prove.

## 1. What the tests prove

In a wet-lab copilot a software error is an experiment error. The suite
(`tests/`, run with pytest) is layered:

1. **Units**: classifiers, VAD windows, sentence chunking, audio, schema and
   evidence validators.
2. **State machine and gates**: step lifecycle, completion and endpoint
   questions, timers, observations, the execution rule.
3. **Model boundaries**: the router's tool validation, answer checks, the
   outside-PDF explanation, report-writer checks — with fake models.
4. **Production boundary**: full voice turns through `/ws` and the HTTP API
   with `fastapi.testclient`, barge-in, reconnection, recovery.
5. **Screen**: JavaScript run against the real `static/` files from Python
   tests, and browser acceptance tests in `tests/e2e/` (Playwright, desktop
   and mobile Chrome, and two tablet projects — portrait 820×1180 and
   landscape 1180×820, Galaxy Tab S4 touch emulation — that run only
   `tests/e2e/tablet-*.spec.ts`; CI runs them).

Rules:

- Add a production-boundary test, not only a helper test, for routing,
  provider and screen changes. Helper-only classification tests do not prove
  production routing.
- Provider calls are always fake-backed. `tests/conftest.py` removes provider
  keys and blocks outside connections; router models are faked in
  `tests/router_fakes.py`. A test that needs live credentials is not added.
  Say "contract-tested" for these, never "live-tested".
- A test that needs an externally licensed source PDF is skipped when it is
  absent (`tests/conftest.py` `MODULES_REQUIRING_CANDIDATE_A_SOURCE_PDF` and
  per-test checks). That is why the pass count depends on the condition.
- Never weaken or delete a regression test to make a change pass. When a
  decision changes the behaviour a test expected, change only that part of
  the assertion and say so in the pull request.

## 2. What each area must keep

| Area | Must hold | Pinned by (examples) |
|---|---|---|
| Execution rule | An analysis with an execution blocker cannot start; a review-pending analysis has no path to run; the start is the one human confirmation | `tests/test_lane_di_execution_rule.py`, `tests/test_lane_rv_no_path_to_run.py` |
| Completion | Forward words, step-named completion, the completion and endpoint questions; negations, questions and hypotheticals never complete | `tests/test_completion_intent.py`, `tests/test_lane_vx_forward.py`, `tests/test_endpoint_observation_gate.py` |
| Router line | One proposal per turn, verbatim evidence, current step only, refusals take the rules' route, a 429 stops the turn, read-only requests leave checkpoints equal | `tests/test_llm_router.py`, `tests/test_llm_router_wiring.py`, `tests/test_lane_fx3_router_unavailable.py`, `tests/test_rule_parity.py` |
| Grounded answers | Numbers and units only from the cited source; "PDF에서 확인할 수 없어요." for what the source lacks; outside-PDF explanation labelled and without numbers or methods; ungrounded safety instructions removed | `tests/test_router_answer_checks.py`, `tests/test_answer_checks.py`, `tests/test_lane_r6_voice_followups.py` |
| Records | Append-only; the speaker's words kept verbatim, negation kept; corrections appended | `tests/test_lane_n_ledger_verbatim.py`, `tests/test_lane_ts_record_negation.py`, `tests/test_experiment_reports.py` |
| Reports | Exports carry the same record; AI notes pass the checks or are dropped; unconfirmed values marked | `tests/test_lab_report.py`, `tests/test_lane_rp3_notebook.py`, `tests/test_lane_rp3_ai_notes.py` |
| Analysis | Evidence rules, hollow pass, pieces, retries, requote, review cards, safety statements never rewritten | `tests/test_lane_ev_evidence_rules.py`, `tests/test_lane_an2_piece_concurrency.py`, `tests/test_lane_an4_requote.py`, `tests/test_lane_rv2_safety_statements.py` |
| PDF lifecycle | Success, corrupt or unsupported input, long-running status, missing values, unsupported constructs, the execution rule | `tests/test_experiment_protocol_analysis.py`, `tests/test_lane_an_source_formats.py` |
| Listening | VAD endpoint (1000 ms everywhere it is stated), level gate, noise endpoint, self-echo, emergency | `tests/test_vad.py`, `tests/test_lane_fx3_endpoint_pause.py`, `tests/test_lane_sp2_level_endpoint.py`, `tests/test_emergency.py` |
| Accounts and data | Invite-only sign-up, login lock, deletion scope shared by the tool and the account, secrets not kept | `tests/test_lane_on_auth_api.py`, `tests/test_lane_fx5_account_erasure.py`, `tests/test_lane_fx5_secrets_not_kept.py` |
| Privacy of metrics | Aggregates and pilot metrics hold no words, audio or titles | `tests/test_runtime_metrics.py`, `tests/test_lane_log_summaries.py` |
| Settings | The code reads exactly the table's names; no old name anywhere | `tests/test_setting_names.py`, `tests/test_migrate_env.py` |
| Documents | Links, setting names, repository paths and script options in every document outside the archives exist; guide sections the code relies on say what the code does | `tests/test_docs_consistency.py`, `tests/test_lane_log_guide.py`, `tests/test_lane_pk_starter_pack.py`, `tests/test_lane_fx5_guide.py`, `tests/test_lane_px_pymupdf_rules.py` |

Wording is load-bearing: some server sentences are parsed by code and pinned
by tests (`docs/VOICE_AND_TONE.md` section 7). Change a sentence and its test
together.

## 3. Replay and evaluators

- `python scripts/replay_turns.py` sends seven fixed sentences
  (`replay_turns.DEFAULT_TURNS`: why a step, the protocol version, continue
  yesterday's run, will this succeed, explain-and-next, cautions, a picture
  request) through the production rules boundary (router off) and fails when
  a turn produces no route, action, router or answer origin, or a
  non-boolean mutation flag. CI runs it.
- `scripts/evaluate_candidate_a_grounded_qa.py` and
  `scripts/evaluate_candidate_a_hardening.py` check routing invariants on the
  in-gel protocol with the evaluation sets in `data/fixtures/evaluation/`.
  They need the licensed in-gel PDF, so CI skips them; on 2026-10-01 three of
  the hardening set's 100 cases failed and CI runs it for visibility only.
- The router evaluation set used to measure the router-first switch (567
  lines) is kept outside this repository with the lane's report.
- `scripts/score_extraction.py SOURCE.pdf --reference reference.json
  --candidate candidate.json` compares two saved responses in the current
  analysis JSON format. Both are parsed against that exact source with the
  production evidence checks, without a provider or a catalog. It reports
  missing/extra steps, order, text-derived values and counts of source-linked
  typed records (including notes, warnings and constructs). Blank or repeated
  labels use the page and exact quote; ids assigned by the model do not align
  records. Different quotes are reported separately, not guessed into a match.
  Korean text is retained in comparisons. The reference is a hand-built
  baseline, not ground truth; evidence findings and extraction differences
  stay separate and neither becomes an execution verdict. Text-derived value
  parsing covers the existing English time/temperature/volume forms; the
  typed-record comparison preserves other declared values as source strings.
  This does not detect omissions on the live analysis path. `--output` may
  create a report file and refuses to overwrite any existing file.

## 4. Release gates (targets, not measurements)

| Area | Evidence | Target |
|---|---|---|
| Routing | Replay plus the real `run_turn` boundary | expected route; zero accidental mutation |
| Workflow | Checkpoint comparison on every read-only family | zero unauthorised transitions |
| Grounding | Exact number, unit, timer and source preservation fixtures | zero invented values |
| PDF onboarding | Simple, multi-step, ambiguous, conditional, corrupt, encrypted, long | fail closed unless the analysis passes with no blocker |
| Voice | STT admission, VAD, interruption, stale generation, TTS | no stale audio; p95 reported, not hidden |
| Privacy | Log, event and admin projections | no secrets, transcripts, audio, free text or ids in aggregates |
| Screen | Real browser, desktop and phone widths | no blocking console errors; upload, cards, recovery usable |
| Regression | Full suite, compileall, replay, `git diff --check` | all pass |

Latency targets are hypotheses until measured in a lab: status feedback under
250 ms after the endpoint; first playable audio p95 under 1.5 s for rule-only
turns and under 3 s for provider turns; barge-in to silence p95 under 300 ms;
no provider call on a completion-only turn. Runtime metrics
(`runtime_metrics.py`) report p50/p95 where samples suffice; never drop slow
or failed samples to improve a number.

## 5. Field evaluation (not run)

For a same-task comparison with the existing document/timer/notebook workflow
and general voice services, use `docs/PILOT_COMPARISON.md` and its blank CSV.
Record setup and active work separately, keep failed/provider/network attempts
in the denominator and distinguish an actual tablet from CI viewport coverage.
The template contains no measured outcomes.

Nothing has been measured in a working lab. The bench measurements so far
(voice level, noise endpoint, speaker verification) used synthetic voices and
noise and must not be presented as bench performance.

- **Measure**: word error rate, intent accuracy, workflow-command accuracy,
  false-mutation rate, VAD start/end error, endpoint and barge-in latency,
  correction/repeat rate — overall and per condition: room noise (clean, fan,
  centrifuge-like, background talk), SNR (clean, 20, 10, 5 dB), face covering,
  microphone distance (30, 60, 120 cm), Korean regional variation and speed,
  ordinary turn vs interruption vs correction. The false-mutation gate for
  language-mismatched completion, stop, resume, observation, confirmation and
  protocol selection is zero.
- **Synthetic first**: licensed or generated clean commands with opaque ids,
  noise mixed locally at the declared SNR (no audio in git unless its licence
  allows), run through the production microphone/STT/routing boundary, results
  in a local manifest, then `voiney-evaluate <results.json>`
  (`voice_evaluation.py`, `EvaluationCase`; no audio paths or bytes).
- **Consented recordings only**: documented consent, purpose, access,
  encryption, region, deletion and retention ≤ 365 days, a store outside the
  application's data; never the STT diagnostic capture as a substitute. The
  harness refuses a field case without a consent id and bounded retention and
  outputs aggregates only.
- **Triage**: at least two devices; randomised condition order; network and
  provider incidents logged apart from acoustic errors; every false mutation
  reproduced with a synthetic fixture where possible, fixed on the production
  WebSocket path, then the whole matrix re-run. Report an untested cell as
  "not tested", never as zero errors.
