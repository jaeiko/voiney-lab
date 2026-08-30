# Deployment Runbook — Controlled Pilot

This documents the reproducible deployment path that already exists in this
repository (`scripts/run_candidate_a.sh` plus a directly-invoked `uvicorn`
process), rather than introducing a new orchestration platform. It is scoped
to a controlled research pilot, not a regulated GxP/clinical release — see
`README.md`'s "Known limitations and deliberate non-goals" section.

## Process model

The application is a single stateless-except-for-SQLite FastAPI process. No
container runtime was available to build/validate in this environment, so no
Dockerfile is included; a systemd unit around the existing launcher is the
smallest reproducible path that could actually be tested here:

```ini
[Unit]
Description=Voice Workflow Agent
After=network.target

[Service]
Type=simple
User=voice-workflow-agent
WorkingDirectory=/opt/voice-workflow-agent
EnvironmentFile=/opt/voice-workflow-agent/.env
ExecStart=/opt/voice-workflow-agent/.venv/bin/uvicorn voice_workflow_agent.server:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Use a dedicated non-root system user and an `EnvironmentFile` (not a
committed file) for secrets, matching the "no fake external validation" and
"never commit `.env`" rules in `AGENTS.md`/`CLAUDE.md`.

## Health and readiness

- `GET /healthz` — liveness only. A monitoring/orchestration layer should
  restart the process if this stops responding.
- `GET /readyz` — configuration readiness. Returns `503` if identity,
  workspace, protocol-catalog, or report configuration fails to parse (for
  example, operational scope without complete OIDC settings); returns `200`
  with non-secret identity mode and capability flags otherwise. This does
  **not** verify live reachability of xAI or any other external provider — no
  local probe should make a billable provider call.

## Durable state and backup

Durable pilot metadata is stored in SQLite, rooted under the directories named by
`VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR`, `VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR`,
and `VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB`. There is no object-store
backend in this build — evidence/asset bytes referenced from these databases
also live under the same data directories (`objects/sha256/` and `evidence/`),
so a backup must capture the allowlisted files as well as the `.sqlite` files.

Use `scripts/pilot_state_backup.py`; its allowlist includes SQLite databases,
protocol objects, and explicitly attached evidence, while excluding `.env`,
raw audio, and unallowlisted files. It uses SQLite's backup API, runs
`PRAGMA quick_check` on source and copied databases, hashes every archived
file, and writes a versioned manifest. Stop the process for a point-in-time
snapshot across all three stores and their object files:

```bash
systemctl stop voice-workflow-agent
sudo -u voice-workflow-agent /opt/voice-workflow-agent/.venv/bin/python \
  /opt/voice-workflow-agent/scripts/pilot_state_backup.py create \
  --protocol-data-dir /var/lib/voice-workflow-agent/protocol \
  --workspace-data-dir /var/lib/voice-workflow-agent/workspace \
  --report-database /var/lib/voice-workflow-agent/reports/experiment_reports.sqlite \
  --output /var/backups/voice-workflow-agent/pilot-pre-session-001.tar.gz
/opt/voice-workflow-agent/.venv/bin/python \
  /opt/voice-workflow-agent/scripts/pilot_state_backup.py verify \
  /var/backups/voice-workflow-agent/pilot-pre-session-001.tar.gz
systemctl start voice-workflow-agent
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
```

The command refuses relative source paths, filesystem roots, output overwrite,
symlinks in a source tree, corrupt SQLite files, unsafe archive entries, and
checksum mismatches. Store the archive on organization-approved encrypted
storage with access controls and retention matching the pilot agreement. Take
one backup immediately before and after each pilot session; periodically
exercise restoration rather than treating archive creation as proof of
recoverability.

Restore only into a fresh absolute directory, verify it, point a disposable
instance at the restored component paths, and run a smoke test before any
promotion:

```bash
/opt/voice-workflow-agent/.venv/bin/python \
  /opt/voice-workflow-agent/scripts/pilot_state_backup.py restore \
  /var/backups/voice-workflow-agent/pilot-pre-session-001.tar.gz \
  /var/lib/voice-workflow-agent-restore-001
/opt/voice-workflow-agent/.venv/bin/python \
  /opt/voice-workflow-agent/scripts/pilot_state_backup.py verify \
  /var/backups/voice-workflow-agent/pilot-pre-session-001.tar.gz

export VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR=/var/lib/voice-workflow-agent-restore-001/protocol
export VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR=/var/lib/voice-workflow-agent-restore-001/workspace
export VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB=/var/lib/voice-workflow-agent-restore-001/reports/experiment_reports.sqlite
```

The automated test suite exercises create, verify, restore, collision refusal,
privacy exclusions, a corrupt archive, and SQLite/object/evidence integrity.
No live pilot dataset was available here, so an operator must still perform a
deployment-specific restore drill before the first participant session.

## Configuration fails closed

`ServerConfigurationError`/`WorkspaceError`/`ProtocolConfigurationError`
already raise at startup or first use for invalid combinations (e.g.
workspace enabled without an absolute data directory, curated-protocol paths
partially set). Development identity is only reachable outside `operational`
scope — `VOICE_WORKFLOW_AGENT_USAGE_SCOPE=operational` requires a complete
OIDC configuration; there is no silent fallback from operational to
development identity.

## Observability

`runtime_metrics.py` records bounded, content-free route/tool/latency
aggregates. The tenant admin analytics page and
`GET /api/workspace/admin/pilot-metrics` add voice/success, clarification,
repeat, STT failure, blocked mutation, interruption, speaker ambiguity,
completed-step/workflow, duration, manual fallback, observation/evidence,
persistence failure, recovery/resume, and completion-rate counters. Durable
session/event counts are lifetime values; voice/failure counters obey the
configured analytics-retention window. Neither includes raw audio, transcripts,
identities, free text, credential values, biometric voiceprints, or model
reasoning.

Monitor at minimum:

- `/healthz` availability and `/readyz` non-200 responses;
- increases in STT/clarification/blocked-mutation/persistence-failure counts;
- ignored versus confirmed barge-in and speaker ambiguity events;
- recovery events and completion rate per controlled pilot window;
- manual fallback usage and observation/evidence capture;
- service restart frequency, disk capacity, and backup verification failures.

On a mutation failure, leave the workflow at its last server-confirmed state,
ask the researcher to refresh the timeline, and preserve the sanitized service
log plus the session identifier in the incident record. Never copy raw audio,
transcripts, bearer tokens, `.env`, evidence bytes, or internal database paths
into logs or tickets.
