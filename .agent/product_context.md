# Product context

Rewritten 2026-10-20 (lane DOC4). What the product is, who it is for, what
evidence the team has, and how success is measured. What the code does is in
`docs/FEATURES.md`; what comes next is in `roadmap.md`; why things were
decided is in `docs/DECISIONS.md`.

## What it is

**VoineyLab** (the company is Voiney) is a voice guide for running a lab's own
protocol. Chemistry and biology wet-lab researchers wearing gloves follow
their protocol without touching a screen:

1. bring a protocol in — PDF, Word or a photo (also a public Google Drive,
   Docs or Sheets link, server-side only for now);
2. the server analyses it and checks every value against the source, and asks
   only what it must, as confirmation cards;
3. the experimenter starts it (the start wizard's last button);
4. step-by-step voice guidance — timers, questions, records, re-asks;
5. a lab notebook and report from what was recorded.

Status: **MVP prototype, not field-validated** ("MVP 시제품 — 현장 미검증").
People have spoken to it only on the development server; nothing has been
measured in a working laboratory.

The server owns workflow state; models only propose. Values and safety
statements come only from the source or from a value a person confirmed; a
term the source leaves unexplained may get a short general explanation,
always labelled "AI 일반 지식". Safety precautions and completion criteria are
never written by the AI.

## What it is not

Not an ELN or LIMS and not a system of record (whether to write records into
one is undecided). Not an autonomous scientist, a safety authority, a
protocol approver or an equipment controller; it does not authorise resuming
work. It claims no GLP, GMP, clinical, 21 CFR Part 11, HIPAA or ISO
compliance, and no complete external integration. An emergency phrase gets a
fixed instruction to stop and contact the lab's emergency channel, nothing
more.

## Users and jobs

Researchers at the bench (the pilot: two master's students, from late October
2026). Their jobs, as the interviews put them:

- "Tell me the exact current action without making me touch a screen."
- "Keep the time for me — I must not miss a timing."
- "Do simple commands at once; only confirm the important data."
- "Use the protocol I uploaded, not different conditions."
- "Record only what I actually said, and not other people's voices."
- After the run: a notebook that helps a researcher, not a voice-test log.

A lab also needs: bringing its own protocol in without code changes, seeing
why a protocol cannot start, a traceable export (Word, Markdown, JSON, CSV)
into its existing notebook, and — for a PI — a way to say "확인함" without an
approval gate.

## Evidence

Counts and dates below are as written in the lane records; the raw interview
and survey records are **not in this repository** (interview and pilot
records belong to the owner named in `docs/GIT_WORKFLOW.md`).

| Evidence | What is recorded | Used for |
|---|---|---|
| Field interviews, 2026-10-07/08 | Three life-science researchers (cell culture, PCR, Western blot). Quotes: "타이밍을 놓치면 안 된다"; "단순한 명령은 바로 수행하고, 중요한 데이터만 확인해 달라"; worry about conditions different from the uploaded protocol; "0.5 mL 를 5 mL 로 읽으면 어떡하나"; worry that other people's speech is recorded; most-sought information: reagent amounts and reaction times | Confirmation modes, forward-by-words, source-only timers, decimal-point rule, source-first pictures, loudness filter |
| Survey, 2026-10-19 | 13 respondents: materials/battery 6, life sciences 5, semiconductors 2. Google Drive the most common place for protocols (5). A request for PI review; "할 일 없이 기다릴 때가 힘들다" | Drive link import, starter-pack fields, the non-blocking "확인함" |
| Advisor comments, 2026-09-22 and 10-02 | Repetitive work and proactive interventions; ambiguous references ("그거 얼마나 넣어?") re-asked mechanically | Timer notices, "그거" re-asks |
| Voice tests | Development server, in-gel protocol, last 2026-10-10. A café test the same day with borrowed earphones picked up bystanders and could not be completed | Voice calibration and loudness filter |
| Report evaluation, 2026-10-19 | The report read as a voice-test log and did not help a researcher | The lab-notebook report |
| Synthetic benches | Voice level, noise endpoint and speaker-verification measurements on synthetic voices and noise only | Thresholds marked "to re-measure" |

The initial-customer profile below is a hypothesis, not evidence.

## Initial customer (hypothesis)

A university lab, core facility, small biotech or CRO team with 5–20 bench
users and one repetitive, low-hazard, multi-step protocol run from PDF or
paper with delayed data entry. The first pilot avoids clinical decisions,
controlled substances, equipment control, and high-hazard or regulated
release work.

## Principles

1. Reliability over personality: bounded truth and stable workflow over
   conversational fluency.
2. Preview before mutation: explanations, history and pictures are
   read-only; an ambiguous combined request asks first.
3. Evidence is visible: source file, page, revision and "where this value
   came from" (원문 · AI 제안 · 검토자 입력) survive every projection.
4. Adoption is a bench problem: noise, gloves, interruptions, a lost
   connection, quick recovery.
5. Analytics are privacy-minimised: numbers, codes and times, not audio,
   words, titles or model output.

## Success measures

The pilot measures, decided 2026-10-16 (`src/voiney_lab/pilot_metrics.py`):
experiments a week and returning users, steps finished without hands,
records and deviations, time to a report, how often an uploaded protocol ran
at once or after review and how long review took, **wrong state changes:
zero**, usability, and willingness to pay. Engineering measures beside them:
first audio and whole-turn latency (p50/p95), speech rejection and
correction rates, analysis pass rate and time.

Offline test success is never a claim of readiness for operational or
regulated use. That needs customer validation, identity and access
controls, approved data handling and a workflow-specific safety review.
