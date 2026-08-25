# Pilot Deployment Guide

Date: 2026-08-24  
Audience: Technical owner and laboratory pilot owner

This guide is the release checklist for one controlled, supervised pilot. The
tested topology is one FastAPI/Uvicorn process and local SQLite/object storage.
It is not a regulated production or high-availability deployment.

## 1. Define the pilot boundary

Before installation, record:

- laboratory owner and technical owner;
- approved protocol and exact revision;
- participants and assigned roles;
- test dates, analytics-retention period, and evidence/report retention policy;
- whether provider-backed voice will be used;
- whether any external connector or eLabFTW write-back is in scope;
- incident owner, abort criteria, and restore objective.

Do not include a connector in scope merely because its adapter is contract-
tested. `CAPABILITY_MATRIX.md` is the current integration truth.

## 2. Prepare the host

Use Python 3.12+, a dedicated non-root service account, an HTTPS reverse proxy
for non-local access, organization-managed secrets, and absolute data paths on
appropriately protected storage.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
cp .env.example .env
```

Never commit `.env`, credentials, private protocols, evidence, raw audio,
transcripts, user identifiers, or runtime databases.

## 3. Configure deliberately

At minimum, review the configuration groups in `.env.example`:

- usage scope and approved safety catalog;
- protocol, workspace, and report storage;
- OIDC identity for operational scope;
- xAI credential and Cascade voice options;
- analytics retention;
- connector credential-name mapping and allowed scopes;
- optional reference/visual features, disabled unless approved.

For anything beyond an isolated internal demo, set
`VOICE_WORKFLOW_AGENT_USAGE_SCOPE=operational` and configure all OIDC values.
Operational mode refuses development identity fallback.

## 4. Verify the exact release

Run from the repository root:

```bash
source .venv/bin/activate
python scripts/replay_turns.py
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
npx playwright test
```

If the externally licensed Candidate A PDF is unavailable, read the explicit
test skips and use the CI empty-catalog browser launcher. Never substitute an
unverified PDF to force an integrity test to pass.

Start the candidate process and require:

```bash
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
```

`/readyz` proves local configuration parsing, including the operational
identity boundary. It does not prove external-provider reachability.

## 5. Seed and approve the protocol

1. Import the exact source and verify its source hash/version.
2. Complete extraction/OCR review and structured analysis.
3. Resolve missing values, unsupported constructs, warnings, and hazards.
4. Have an authorized reviewer approve the exact revision.
5. Confirm the Researcher workspace displays the intended approval context.

A development activation is not an operational approval and is unavailable in
operational scope.

## 6. Prepare recoverability

Stop the process and create/verify a pre-session snapshot using the exact command
in `DEPLOYMENT_RUNBOOK.md`. Store it on approved encrypted off-host storage. Run
one restore drill into a fresh location before the first participant session.

## 7. Rehearse all roles

Use a non-hazardous or fictional workflow to verify:

- Researcher: start, current-step question, completion confirmation,
  observation/evidence, pause, refresh, and resume;
- Reviewer: inbox, impact summary, technical diff, request revision, approval,
  and revocation consequences;
- Administrator: membership, permissions, connector check-before-enable,
  activity view, retention, and pilot metrics;
- Failure: empty speech, stale version, provider failure, missing evidence file,
  and service restart all remain non-mutating or recover to confirmed state.

## 8. Run the supervised session

- Keep a human observer available.
- Record the starting pilot-metrics snapshot and retention period.
- Stop immediately on any abort criterion in `PILOT_READINESS_PACKAGE.md`.
- Use session identifiers—not user identifiers or transcripts—in incident notes.
- When uncertain whether a mutation committed, refresh the canonical timeline
  before repeating the command.

## 9. Close out

1. Verify final session state and report/evidence completeness.
2. Capture the ending pilot-metrics snapshot.
3. Export only the approved report formats and perform any explicitly confirmed
   ELN write-back.
4. Stop the service and create/verify the post-session backup.
5. Complete participant interviews and incident review.
6. Do not advance beyond a supervised pilot until the remaining gates in
   `PRODUCTIZATION_FINAL_REPORT.md` have owners and acceptance evidence.

## Operational references

- Detailed service, probe, backup, restore, monitoring, and incident commands:
  `DEPLOYMENT_RUNBOOK.md`.
- Role instructions: `USER_GUIDE.md`.
- Failure recovery: `TROUBLESHOOTING_GUIDE.md`.
- KPI and participant package: `PILOT_READINESS_PACKAGE.md`.
