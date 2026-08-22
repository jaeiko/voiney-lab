# Commercialization audit

Audit date: 2026-08-22

Repository: `voice-workflow-agent`

Branch: `refactor/voice-workflow-agent-stability`
Baseline commit: `030779703d7a9bbe0bcfd839829fe7b17c817fab`

## Executive finding

The repository already contained unusually strong deterministic workflow, PDF
evidence, interruption, reporting, and safety primitives, but it was not yet an
honest commercial product. The largest defect was architectural: production
curated voice turns called `CuratedProtocolSession.plan()` before the newer intent
helpers, so learning/audit/history/uncertainty fixes could pass helper tests without
affecting the real WebSocket route. The PDF API also returned a nested upload
object that the browser read as a flat object, preventing automatic analysis.

Those defects are repaired. The current build has one production routing boundary,
an end-to-end PDF upload/analysis/review path, current xAI web-image request fields,
a single Cascade voice configuration, rights-gated visual proxying, privacy-safe
operations metrics, and explicit remaining-risk documentation.

It is suitable for controlled fictional/non-sensitive pilots. It is not yet a
validated operational or regulated lab system.

## Audit method

- Traced audio, WebSocket, routing, workflow mutation, research, TTS, PDF, report,
  and frontend paths from actual production entrypoints.
- Captured a clean baseline and ran the complete offline suite before edits.
- Replayed the required request families through the production curated boundary,
  then added a real `run_turn` WebSocket test.
- Exercised PDF lifecycle and review projections against isolated temporary
  stores and fictional fixtures.
- Checked xAI calls against official documentation current on the audit date.
- Reviewed the production frontend script with Node-backed DOM harnesses and an
  actual browser pass.
- Performed current market research with Exa: 125 search results across four
  workstreams, then deep-read high-signal primary product, survey, research, case
  study, and marketplace pages.

## Finding and remediation ledger

| Severity | Finding | Remediation | Verification |
|---|---|---|---|
| Critical | Real curated turns bypassed newer intent routing | Added immutable shared arbitration and `route_curated_runtime_turn`; `server.run_turn` now uses it | A–G replay and `tests/test_runtime_intent_routing.py` |
| High | Helper tests could not prove production route | Added canonical `turn.route_decision` and real WebSocket boundary assertion | route/intention/origin/mutation observable in events |
| High | Upload response shape did not match frontend | Browser now reads `payload.protocol`, requests analysis, polls, reviews, and refreshes catalog | production JS harness upload → analysis → review → catalog |
| High | PDF analysis was not reviewable enough | Added source-linked read-only review projection for all execution-critical fields and constructs | catalog review tests and fictional fixture matrix |
| High | Development activation could be invoked in operational scope | Fail-closed scope gate; only demo/reference/test scopes permit it | API tests cover allowed and denied scopes |
| High | Provider web images could be hotlinked and xAI fallback had runtime-only missing imports | Added imports, rights gate, SSRF/byte validation, same-origin registry, and source-link fallback | provider adapter, production job, and frontend contract tests |
| Medium | Visual intent could trigger duplicate research/image paths | Dedicated visual job owns image lookup; general research no longer fans out for the same intent | server route and visual tests |
| Medium | xAI request included an undocumented source include | Replaced with documented `include:["no_inline_citations"]`; retained `enable_image_search:true` | fake request-shape tests |
| Medium | Sample config and README claimed a removed Native path | Removed active realtime variables and rewrote current architecture as Cascade-only | configuration test and public voice profile |
| Medium | No safe cross-session operational view | Added fail-closed admin aggregate endpoint/UI plus bounded content-free runtime registry | store, endpoint, runtime, and frontend tests |
| Medium | Older documentation mixed historical phase claims with current behavior | Replaced README/agent contract and marked older phase docs historical | documentation review |

## Production intent behavior

The shared classifier returns one of: `workflow_control`, `learning`,
`protocol_audit`, `history_resume`, `uncertainty`, `combined_learning_next`,
`visual`, `current_step`, `general_qa`, or `unknown`.

| Scenario | Expected behavior | Mutation on first turn |
|---|---|---:|
| A. Why add this reagent? | Explain only from current protocol facts; state limitations | no |
| B. Protocol number/hash? | Speak compact identity; display full revision/hash/source | no |
| C. Continue previous experiment | State honestly when no durable resumable history exists | no |
| D. “I’m not sure” | Bounded clarification/current-step support | no |
| E. Why + go next | Explain rationale, preview next, stage explicit confirmation | no |
| F. Show equipment photo | Preserve state; start bounded visual job if enabled | no |
| G. Current step complete | Run deterministic confirmation/precondition gate | at most one authorized transition |

Compatibility helpers in `completion_intent.py` remain intentionally, but they
project the shared classifier rather than define competing behavior.

## PDF onboarding assessment

The current lifecycle is arbitrary-document capable at the storage and structured
analysis layers. Candidate A is now only an optional development fixture, not a
hard-coded runtime assumption.

Implemented behavior:

- bounded streamed upload, content type/size/encryption/malformed checks;
- immutable source bytes, SHA-256 identity, extracted page text, and deduplication;
- explicit single/chunked analysis with durable progress and safe retry;
- typed validation that preserves exact scientific strings and page evidence;
- review UI for metadata, prerequisites, materials, equipment, sections, steps,
  actions, quantities, conditions, timers, observations, warnings, missing values,
  constructs, conflicts, and readiness reasons;
- fail-closed readiness for unsupported/missing/ambiguous execution semantics;
- separate service approval and non-operational development activation.

Added commercial fixture coverage:

- fictional multi-step protocol with quantities and timer;
- fictional conditional branch retained explicitly and blocked;
- fictional ambiguous/missing execution value retained and blocked;
- existing catalog suite covers corrupt, unsupported, encrypted, oversized,
  deduplicated, long/chunked, failed, retried, and concurrent cases.

Still missing for a commercial approval studio: reviewer identities/roles, revision
diff, clarification assignment, electronic signatures, revocation, tenant policy,
and validated OCR quality controls.

## xAI contract and voice assessment

Official documentation checked on 2026-08-22:

- [Web Search](https://docs.x.ai/developers/tools/web-search) documents
  `tools:[{"type":"web_search","enable_image_search":true}]` and Markdown
  image results.
- [Tool usage details](https://docs.x.ai/developers/tools/tool-usage-details)
  identifies web/image search usage records.
- [Citations](https://docs.x.ai/developers/tools/citations) documents top-level
  citations and `include:["no_inline_citations"]` for Responses.
- [Speech-to-speech](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech)
  identifies `grok-voice-latest` and the shared voice roster.
- [Text-to-speech](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech)
  documents `/v1/tts`, `voice_id`, and Leo as a supported authoritative/strong
  voice.

The application currently uses STT/agent/TTS Cascade, not native speech-to-speech.
`TTS_VOICE=leo` is now the one canonical path. The provider persona and voice are
visible as non-secret runtime capability metadata. Professor-style language is
enforced in the agent system prompt, while deterministic curated replies remain
short and source-bounded.

Image-search cost behavior is explicit in `tool.call` and aggregate metrics. The
public catalog is attempted before one paid xAI image search. Results are limited
to one candidate and rejected for display without rights/source/byte admission.

## Security and privacy assessment

Positive controls already present or strengthened:

- server-only credentials and explicit environment parsing;
- source, revision, turn, generation, and configuration identities on async work;
- append-only workflow/report events and parameterized SQL;
- strict state mutation gates and explicit report confirmation;
- sanitized errors, CSP/no-store asset responses, bounded uploads, and no model
  reasoning in browser events;
- no raw audio retention by default;
- admin token compared as constant-time digests and never logged/stored by UI;
- admin/runtime projections exclude audio, transcripts, free text, private titles,
  report/session IDs, prompts, and reasoning.

Pre-production blockers:

- shared admin token must become OIDC/SSO, tenant RBAC, SCIM, and audited role
  assignment;
- no formal tenant isolation, encryption/backup/retention/deletion control plane;
- no production rate limiting/WAF/abuse policy or centralized telemetry export;
- no completed threat model/penetration test/dependency SBOM process;
- no customer DPA, provider data-flow approval, or regulated validation package;
- report download authorization is identifier-based and requires authenticated
  tenant ownership before real deployments;
- optional diagnostic audio requires an organization-approved retention policy.

## Market landscape

### Direct voice execution competitors

| Product | Current strength | Implication |
|---|---|---|
| [LabVoice](https://www.labvoice.ai/product) | Low-code LabFlow orchestration, voice capture, narration, timers, media/barcodes, instrument/software integration, custom audit trails | Direct benchmark; workflow designer and integrations are mature expectations |
| [LabTwin](https://labforward.io/product) | Voice notes/tables, protocol guidance, prompts, timers, OCR/barcodes, ELN/LIMS/API/IoT, real-time validation | Direct benchmark; data capture and write-back matter more than chat novelty |

### Adjacent AI informatics platforms

| Product | Current strength | Gap this product can exploit |
|---|---|---|
| [Benchling AI](https://www.benchling.com/ai-at-benchling) | Deep structured R&D context, PDF/CRO import, analysis/reporting/models, citations/audit, MCP ecosystem | Heavy platform context; not positioned primarily as hands-free live bench execution |
| [Scispot Scibot](https://www.scispot.com/ai) | Natural-language experiment/data/analytics/reporting across alt-ELN/LIMS/SDMS | Broad lab OS; opportunity for a focused protocol-to-voice layer that integrates rather than replaces |
| [Sapio Elain](https://www.sapiosciences.com/ai-for-drug-discovery/) | Unified ELN/LIMS, agentic design/analysis/workflow, provenance/compliance positioning | Enterprise platform breadth; focused pilots can win on speed-to-value and bounded execution |

### Evidence of pain and willingness to pay

- The CMU [Vitro study](https://www.cs.cmu.edu/~chinmayk/assets/pdfs/2019-DIS-VitroAssistant.pdf)
  found scientists wanted a reliable piece of lab equipment, physical/social
  context, interruption, and support for careful protocol deviation—not a generic
  human-like assistant.
- A Regeneron practitioner case reported temporary paper/phone/whiteboard records,
  174,000 captured data points, multiple accents, and a 1.3% correction rate in Q1
  2024 after adoption. [Bio-IT World case study](https://www.bio-itworld.com/news/2024/07/09/regeneron-uses-voice-to-text-for-science-first-data-digitization-efforts-for-animal-research)
- A 2025 Boehringer Ingelheim case reports two hours saved weekly, faster review,
  and more usable data; treat vendor-reported outcomes as directional until
  independently reproduced. [LabTwin case study](https://labforward.io/blog/casestudy/accelerating-histopathology-workflow-at-boehringer-ingelheim)
- LabVoice publicly lists a one-user, one-month prototype at $1,000, which provides
  a concrete pilot-price anchor but not a full enterprise price. [AWS Marketplace](https://aws.amazon.com/marketplace/pp/prodview-qwpkb6lb3ghli)
- The Pistoia Alliance’s 2025 survey (>200 respondents) reports AI as the top
  investment area, but data silos and skills remain material barriers.
  [Pistoia Alliance](https://pistoiaalliance.org/news/survey-ai-adoption-life-sciences-labs-skills-gap/)
- Deloitte’s April 2025 surveys found realized throughput/error benefits from lab
  modernization while poor interoperability remained a major obstacle.
  [R&D labs](https://www.deloitte.com/us/en/insights/industry/health-care/future-proofing-pharma-rnd-labs.html),
  [QC labs](https://www.deloitte.com/us/en/insights/industry/health-care/biopharma-lab-modernization-digital-transformation-qc-lab-future.html)
- Cenevo’s January 2026 survey (113 respondents) reported only 5% using AI agents
  in production, 58% with privacy/security concerns, and integration as the leading
  practical priority. This is vendor-sponsored evidence and should be weighted
  accordingly. [Survey release](https://www.prnewswire.co.uk/news-releases/second-annual-cenevo-survey-of-life-science-professionals-reveals-future-of-ai-in-modern-labs-302804292.html)

### Recommended wedge and segmentation

Best first segment: 5–50-person biotech/CRO/core-facility teams running repeated,
low-hazard protocols from PDF/paper while entering results later into an existing
ELN. They have visible labor/data-quality pain, shorter buying cycles than global
pharma, and more willingness to pay than individual academic labs.

Offer a fixed-scope 4–6 week pilot: one protocol, 5–20 users, one export or
write-back connector, deployment/training, and a before/after report. A plausible
price hypothesis is $1k–$5k depending on integration; this is an inference from the
public LabVoice prototype anchor, not observed demand for this product. Validate it
with 15–20 buyer interviews before publishing pricing.

Priority order:

1. Fast, trustworthy PDF review and approval.
2. Reliable voice execution, interruption, recovery, and exact data capture.
3. One ELN write-back integration and admin identity/RBAC.
4. Measurable pilot ROI and privacy/security package.
5. Advanced conditionals/multi-day work only after reviewers can govern them.

Avoid leading with autonomous discovery, open-ended co-scientist claims, or
replacement of the ELN/LIMS. The market already has better-capitalized platforms
for those messages, and current buyers prioritize connected, governed data.

## Residual risk and disposition

| Risk | Disposition |
|---|---|
| Real credentials/provider latency not guaranteed in offline CI | bounded opt-in live smoke and facility pilot required |
| Actual browser PDF analysis needs a provider | fake-backed integration plus controlled live fictional PDF when credentialed |
| Noise/accent performance unknown in target labs | collect consented test corpus; publish correction/rejection distributions |
| Arbitrary advanced workflows not executable | preserved in review and fail closed; DAG semantics are roadmap work |
| Commercial authentication/compliance incomplete | explicitly blocks operational launch |
| In-process metrics reset on restart | acceptable for local pilot; export to tenant-aware observability before production |
| Shared report export URLs lack tenant authorization | must be secured before any private/operational deployment |

No remaining issue in this audit is being represented as “production ready” merely
because offline tests pass.
