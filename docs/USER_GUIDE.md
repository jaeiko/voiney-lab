# User Guide — Controlled Pilot

Date: 2026-08-24

This guide covers the researcher, reviewer, and laboratory administrator
workspaces. It does not replace a laboratory SOP, safety training, or emergency
procedure. Stop and follow facility policy whenever the approved protocol and
the physical situation differ.

## Researcher

### Start an experiment

1. Open the Researcher workspace and wait for the protocol list to finish
   loading.
2. Select the approved protocol. Before starting, verify the context card shows
   the expected experiment, exact version, approval state/reviewer, current step,
   and available actions.
3. If the selected revision is not approved or is no longer available for new
   sessions, do not work around the block; contact a reviewer.
4. Select **Start new experiment**. If an open experiment is explicitly selected,
   the action changes to **Resume experiment** and shows what will and will not be
   restored.
5. Start the voice session. The visible voice states mean Ready, Listening,
   Understanding request, and Providing guidance.

### Work hands-free

Useful commands include:

- “Start the protocol.”
- “What is the current step?”
- “Why do I do this step?”
- “What are the warnings?”
- “Start the step timer.”
- “I completed this step.”
- “Pause the workflow.”

A completion request may ask for explicit confirmation. Confirm only after the
physical work is actually complete. Explanations, warnings, protocol audits,
history, and previews must not change the step.

If recognition is empty, non-speech, language-inconsistent, or ambiguous, the
system keeps the current step and asks for another request. Check the current
step before retrying any state-changing command.

### Record observations and evidence

- Add a voice or manual observation to the current step. It is labeled
  observation-only and cannot modify the approved instructions.
- Attach a JPEG, PNG, WebP, PDF, or DOCX up to 32 MiB. It is stored as
  not-interpreted evidence.
- Use **Download original evidence** in the timeline when authorized. A missing,
  changed, or invalid stored object is refused rather than returned unchecked.

### Pause, refresh, and resume

1. Pause before intentionally leaving the bench session.
2. After a refresh or reconnect, select the exact open experiment.
3. Read the recovery disclosure. The server restores the protocol/revision,
   contiguous completed steps, and current step.
4. Pending confirmations, prior conversation, and active timers are not restored.
   Re-establish those intentionally if needed.
5. If the UI reports stale state, refresh the timeline and reselect the session;
   do not assume the last command succeeded.

## Reviewer

### Review a request

1. Open the Reviewer workspace and select a pending request.
2. Confirm the protocol name/version, requester, request reason, and immutable
   source identity.
3. Read **What changed**, **Why**, **Experimental impact**, and **Risk** before the
   technical diff.
4. Treat unknown or missing risk evidence as unknown. Do not infer that absence
   of a warning means low risk.
5. For OCR material, remember that accepting extracted page text is not protocol
   approval. Structured review and approval remain separate.

### Decide

- **Approve** makes the exact revision available according to its operational
  gates.
- **Request revision** rejects this immutable revision and requires a new one.
- **Disable future use** revokes the revision for new sessions while preserving
  existing experiment history.

Select only an action offered by the server, enter a meaningful comment, review
the consequence panel, and confirm once. If another reviewer acted first, the
stale decision is rejected; reload the packet instead of overwriting it.

### Audit

The history shows actor, timestamp, decision, comment, and affected version. It
is append-only. Do not rely on an exported screenshot as a replacement for the
canonical server record.

## Laboratory administrator

### Manage users and permissions

1. Open the Administrator workspace.
2. Review Account Identifier, User Identity, Permission Level, and effective
   allowed actions.
3. Assign only the fixed role needed for the pilot. The system supports
   researcher, reviewer, lab administrator, and organization administrator—not
   custom roles.
4. Recheck the security activity view after changes. An administrator cannot
   deactivate their own administrative access through the protected path.

### Configure an external connection

Follow the enforced sequence:

1. Select the integration.
2. Select its server-provisioned secure credential handle.
3. Enter the narrowest allowed scope.
4. Run **Check configuration**.
5. Enable only after the status is ready.

The check verifies server credential availability and scope syntax. It does not
contact the provider and must not be described as a successful login or live
connection. A newly created or migrated connection stays disabled/untested until
checked.

### Monitor a pilot

- Review completed workflows, failed commands, recovery events, state-change
  failures, user actions, and completion rate.
- Record the analytics-retention duration with every metric snapshot. Durable
  session counts and retention-bounded action/failure counts have different
  windows.
- Review connection posture, access activity, and failures without looking for
  secrets—the browser never receives credential values or references.

## End a pilot session

1. Confirm the final experiment status and timeline.
2. Export the required report format. CSV is available for structured transfer;
   JSON/Markdown/DOCX are also supported.
3. If eLabFTW write-back is part of the approved pilot, verify the session and
   report are completed/matching, review the payload boundary, and explicitly
   confirm the idempotent transfer.
4. Ask the operator to create and verify the post-session backup.
5. Record issues using the incident template in `PILOT_READINESS_PACKAGE.md`.
