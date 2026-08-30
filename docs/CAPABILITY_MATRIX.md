# Capability Matrix

Date: 2026-08-24  
Maturity: Controlled Pilot Ready, Field-Unvalidated

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
| Approved protocol selection | Implemented and regression-tested | Exact revision, source identity, approval context, executable-state checks | Facility must review and approve its own protocol |
| Voice start/current/next/complete | Implemented and regression-tested | Cascade, shared arbitration, explicit completion confirmation, stale fences | No noisy-lab human field validation |
| Read-only explanation/audit/history | Implemented and regression-tested | Source-bounded response and non-mutation replay tests | External web context remains supplementary |
| Pause/resume/reconnect | Implemented and regression-tested | Durable versioned session recovery with explicit restored/not-restored disclosure | Pending confirmation, conversation, and active timers are intentionally not restored |
| Manual/voice observation | Implemented and regression-tested | Step-linked append-only `observation_only` entries | Does not update protocol knowledge automatically |
| Evidence upload/download | Implemented and regression-tested | 32 MiB allowlist, content hash, tenant read gate, integrity-checked download | Opaque; no automatic interpretation |
| Experiment timeline/audit | Implemented and regression-tested | Append-only lifecycle, step, observation, evidence, recovery, and review events | Not an electronic-signature system |
| Report export | Implemented and regression-tested | JSON, Markdown, CSV, and DOCX | Organization must validate format for its records policy |
| Reviewer inbox/diff/decision | Implemented and regression-tested | Impact-first packet, immutable decision history, approve/request revision/disable future use | No delegated/custom approval policy engine |
| Administrator users/permissions | Implemented and regression-tested | Fixed centralized roles, effective permissions, audit events | No custom roles or per-resource grants |
| Connector check/enable | Implemented and regression-tested | Disabled-by-default, credential presence/scope check, explicit enable | Check is not a live provider test |
| Pilot metrics | Implemented and regression-tested | Tenant-scoped completed/failed/recovery/mutation/action counts | Some counts are retention-bounded; no dedicated correction/abandonment KPI |
| Backup/verify/restore | Implemented and regression-tested | SQLite backup API, allowlisted objects, checksums, safe extraction | Synthetic data only; deployment restore drill required |

## Protocol and knowledge lifecycle

| Capability | Status | Scope / evidence | Pilot limitation |
|---|---|---|---|
| Local PDF ingestion | Implemented and regression-tested | Immutable bytes, bounded extraction, corrupt/encrypted/unsupported failure paths | Private PDFs must remain outside source control |
| Structured protocol analysis | Implemented and contract-tested | Strict typed/evidence validation and fake models | Historical live connectivity did not complete a realistic full-document run |
| Chunked long-document analysis | Implemented and regression-tested | Bounded plans, merge validation, missing/conflict gates | Process-local background tasks |
| OCR lifecycle | Implemented and contract-tested | Trusted injected adapter, page review, accept/reject separation | No bundled/live OCR provider |
| Lab adaptation | Implemented and regression-tested | Typed immutable child revision through existing approval | Facility-specific scientific review required |
| Knowledge promotion/translation | Implemented and regression-tested | Provenance, review, protected numeric-token checks | Not autonomous scientific curation |
| Asset cards | Implemented and regression-tested | Tenant metadata, location history, HTTPS references | Not inventory management |

## External systems

| Integration | Status | Evidence | Current claim |
|---|---|---|---|
| xAI STT | Live-tested historically | Real 200 Korean round trip recorded in `COMMERCIALIZATION_PASS4_REPORT.md`; fake-backed regression tests | Provider path has worked in the recorded environment; field accuracy unknown |
| xAI TTS | Live-tested historically | Real PCM response recorded in prior handoff; fake-backed regression tests | Provider path has worked in the recorded environment |
| xAI/LLM structured analysis | Contract-tested; live connectivity historically confirmed | Real 200 on a minimal synthetic document, strict result rejected; fake-backed pipeline | Connectivity only, not live end-to-end validation |
| Google Drive / Shared Drive | Contract-tested | Fake transport, root/cursor/version tests | No live OAuth or file read |
| GitHub | Contract-tested | Fake transport and signed webhook/replay tests | No live GitHub App installation |
| protocols.io | Contract-tested | Fake transport, identity/origin/prefix tests | No live authenticated import |
| Generic OIDC | Contract-tested | Generated keys/claims, issuer/audience/time/membership tests | No real IdP login |
| eLabFTW | Contract-tested | Fake create/PATCH transport, confirmation/idempotency/SSRF tests | No live ELN write-back |
| Snakemake / Nextflow | Implemented metadata-only | Static repository snapshot inspection | Never executes imported code |
| Seqera execution | Not implemented | Interface only | No supported launch path |
| Generic LIMS synchronization | Not implemented | None | Product is not a full LIMS |

## Explicit non-capabilities

The product does not autonomously approve protocols, infer experimental success,
derive completion from model prose, replace facility safety review, execute dry-
lab workflows, provide emergency response, guarantee regulatory compliance, or
support multi-instance high availability.
