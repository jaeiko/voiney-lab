# Candidate A grounded Q&A, reports, and Native resilience

Status: development-only design record, implemented offline on 2026-08-11.
Candidate A remains `analysis_required`; Steps 7, 9, and 20 remain fail-closed.

## Root-cause map

| Symptom | Proven production boundary | Cause and repair |
| --- | --- | --- |
| A 6/25 total-step question returned adjacent instructions | `curated_protocol.classify_curated_control_intent()` → `RELATED_QUESTION` → `server.run_turn()` | There was no whole-protocol scope. `PROTOCOL_QUERY` now answers total/current/remaining, ordered overview, preparation, safety, and exact numbered steps from the protected ordered structure without retrieval or Provider access. |
| A useful answer waited behind a 20-second web failure | related-question branch in `server.run_turn()` | Web enrichment was awaited before the primary reply/TTS. The server now sends admitted local evidence and playable audio first, then performs one generation-owned supplement and patches the same Turn. A new accepted utterance cancels the old read-only request. |
| External failures were opaque | `external_references.XaiAuthoritativeWebSearch.search()` | One legacy output shape was the effective gate and broad exceptions lost the phase. The adapter now accepts documented object/dict evidence surfaces, still requires executed web tooling plus an allowlisted supporting citation, and returns a stable sanitized failure category. |
| Report panel stayed empty | server report events → browser `reportEventCurrent()` | The browser required `configuration_id`, but report events omitted it. `run_turn.current_text()` now injects accepted configuration/generation identity centrally. The event contains the application session, current report, and stable report list. |
| Completed event pointed at the new step | `_record_experiment_report_plan()` | Attribution read post-transition state/label. The report captures explicit pre/post step IDs and records the completed pre-transition step with `completion_source=user_command`. |
| TTS failure could erase accepted progress | report write after TTS / checkpoint rollback | Accepted mutations are persisted before TTS. Once that write succeeds, a TTS failure does not roll back the state transition or durable event. A persistence failure still fails closed before presentation. |
| `프로토콜을 종료할게` was not a stop | curated exact command grammar | Anchored Korean/English natural-stop forms now run before quality/scientific routing. Questions such as `종료 조건이 뭐야?` do not match. Repeated stop does not duplicate report finalization. |
| `Cough.` became a normal Turn | post-STT entry to Cascade/Native routing | Shared `language.classify_input_event()` rejects only whole non-lexical events and explicit provider no-speech. Raw labels remain diagnostic; valid short controls and scientific terms remain accepted. |
| Native appeared to reconnect after every answer | `NativeRealtimeSession._supervise()` / `_connect_and_consume()` | `response.done` already ended only one response; the observed transport cause was not provable. Sanitized app-session/connection-epoch/close telemetry was added. A fake 10-turn/5-barge-in session stays on one epoch; one forced stream close performs exactly one bounded resumption. |

## Authoritative control and evidence flow

The precedence is: emergency/stop; start/resume; current/repeat/audio recovery;
completion/navigation; report/anomaly and other server-owned operations;
deterministic protocol structure; exact current/specific/whole-protocol evidence;
approved internal evidence; one optional authoritative web supplement; bounded
clarification/off-topic response. Only the server-owned workflow branch may
mutate state.

Protocol structure answers use `len(fixture.steps)`, the current zero-based
server index, ordered sections, `before_start`, materials/equipment, and explicit
warnings. The known fixture is 25 ordered steps (section counts 1 + 6 + 13 + 3 +
2). At source position 6, the result is current 6/25 and 19 steps after the
current step. It is not inferred from display text or search snippets.

For a related question, `reply.delta`, TTS, audio, and `turn.done` are emitted
from admitted local evidence first. `research.state` and `research.result` carry
`configuration_id`, `turn_id`, `generation`, and a correlation ID. The browser
accepts a late patch only for that known session/Turn/generation, including a
valid historical Turn; it never attaches it to the newest Turn. External text is
read-only, escaped by DOM text nodes, labelled non-protocol, and cannot remove a
step blocker.

## External research contract

Live search is explicit and domain-restricted. Defaults for the Candidate A
launcher are total 20 seconds, connect 3 seconds, read 15 seconds, zero SDK
retries, five citations maximum, and a 900-second in-memory TTL. Only validated
cited success is cached. Cache authority never changes from external context.

Stable outcomes are `disabled`, `cancelled`, `dns_error`, `connect_error`,
`tls_error`, `authentication_error`, `permission_error`, `rate_limited`,
`provider_5xx`, `timeout_connect`, `timeout_read`, `timeout_total`,
`invalid_request`, `unsupported_model`, `response_schema_error`,
`tool_not_executed`, `no_allowed_citation`, `not_found`, and `success`.
Diagnostics expose only model, profile/domain count, phase, status/exception
class, safe request ID, attempt/tool/citation counts, admitted domains, and
duration. Keys, headers, client objects, full Provider bodies, and unrestricted
source content are excluded.

`scripts/diagnose_candidate_a_research.py` is offline by default. `--live`
performs exactly one bounded request only when the existing feature and
credential are enabled. `scripts/evaluate_candidate_a_grounded_qa.py` is always
offline and reports routing, mutation, noise, stop, and latency metrics.

The bounded 2026-08-11 task diagnostic attempted three requests using the
configured five-domain Candidate A profile. AMBIC and HPLC-water requests both
reached the Provider boundary but produced no executed-tool or citation evidence
before the configured eight-second read deadline. The final diagnostic retained
the sanitized classification `timeout_read` (`APITimeoutError`, one attempt,
8.724 seconds, zero admitted citations). Consequently the adapter is
implemented and offline-verified, but successful live evidence admission is not
verified; the immediate local-answer path remains the user-visible fallback.

The Week 5 adapter now consumes the current streamed Responses event shape and
`usage.server_side_tool_usage`, instead of treating the legacy root usage field
as the only proof that search ran. The historical 2026-08-11 measurements above
remain historical evidence; use the current four-profile command in
`CANDIDATE_A_REAL_VOICE_ACCEPTANCE.md` before making a new live reliability
claim.

## Experiment-report contract

One ignored-runtime SQLite report exists per procedure session. Events are
append-only and idempotent by report/event key. The current implementation
records session start, presentation, explicit completion versus navigation,
blocked transition, source consulted, user-reported anomaly, system research
failure, stop, and finalization. Stop has payload `stopped_by_user` and report
status `stopped`; it is never protocol completion.

`experiment.report.state` includes accepted configuration, application session,
Turn/generation, report ID/status/counts, and summaries of all reports in that
session. JSON, Markdown, and UTF-8-BOM CSV exports have stable event order,
download disposition, and no hidden reasoning or stack trace. Automated tests
use temporary databases only. Pause was not added: the curated domain has no
server-authoritative pause/timer checkpoint operation that can be reused safely.
Resume continues to use the existing validated start/resume command.

## Native lifecycle contract

One browser voice session owns one application `NativeRealtimeSession`. One
upstream connection epoch may carry many response IDs. `response.done` clears
only the matching response; it does not terminate the application or provider
session. Barge-in cancels/truncates the response and preserves the transport.
A genuine stream close may schedule the existing bounded resumption using the
conversation ID. Events expose a hashed application-session reference, epoch,
model, reconnect flag, uptime, safe conversation reference, last Provider event,
close initiator/code/reason, and exception category.

The model remains configurable as `grok-voice-latest`. No live compatibility
probe was available during offline implementation, so a versioned alias was not
pinned speculatively. Production pinning requires a successful account-specific
live probe and the manual long-session matrix.

## Comparable-system review

Primary references were reviewed on 2026-08-11.

| Official reference | Observed pattern | Decision |
| --- | --- | --- |
| [OpenAI Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations) | A Session/Conversation persists across multiple Responses; `response.done` is response-scoped. | Adopted as a lifecycle invariant and fake-transport test, without copying provider-specific event names. |
| [OpenAI Realtime VAD](https://developers.openai.com/api/docs/guides/realtime-vad) | Speech start/stop establishes an audio Turn boundary, not semantic proof that the event is language. | Adapted: shared post-STT non-lexical classification complements existing VAD and prefix handling. |
| [OpenAI voice agents](https://developers.openai.com/api/docs/guides/voice-agents) | Chained voice is appropriate when deterministic intermediate logic and durable transcripts matter. | Retained Cascade as Candidate A authority; Native stays comparison-only. |
| [xAI web search](https://docs.x.ai/developers/tools/web-search), [citations](https://docs.x.ai/developers/tools/citations), and [tool usage](https://docs.x.ai/developers/tools/tool-usage-details) | Domain filters, citations, included sources, and server-side tool-usage metadata are distinct response surfaces. | Adapted with strict executed-tool plus allowlisted claim-support admission. |
| [xAI streaming and synchronous tools](https://docs.x.ai/developers/tools/streaming-and-sync) | A synchronous tool workflow waits for the complete tool chain. | Adapted by removing web enrichment from the first useful audio critical path. |
| [xAI Speech-to-Speech](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech) | Provider aliases and realtime event contracts may evolve. | Telemetry/configurability adopted; blind model pin rejected pending a live compatibility probe. |
| [Labguru batch-record materials](https://help.labguru.com/en/articles/10117939-batch-record-materials-used) | Structured experiment records associate materials and activities with a durable record. | Adapted to the existing event store and per-report exports; no proprietary UI copied. |
| [GitHub Actions workflow runs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs) | Auditable runs expose status, cancellation, logs/artifacts, and stable history. | Adapted conceptually as idempotent event status/export; a new workflow platform was rejected. |

## Deferred and manual boundaries

Manual Mac/Whale real audio remains required for cough false-positive rates,
first-word retention, acoustic echo, actual stop during playback, long Native
uptime, close codes, and provider model compatibility. Live xAI evidence quality
and latency require explicit configured credentials and bounded opt-in checks.
No result here approves Candidate A, resolves Steps 7/9/20, makes Native
authoritative, or establishes production safety.
