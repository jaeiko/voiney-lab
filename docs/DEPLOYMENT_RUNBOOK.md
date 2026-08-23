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
- `GET /readyz` — configuration readiness. Returns `503` if required
  configuration failed to parse (e.g. workspace enabled without an absolute
  data directory); returns `200` with non-secret capability booleans
  otherwise. This does **not** verify live reachability of xAI or any other
  external provider — no local check can do that without making a real,
  billable request on every probe.

## Durable state and backup

All durable pilot state is SQLite, rooted under the directories named by
`VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR`, `VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR`,
and `VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB`. There is no object-store
backend in this build — evidence/asset bytes referenced from these databases
also live under the same data directories (`objects/sha256/`), so a backup
must capture the whole tree, not just the `.sqlite` files.

**Backup** (process stopped, to guarantee a consistent snapshot — SQLite's
own WAL/journal makes a live copy of just the `.sqlite` file unsafe without
using `sqlite3 .backup`):

```bash
systemctl stop voice-workflow-agent
tar czf "backup-$(date +%Y%m%d-%H%M%S).tar.gz" -C /opt/voice-workflow-agent data
systemctl start voice-workflow-agent
```

**Restore** (into a fresh/disposable directory, verified before promoting):

```bash
mkdir -p /tmp/restore-test && tar xzf backup-*.tar.gz -C /tmp/restore-test
VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR=/tmp/restore-test/data/... \
  python -m pytest -q tests/test_experiment_protocol_store.py  # sanity check the restored catalog opens
```

This is a documented, file-copy-based procedure verified conceptually
against the existing data-directory layout; it has not been exercised
against a live pilot dataset in this environment (no such dataset exists
here) and should be dry-run before the first real pilot session.

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
aggregates. Do not add logging of secrets, raw audio, full transcripts, or
model reasoning — this is an existing, tested invariant
(`tests/test_runtime_metrics.py`, `tests/test_identity_and_workspace.py`),
not a new one introduced here.
