# Phase 10 Demo Walkthrough — In-gel Digestion

This document maps the commercial demo flow to concrete, existing UI elements
and API routes. It exists so the demo can be rehearsed and reviewed without
guessing which control does what. It does not introduce any new capability —
every element referenced here already exists in `static/index.html` and its
backing routes in `server.py`.

It expands on the presenter's own outline in `demo_script.md` (kept as-is,
untracked, and not edited by this document) with the concrete element IDs and
API calls behind each step.

## Prerequisites

- Server started via `scripts/run_candidate_a.sh` (loads the Candidate A
  in-gel digestion fixture and the `local-admin` dev identity, which has
  researcher + reviewer + admin workspace access).
- No live external provider is required for the demo path below; source
  connectors (protocols.io / Drive / GitHub) and eLabFTW remain optional and
  contract-tested only (see `LAB_WORKFLOW_OS_IMPLEMENTATION_REPORT.md`).

## Walkthrough

1. **Upload protocol** — Researcher workspace, "새 실험 PDF 등록" (`#protocol-pdf`
   / `#protocol-upload-status`). Uploading triggers structured analysis and
   populates `#protocol-review-panel`.
2. **Review** — `#protocol-review-panel` shows the parsed sections and, if the
   source required OCR, the OCR review controls (`#protocol-ocr-run`,
   `#protocol-ocr-accept`, `#protocol-ocr-reject`). Accepting OCR
   (`#protocol-ocr-accept`, styled distinctly as `.btn-ocr-neutral`) only
   confirms the extracted text matches the source page — it is **not**
   protocol approval, and the button label and status text say so explicitly.
3. **Approve** — Reviewer workspace (`#workspace-reviewer` → `#reviewer-workspace`).
   The pending revision appears in `#reviewer-inbox`; selecting it populates
   `#reviewer-diff`. `#reviewer-approve` (styled `.btn-reviewer-approve`, the
   only green "approve" action in the product) commits the approval.
4. **Start session** — Researcher workspace, select the approved protocol in
   `#protocol-id`, then `#start`. If a session is already active,
   `#new-session-modal` confirms before ending it.
5. **Voice commands** — Once `LISTENING`, spoken turns render in `#log`.
   Current step state is mirrored in the sticky rail (`#rail-step-badge`,
   `#rail-timer-badge`) and the procedure card (`#procedure-step-title`,
   `#procedure-instruction`, `#procedure-primary`).
6. **Complete experiment** — Steps advance via server-confirmed voice
   completion only (never from model prose alone). Progress is visible in
   `#procedure-progress` and mirrored into the experiment timeline
   (`#experiment-event-timeline`), which now tags each entry with a
   `data-event-kind` (lifecycle / step / observation / evidence /
   reviewer-action / pause / resume / completion) for at-a-glance scanning.
7. **Export report** — `#procedure-report-details` lists
   Markdown/JSON/CSV/DOCX exports (`#export-report-*`). Writing the completed
   record to eLabFTW is a separate, explicit confirmation
   (`#elab-confirm` + `#elab-writeback`) — never automatic.

## Beyond the seven-step outline

These are part of the full product story and worth showing if time allows,
but are not in the presenter's short-form outline above:

- **Pause / Resume / Recovery** — `#rail-pause-session` pauses; reloading the
  page and reselecting the session in `#experiment-session-select` resumes.
  The new `#experiment-session-status-badge` reflects `ready` / `in_progress`
  / `paused` / `blocked` / `completed` / `stopped` directly from the server's
  `ExperimentSession.status`, so recovery state is visible without reading
  logs.
- **Observation / Evidence** — `#manual-observation-content` +
  `#manual-observation-save`, and `#experiment-evidence-file` +
  `#experiment-evidence-upload`. Both are explicitly `observation_only` /
  `not_interpreted` — they never rewrite the approved protocol's instructions.
- **Optional integration boundary** — `#experiment-workflow-link` attaches
  reviewed Snakemake/Nextflow metadata to the experiment. This is a metadata
  link only; the product does not execute the referenced workflow.

## What this demo does not claim

Per the Phase 12 integration classification, do not present protocols.io,
Google Drive, GitHub, OIDC, eLabFTW, or the OCR provider as live-validated —
they are contract-tested against fake transports in this repository. Present
them as configured-but-not-live-tested if a reviewer asks.
