# Pilot Deployment Guide

Date: 2026-08-24  
Audience: Technical owner and laboratory pilot owner

This guide is the release checklist for one controlled, supervised pilot. The
tested topology is one FastAPI/Uvicorn process and local SQLite/object storage.
It is not a regulated production or high-availability deployment.

## 1. Define the pilot boundary

Before installation, record:

- laboratory owner and technical owner;
- the protocol PDF to be used and its SHA-256 after upload;
- participants (every participant is an experimenter; there are no roles
  since lane DI, 2026-10-08);
- test dates and evidence/report retention policy;
- whether provider-backed voice will be used;
- incident owner, abort criteria, and restore objective.

`CAPABILITY_MATRIX.md` is the current integration truth.

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
- the optional reference feature, disabled unless approved.

Start the pilot with `scripts/run_pilot.sh`, not by hand. It decides the usage
scope and the approved safety catalog itself and ignores `.env` for both:

- The usage scope is `reference_only` and the approved safety catalog is
  `data/runtime/pilot/approved_safety_catalog.sqlite`. A different value of
  `VOINEY_LAB_USAGE_SCOPE` or `VOINEY_LAB_SAFETY_CATALOG`
  exported in the shell is reported with `[WARN]` and ignored.
- The pilot does not run `operational` yet. That scope requires all OIDC
  values and refuses development identity; without an identity provider every
  `/api` and `/ws` request fails. `demo` and `test_only` are ruled out because
  every document of those scopes is demo material, which the launcher refuses.
  Once an identity provider is configured, moving the pilot to `operational`
  means changing `PILOT_USAGE_SCOPE` in the launcher and placing at least one
  approved, active `operational` document in the catalog.
- The launcher refuses to start, and `--check-only` exits 1, when that catalog
  is missing or unreadable, holds any demo document (scope `demo`/`test_only`
  or a title containing "fictional", in any approval state), or has no
  approved, active `reference_only` document. Build it as
  `APPROVED_DOCUMENT_OPERATIONS.md` §4 describes and require
  `./scripts/run_pilot.sh --check-only` to exit 0 before the session.

The launcher does not set the following, so `.env` still decides them. Check
each one before the session:

- `VOINEY_LAB_CURATED_PROTOCOL_FIXTURE`,
  `VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE` and
  `VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF` are empty; otherwise the
  Candidate A development fixture loads.
- `VOINEY_LAB_STT_DIAGNOSTICS_ENABLED` is false. When it is on, raw
  transcripts and audio are written to disk.
- Under a non-operational scope everyone is the one development identity,
  the experimenter (lane DI, 2026-10-08); a login comes before the pilot.
- The safety card filters facility SOPs by `VOINEY_LAB_FACILITY_ID`
  only when the SOP itself is in the `operational` scope, so a
  `reference_only` catalog filters none: put only this laboratory's documents
  in the pilot catalog.

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

Start the pilot with `./scripts/run_pilot.sh`, which serves on port 8080
unless `PORT` is set, and require:

```bash
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/readyz
```

`/readyz` proves local configuration parsing, including the operational
identity boundary. It does not prove external-provider reachability.

## 5. Upload the protocol and read its analysis

1. Upload the protocol PDF and note its SHA-256.
2. Wait for the automatic OCR (if any) and the automatic analysis to finish.
3. Open the start screen ("시작 전 확인 · 분석 결과 요약"): read the step
   count, the safety statements beside their Korean, "실행을 막는 사유" and
   "시작 전 알림". A blocked analysis has to be fixed at the source (a
   readable PDF, resolved safety-critical conflict); nothing on the screen
   overrides it.
4. Confirm "이 프로토콜로 시작" is offered. That press, by the experimenter at
   the bench, is the one human confirmation and is written to the ledger.

There is no approval, reviewer step or development activation (lane DI,
2026-10-08).

## 6. Prepare recoverability

Stop the process and create/verify a pre-session snapshot using the exact command
in `DEPLOYMENT_RUNBOOK.md`. Store it on approved encrypted off-host storage. Run
one restore drill into a fresh location before the first participant session.

## 7. Rehearse the bench

Use a non-hazardous or fictional workflow to verify:

- Experimenter: upload, start screen, start, current-step question,
  completion confirmation, observation/evidence, value confirmation, pause,
  refresh, and resume;
- Failure: empty speech, stale version, provider failure, missing evidence file,
  and service restart all remain non-mutating or recover to confirmed state.

## 8. Run the supervised session

- Keep a human observer available.
- Stop immediately on any abort criterion in `PILOT_READINESS_PACKAGE.md`.
- Use session identifiers—not user identifiers or transcripts—in incident notes.
- When uncertain whether a mutation committed, refresh the canonical timeline
  before repeating the command.

## 9. Close out

1. Verify final session state and report/evidence completeness.
2. Export the report formats the laboratory keeps (JSON, Markdown, CSV, DOCX).
3. Stop the service and create/verify the post-session backup.
4. Complete participant interviews and incident review.
5. Do not advance beyond a supervised pilot until the remaining gates in
   `PRODUCTIZATION_FINAL_REPORT.md` have owners and acceptance evidence.

## Operational references

- Detailed service, probe, backup, restore, monitoring, and incident commands:
  `DEPLOYMENT_RUNBOOK.md`.
- Experimenter instructions: `USER_GUIDE.md`.
- Failure recovery: `TROUBLESHOOTING_GUIDE.md`.
- KPI and participant package: `PILOT_READINESS_PACKAGE.md`.
