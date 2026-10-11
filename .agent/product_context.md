# Product context

Revised 2026-10-10 (lane CL) to the human decisions of that day.

## Product promise

**VoineyLab** (the company is Voiney; "Voice Workflow Agent" is the old name
still found in code and older documents):

> 우리 랩이 올린 프로토콜 그대로, 시간은 대신 재고, 순서는 지켜 주고, 말한 것은
> 노트로 남기는 AI.

Status: **MVP prototype, not field-validated** ("MVP 시제품 — 현장 미검증").
People have spoken to it only on the development server; nothing has been
measured in a working laboratory.

It is a bench execution layer for the laboratory's own uploaded protocol: it
keeps the protocol's order, runs its timers, answers from its source, and turns
what the experimenter says into an append-only record and a report. It helps a
scientist keep hands and attention on the experiment.

The product is not an autonomous scientist, a safety authority, a protocol
approver, an ELN/LIMS, or a system of record. It claims no regulatory
compliance.

## What exists now and what is planned

Now: PDF upload → automatic OCR of pages without a text layer → structured
analysis → source-evidence check → the start screen (the experimenter's
"이 프로토콜로 시작" is the one human confirmation; there is no PI or
administrator approval and no revision approval, rejection or revocation,
decision of 2026-10-08) → voice guidance with timer notices, questions and
pictures → the record → the report (.docx and others).

Planned, in this order of lanes (none of it is built):

- **Review tab (lane RV)** — upload → automatic analysis (about one minute) →
  review (by the uploader or anyone; the reviewer's name is required to
  register) → "검토 완료하고 등록" → the reviewed version is saved (reused,
  recorded as v1/v2) → start check → voice guidance. A value changed in
  review is shown as "검토자 입력" and a value the AI proposes as "AI 제안";
  either becomes a run value only once a person confirms it.
- **Filtering other people's speech** (lane SP1).
- **Mobile web (PWA)** for the screen; an app comes later.
- **Understanding led by the LLM router**, behind the front rules (AGENTS
  rule 3).
- The Answer, Source and Visual roles of `multi_brain.py` come back (lane RA);
  Moss returns for searching past records and manuals; the approved safety
  catalog and the pilot launcher are decided in lane PL.

## Values and explanations

- Values and safety come only from the source or from a value a person
  confirmed.
- What the source leaves unfriendly -- a term's meaning, say -- may get a
  short general explanation, always labelled ("AI 일반 지식").
- Safety precautions and completion criteria are never written by the AI.

## Initial customer and use case

The recommended initial customer is a small biotech, CRO team, university core
facility, or training lab with 5–20 bench users and one repetitive, low-hazard,
multi-step protocol currently run from PDF/paper with delayed data entry. The
first pilot should avoid clinical decisions, controlled substances, autonomous
equipment control, and high-hazard or regulated release workflows.

Primary user jobs:

- “Tell me the exact current action without making me touch a screen.”
- “Explain why this step exists without changing my workflow.”
- “Keep the timer and record only what I actually observed.”
- “Show the source page or a clearly labeled picture.”
- “Let me interrupt, repeat, pause, or ask a side question without losing state.”

Laboratory jobs:

- Upload the laboratory's own protocol without code changes and see why it
  cannot start, if it cannot.
- Review the analysis and register a reviewed version with the reviewer's
  name (planned, lane RV).
- Export a traceable record (.docx, Markdown, JSON, CSV) into the existing
  notebook or informatics stack.
- See aggregate adoption, completion, latency and blocked-step counts without
  exposing research content (`GET /api/admin/metrics`).

## Product principles

1. Reliability over personality. The professor persona is calm and concise, but
   bounded truth and stable workflow behavior matter more than conversational
   fluency.
2. Preview before mutation. Explanations, audits, history, uncertainty, and
   visuals are read-only. Ambiguous combined requests require confirmation.
3. Evidence is visible. Source file, hash, revision, pages, citations, rights, and
   limitations must survive every projection.
4. Adoption is a workflow problem. Support noisy environments, accents, careful
   protocol deviation, interruptions, multimodal displays, and quick recovery.
5. Analytics are privacy-minimized. Aggregate operational metadata, not audio,
   transcripts, private titles, identities, or model reasoning.

## Success measures

- Time from PDF upload to a protocol that can start (the review tab's target
  for the analysis is about one minute).
- Percent of required steps/quantities/timers/observations preserved.
- Zero unauthorized state transitions.
- First-playable-audio and total-turn p50/p95.
- Speech rejection and correction rate by controlled test corpus.
- Documentation completeness and time saved versus baseline.
- Pilot weekly active users, completed workflows, blocked-step distribution, and
  the laboratory's intent to continue.

Do not claim readiness for operational or regulated use from offline test success
alone. Production readiness requires customer validation, identity and access
controls, approved data handling, and workflow-specific safety review.
