# Capability Matrix

Date: 2026-08-24  
Maturity: Controlled Pilot Ready, Field-Unvalidated

Revised 2026-10-08 (lane DI): the reviewer/admin rows and the removed
integrations are marked as such below.

Status vocabulary:

- **Implemented and regression-tested** — runnable product code with automated
  production-boundary coverage.
- **Contract-tested** — adapter/protocol exercised against offline fakes; no
  claim that a real account or service worked.
- **Live-tested historically** — a bounded real call is documented in the
  current prior handoff; it was not repeated in this productization pass.
- **Not implemented** — no supported product path exists.

## Product workflows

| Capability | Status | Scope / evidence | Pilot limitation |
|---|---|---|---|
| Protocol upload → start | Implemented and regression-tested | Immutable PDF, automatic OCR and analysis, source-evidence check, execution blockers/notices, the experimenter's start as the one confirmation (`tests/test_lane_di_execution_rule.py`) | No approval step; the laboratory decides on its own which PDFs it uploads |
| Voice start/current/next/complete | Implemented and regression-tested | Cascade, shared arbitration, explicit completion confirmation, stale fences | No noisy-lab human field validation |
| Read-only explanation/audit/history | Implemented and regression-tested | Source-bounded response and non-mutation replay tests | External web context remains supplementary |
| Pause/resume/reconnect | Implemented and regression-tested | Durable versioned session recovery with explicit restored/not-restored disclosure | Pending confirmation, conversation, and active timers are intentionally not restored |
| Manual/voice observation | Implemented and regression-tested | Step-linked append-only `observation_only` entries | Does not update protocol knowledge automatically |
| Evidence upload/download | Implemented and regression-tested | 32 MiB allowlist, content hash, tenant read gate, integrity-checked download | Opaque; no automatic interpretation |
| Experiment timeline/audit | Implemented and regression-tested | Append-only lifecycle, step, observation, evidence, value-confirmation, and recovery events | Not an electronic-signature system |
| Report export | Implemented and regression-tested | JSON, Markdown, CSV, and DOCX | Organization must validate format for its records policy |
| Reviewer screen, approval, revocation | Removed 2026-10-08 (lane DI) | Code in git history; tables kept, unused | — |
| Administrator screen, roles, connectors, pilot metrics | Removed 2026-10-08 (lane DI) | Code in git history; one experimenter identity | — |
| Backup/verify/restore | Implemented and regression-tested | SQLite backup API, allowlisted objects, checksums, safe extraction | Synthetic data only; deployment restore drill required |

## Protocol and knowledge lifecycle

| Capability | Status | Scope / evidence | Pilot limitation |
|---|---|---|---|
| Local PDF ingestion | Implemented and regression-tested | Immutable bytes, bounded extraction, corrupt/encrypted/unsupported failure paths | Private PDFs must remain outside source control |
| Structured protocol analysis | Implemented and contract-tested | Strict typed/evidence validation and fake models | Historical live connectivity did not complete a realistic full-document run |
| Chunked long-document analysis | Implemented and regression-tested | Bounded plans, merge validation, missing/conflict gates | Process-local background tasks |
| OCR lifecycle | Implemented and contract-tested | Trusted injected adapter, automatic run at upload, text accepted as source | No bundled/live OCR provider |
| Step translation | Implemented and regression-tested | Automatic after analysis; numbers/units/names/negation checks, judged at read time | Machine translation; the source is shown when the check refuses |
| Lab adaptation, knowledge promotion | Removed 2026-10-08 (lane DI) | Code in git history | — |
| Asset cards, knowledge entries | Read routes kept, no screen | Tenant metadata | Not inventory management |

## External systems

| Integration | Status | Evidence | Current claim |
|---|---|---|---|
| xAI STT | Live-tested historically | Real 200 Korean round trip recorded in `COMMERCIALIZATION_PASS4_REPORT.md`; fake-backed regression tests | Provider path has worked in the recorded environment; field accuracy unknown |
| xAI TTS | Live-tested historically | Real PCM response recorded in prior handoff; fake-backed regression tests | Provider path has worked in the recorded environment |
| xAI/LLM structured analysis | Contract-tested; live connectivity historically confirmed | Real 200 on a minimal synthetic document, strict result rejected; fake-backed pipeline | Connectivity only, not live end-to-end validation |
| Generic OIDC | Contract-tested | Generated keys/claims, issuer/audience/time/membership tests; no roles claim | No real IdP login |
| Google Drive, GitHub, protocols.io imports | Removed 2026-10-08 (lane DI) | Code in git history | — |
| eLabFTW write-back | Removed 2026-10-08 (lane DI) | Code in git history | — |
| Snakemake / Nextflow metadata | Removed 2026-10-08 (lane DI) | Code in git history | — |
| Web image search / image generation (xAI) | Removed 2026-10-08 (lane DI); lane WV rebuilds | An image request answers `visual_failed` and reads the source | — |
| Generic LIMS synchronization | Not implemented | None | Product is not a full LIMS |

## Explicit non-capabilities

The product has no protocol approval step at all, does not infer experimental
success, derive completion from model prose, replace facility safety review,
import protocols from external services, export to an ELN, provide emergency
response, guarantee regulatory compliance, or support multi-instance high
availability.
