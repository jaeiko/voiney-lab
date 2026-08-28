# Voice Workflow Agent Architecture Map

Snapshot: `071669702767b0441ad3d9e2dd2836318a31494f` on
`refactor/voice-workflow-agent-stability`, before the Laboratory Workflow OS
implementation.

## Product boundary

Voice Workflow Agent is the hands-free execution and knowledge layer beside an
ELN. It does not replace the ELN, approve science autonomously, infer completion,
or execute computational workflows.

```text
approved source revision
        |
        v
browser microphone -> /ws -> Cascade voice turn -> deterministic authority
        |                         |                         |
        |                         |                         +-> protocol/procedure state
        |                         +-> grounded read-only QA
        +-> playback/VAD                 |
                                        +-> experiment report events

commercial HTTP APIs -> tenant workspace -> source/review/integration metadata
```

## Working runtime components and invariants

| Concern | Existing authority | Working responsibility | Extension rule |
|---|---|---|---|
| Browser microphone and playback | `static/index.html`, `static/mic-capture-worklet.js` | Capture PCM, send control/audio frames, render server events, enforce playback identities | Add session/timeline UX around the existing control protocol; do not replace it |
| Cascade connection lifecycle | `server.ListenerSession`, `server.voice_socket` | One WebSocket connection, configuration acceptance, turn/generation identity, cancellation and cleanup | Bind it to a durable experiment ID; keep turn/generation semantics unchanged |
| Audio framing | `audio.FrameBuffer` | PCM frame alignment and conversion | No workflow logic here |
| VAD and barge-in | `vad.EndpointDetector`, `server.ListenerSession` interrupt detector | Speech onset/end, endpointing, prefix preservation, playback interruption | Preserve detector state machine and stale-generation fences |
| STT/TTS | `server.transcribe`, `server.synthesize`, `language` | Provider boundary, Korean-first configuration, language mismatch rejection, PCM validation | Keep network/provider logic separate from workflow state |
| Intent arbitration | `intent_arbitration`, `runtime_routing`, `completion_intent`, `procedures` | Deterministic intent priority and exact command authorization | Add observation intents before generative routing without weakening completion gates |
| Protocol execution authority | `curated_protocol.CuratedProtocolSession`, `procedure_store.ProcedureStore`, `procedures.ProcedureController` | Current step, explicit start/completion, timers, source-defined observations, pause/stop and safety blocking | Durable session state mirrors committed authority; it never supersedes it |
| Human-confirmation checkpoints | `experiment_protocol.human_confirmation_checkpoints`, `CuratedProtocolSession.confirm_human_checkpoint` | Quote a source-defined observable condition, admit only an explicit human answer, then replay the exact source range or leave the loop | The condition and the range come from the represented source; the server never evaluates the condition and model output never reaches this boundary |
| Source-ambiguity resolution | `protocol_catalog.resolve_source_ambiguity`, `experiment_protocol_store` clarifications | Append a new analysis revision carrying the reviewer's reading, actor, role, time, and base revision | The original PDF and earlier analysis revisions are immutable; a range the server cannot execute deterministically is refused, never guessed |
| User-facing status copy | `product_labels` | One Korean label per internal status code, served inside API projections | Pages resolve status wording here instead of mapping lifecycle codes themselves |
| Safety gates | `curated_protocol`, `procedures`, `emergency`, approved-reference modules | Explicit confirmation, missing-value blocks, warnings, emergency routing and approved evidence | Lab adaptations and OCR remain review-required until an exact revision is approved |
| Experiment event/report record | `experiment_reports.ExperimentReportStore`, server report projection | Append explicit workflow/report events and export completed records | Evolve into/associate with a tenant-owned ExperimentSession and preserve append-only history |
| Protocol upload/extraction | `experiment_protocol_pdf`, `experiment_protocol_files`, `protocol_catalog` | Validate/store PDFs, extract text, analyze chunks, review, activate and revoke | Insert OCR only as a provenance-bearing fallback after extraction failure |
| Commercial tenant workspace | `workspace_store.WorkspaceStore`, `identity` | Organizations, memberships, lineage, approval history, knowledge, assets, connectors, analytics and retention | Add forward migrations and tenant-owned experiment/adaptation/evidence tables |
| Source connectors | `protocol_sources` | protocols.io, Drive and GitHub transport validation, version identity and webhook replay controls | Feed immutable lineage revisions and review inbox; credentials remain references |
| ELN connector | `eln_connectors` plus server/workspace boundary | Confirmed, idempotent eLabFTW create/PATCH from completed report projection | Source write-back from a completed ExperimentSession report only |
| Computational metadata | `drylab_workflows`, `workspace_store` | Snakemake/Nextflow repository metadata, review events and wet/dry links | Metadata only; no clone, runner or command execution |
| Role UX | `identity`, workspace HTTP routes, `static/index.html` | Researcher, reviewer and administrator projections | Expose only permission-appropriate actions and tenant-owned resources |

## Current persistence topology

```text
protocol catalog SQLite
  PDF registrations, extracted text, analysis lifecycle, activation/approval

commercial workspace SQLite (schema v1)
  organizations, principals, memberships
  protocol families -> sources -> immutable lineage revisions -> approvals
  connector configuration/cursors/webhook receipts
  knowledge/assets, dry-lab metadata, ELN audit, aggregate analytics

experiment report SQLite (schema v1)
  report keyed by transient WebSocket session_id
  append-only workflow events and final report status

procedure SQLite
  deterministic procedure runtime state for the existing procedure path
```

The important foundation gap is that a WebSocket `ListenerSession` is transient.
Its generated `session_id` owns a report, but there is no durable tenant-owned
`ExperimentSession` entity with pause/resume/recovery semantics. The new model
must therefore be added to the workspace and associated with the existing report
store; replacing `ListenerSession`, `CuratedProtocolSession`, or `ProcedureStore`
would violate the regression boundary.

## Existing request flow

1. The browser opens `/ws`, the server resolves OIDC or an explicitly enabled
   development profile, and the tenant membership is intersected with local
   membership state.
2. The client submits an exact protocol/configuration. The server validates the
   catalog revision and creates a `ListenerSession` configuration fence.
3. Audio is framed and admitted by the existing VAD. Every turn receives a
   monotonically increasing turn ID and connection generation.
4. STT output passes language admission and deterministic intent arbitration.
5. Protocol execution mutations are authorized only by the curated/procedure
   controller. Generative and retrieval paths remain read-only.
6. Accepted mutations are appended to the experiment report. Turn cancellation
   and barge-in results are generation-fenced before playback or mutation.
7. Completed reports can be exported or explicitly written to eLabFTW.

## Phase extension map

| Required phase | Reuse | Missing foundation to add |
|---|---|---|
| 1. Experiment Session | `ListenerSession`, report store, resource bindings | Tenant-owned durable lifecycle, current/completed steps, recovery token/version, voice binding |
| 2. Observation/Event Timeline | Existing report events and source-defined observation gates | General researcher notes, evidence metadata, unified append-only timeline API/UI |
| 3. Lab Adaptation | Immutable lineage revisions and review events | Typed adaptation draft, original/revision relationship, controlled equipment/reagent/note/tip changes |
| 4. Role UX | RBAC and three workspace views | Session dashboards, recovery actions, reviewer adaptation/timeline actions |
| 5. Source Connectors | Strict adapters/configuration/cursors | End-to-end sync-to-review endpoints and OAuth/App authorization metadata |
| 6. ELN | eLabFTW adapter/idempotency audit | Direct association with completed durable sessions and provenance-complete report projection |
| 7. OCR | Extraction failure classification | Bounded OCR provider interface, page provenance, review-required OCR result lifecycle |
| 8. Computational Metadata | Dry-lab store and wet/dry links | Link validation against durable experiment sessions and visible metadata projection |
| 9. Product Polish | Existing bench UI and In-gel protocol support | Clear experiment dashboard/timeline/recovery/status presentation and acceptance polish |

## Regression evidence map

- `test_candidate_a_websocket_integration.py`,
  `test_curated_protocol_cascade.py`, and `test_runtime_intent_routing.py` cover
  the Cascade and protocol-authority boundary.
- `test_vad.py`, `test_audio.py`, and stability tests cover VAD, barge-in and
  stale-turn behavior.
- `test_experiment_reports.py` covers append-only reports and projections.
- `test_protocol_catalog.py`, chunk/PDF tests, and upload tests cover protocol
  lifecycle and safety gates.
- `test_identity_and_workspace.py` and `test_workspace_api.py` cover RBAC,
  tenancy and workspace APIs.
- Connector, ELN and computational metadata tests exercise their external
  contracts without claiming unavailable live credentials.

Every phase must add focused coverage, run the full regression suite, validate a
schema-v1-to-current migration using a copied fixture database, and update the
implementation/migration documentation before the next phase begins.
