# Voice Workflow Agent

**Reviewed protocol source → hands-free bench execution → auditable experiment record → integration.**

Voice Workflow Agent is a voice-first laboratory protocol knowledge and execution
layer. It preserves the authority of reviewed source documents while helping a
researcher work hands-free at the bench, record observations and timers, ask
bounded learning questions, and produce a source-linked experiment record.

This repository is on
`refactor/voice-workflow-agent-stability`. The flagship application is in
[`voice-workflow-agent`](voice-workflow-agent/README.md); course material remains
in the surrounding repository but is not the product runtime.

## Product scope

The controlled-pilot build provides:

- Cascade voice interaction with VAD, interruption, pause/resume, and stale-turn
  cancellation;
- Korean/English/AUTO input preferences and a fail-closed language-consistency
  gate before workflow mutation;
- immutable PDF onboarding with asynchronous analysis, evidence review,
  readiness gates, explicit development activation, approval, and revocation;
- tenant-scoped protocol lineage, revisions, translations, review history,
  source inboxes, lab knowledge, and lightweight asset-location metadata;
- read-only protocol source connectors for protocols.io, Google Drive/Shared
  Drives, and GitHub;
- metadata-only Snakemake and Nextflow registration, linked to exact wet-lab
  sessions without executing imported code;
- controlled, confirmed eLabFTW experiment write-back;
- OIDC-compatible identity, central RBAC, tenant ownership checks, and
  privacy-safe persistent pilot analytics; and
- separate researcher, reviewer, and lab-admin workspaces.

It is deliberately not a full ELN/LIMS, an arbitrary code runner, an autonomous
scientist, an autonomous protocol approver, or a safety authority. Parsing a
document never makes it operationally approved.

## System shape

```text
Protocol source
  ├─ local immutable PDF
  ├─ protocols.io exact DOI/version
  ├─ Google Drive / Shared Drive file + change cursor
  └─ GitHub repository + commit + path
          ↓
tenant-scoped lineage → draft revision → diff/evidence review
          ↓
human decision → executable development draft or approved revision
          ↓
Cascade voice session → deterministic workflow mutations
          ↓
append-only experiment record → confirmed eLabFTW write-back
```

## Quick start

Python 3.12 or newer is required.

```bash
git switch refactor/voice-workflow-agent-stability
cd voice-workflow-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env
```

Configure only server-owned values in `.env`, then run:

```bash
uvicorn voice_workflow_agent.server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. A secure context is required for microphone use
outside localhost.

The detailed setup, environment contract, API surface, connector configuration,
security model, and limitations are in the
[`voice-workflow-agent` README](voice-workflow-agent/README.md).

## Replay and verification

After the editable install, the A–G production-intent replay is package-native:

```bash
voice-workflow-replay
# Equivalent:
python -m voice_workflow_agent.replay_turns
```

Run the offline verification suite from `voice-workflow-agent/`:

```bash
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
```

Automated tests use local fakes and do not require provider credentials. Never
commit `.env`, API keys, access tokens, private protocol files, runtime SQLite
databases, or microphone recordings.

## Commercialization evidence

- [Pass 2 commercialization report](voice-workflow-agent/docs/CODEX_COMMERCIALIZATION_PASS2_REPORT.md)
- [Voice field evaluation plan](voice-workflow-agent/docs/VOICE_FIELD_EVALUATION_PLAN.md)
- [Controlled-commercialization audit](voice-workflow-agent/docs/CODEX_COMMERCIALIZATION_AUDIT.md)
- [Prior commercialization report](voice-workflow-agent/docs/CODEX_FINAL_COMMERCIALIZATION_REPORT.md)

Historical phase and course documents are retained for traceability. Where they
conflict with the current README files or executable code, the current code and
Pass 2 report are authoritative.
