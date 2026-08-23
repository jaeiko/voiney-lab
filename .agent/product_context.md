# Product context

## Product promise

Voice Workflow Agent is an integration-light bench execution layer between a
reviewed protocol document and an ELN/LIMS. It helps a scientist keep hands and
attention on the experiment while providing concise source-linked instructions,
timers, explicit observation capture, bounded explanations, and an audit-ready
event record.

The product is not an autonomous scientist, safety authority, protocol approver,
or system of record. It must be useful before deep instrument or ELN integration,
but its commercial value increases when canonical events can be written to those
systems under customer governance.

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
- “Show the source page or a clearly labeled external visual.”
- “Let me interrupt, repeat, pause, or ask a side question without losing state.”

Buyer/admin jobs:

- Import and review a customer protocol without code changes.
- Know why a protocol is not executable and who approved a revision.
- See adoption, completion, latency, correction, and blocked-step aggregates
  without exposing research content.
- Export a traceable record and integrate it into the existing informatics stack.

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
5. Admin analytics are privacy-minimized. Aggregate operational metadata, not
   audio, transcripts, private titles, identities, or model reasoning.

## Commercial success measures

- Time from PDF upload to reviewed executable draft.
- Percent of required steps/quantities/timers/observations preserved.
- Zero unauthorized state transitions.
- First-playable-audio and total-turn p50/p95.
- Speech rejection and correction rate by controlled test corpus.
- Documentation completeness and time saved versus baseline.
- Pilot weekly active users, completed workflows, blocked-step distribution, and
  customer-approved expansion intent.

Do not claim readiness for operational or regulated use from offline test success
alone. Production readiness requires customer validation, identity and access
controls, approved data handling, and workflow-specific safety review.
