# Troubleshooting Guide

Date: 2026-08-24, revised 2026-10-08 (lane DI: one experimenter screen)

The safe default is always to preserve the last server-confirmed state. Do not
repeat a state-changing command until the experiment timeline shows whether the
first attempt committed. Do not repair production state by editing SQLite.

## Experimenter symptoms

| Symptom | Meaning | Safe recovery |
|---|---|---|
| Protocol list is loading or empty | Catalog may be disabled, empty, unavailable, or still loading | Wait once, refresh, then have an operator check `/readyz` and catalog configuration; do not select an invented revision |
| "이 프로토콜로 시작" is not offered | The analysis has not finished, or it carries an execution blocker ("실행을 막는 사유": unreadable page, failed source-evidence check, no executable step, safety-critical conflict) | Read the blocker on the start screen and fix it at the source (a readable PDF, a resolved conflict); nothing on the screen overrides it |
| Start screen shows "시작 전 알림" | Readiness found something that does not block (a missing value, an ambiguity, a construct without guidance) | Read it before starting; the step will read the source and ask for the value when it comes |
| “Listening” never appears | Microphone permission, audio device, WebSocket, or configuration acceptance failed | Check browser microphone permission and device, reload once, then check service health; no experiment step changed |
| Speech rejected / language uncertain | Empty, non-speech, or language-inconsistent transcript admission | Speak again clearly; first verify the current step if the command could mutate state |
| Stale/conflict message | Another tab, voice turn, or user changed the session version | Reload the timeline and explicitly reselect the experiment; never overwrite the newer state |
| Voice-turn processing failed | STT/model/TTS or bounded server processing failed | Assume no mutation unless the canonical timeline shows one; check current step and retry only after recovery |
| Resume disclosure says items were not restored | Expected recovery boundary | Reconfirm any pending completion, restart timers intentionally, and ask again for prior conversational context if needed |
| Evidence download fails | File missing, changed, linked, unauthorized, or hash/size-invalid | Preserve the timeline record, contact the operator, and restore/reattach only through an approved incident process |

## Operator symptoms

| Symptom | Meaning | Safe recovery |
|---|---|---|
| `/readyz` returns 503 | Required local configuration did not parse | Read the non-secret exception class, inspect service configuration locally, correct it, and restart; do not add secrets to incident logs |
| Operational identity configuration invalid | OIDC issuer/audience/JWKS is incomplete or malformed | Configure all OIDC values with HTTPS metadata; operational mode must not fall back to development identity |
| OCR never finishes | The injected OCR provider is missing or failed | Check the provider configuration; the protocol stays blocked (`source_page_requires_ocr`) until the pages are read |

The reviewer and administrator screens were removed on 2026-10-08 (lane DI);
there is no inbox, approval, connector or pilot-metrics symptom any more.

## Service and storage

### Process unavailable

1. Check the service manager and sanitized logs.
2. Confirm disk capacity and permissions on protocol/workspace/report paths.
3. Restart only after local configuration is corrected.
4. Require `/healthz` and `/readyz` success.
5. Have researchers reload and use the recovery disclosure; do not infer that an
   in-flight command committed.

### Backup fails

- Relative/root path: provide exact absolute component paths.
- Existing output: choose a new archive name; the tool intentionally refuses
  overwrite.
- Symlink in source: remove the indirection through an approved storage change;
  the backup tool refuses it.
- SQLite quick-check failure: stop the service, preserve the files, and escalate;
  do not create a misleading “successful” archive.
- Verify/checksum failure: quarantine the archive and create a new stopped-
  process backup. Never restore it.

### Restore fails

The destination must be an absolute path that does not exist. Verify the archive
first, restore into a fresh directory, use a disposable service instance, and
smoke-test before any promotion. A successful extraction without manifest,
checksum, and SQLite verification is not a successful restore.

## External provider failures

- Keep the workflow at its server-confirmed checkpoint.
- External source/web content is supplementary and cannot override approved
  protocol or safety evidence.
- Use fake-backed automated tests for diagnosis. A real provider test must be
  explicit, bounded, credential-authorized, and must not print credentials,
  proprietary documents, or full prompts.
- Classify configuration checks, contract tests, connectivity, and end-to-end
  provider success separately.

## Incident information to collect

Collect timestamp, session ID, visible step/status, action attempted, response
code or safe UI message, whether the timeline changed, browser/service version,
and reproducible steps. Do not collect raw audio, full transcripts, bearer
tokens, `.env`, credentials, model reasoning, evidence bytes, private protocol
text, or user identifiers in ordinary logs/tickets.
