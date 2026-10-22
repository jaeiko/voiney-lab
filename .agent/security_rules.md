# Security, privacy and safety rules

Rules every change must keep. Revised 2026-10-20 (lane DOC4) against dev
`e7c31c8`. The controls below are for a supervised pilot; they are not
production identity and access management.

## 1. Safety and knowledge boundary

1. **Values and safety come from the source or a person.** Amounts, times,
   temperatures, concentrations and safety statements come only from the
   protocol's source (passing the source-evidence check) or from a value a
   person confirmed. Shown provenance: "원문", "AI 제안", "검토자 입력"
   (`protocol_catalog.REVIEW_ORIGIN_KO`).
2. A term the source leaves unexplained may get a short general explanation,
   labelled "AI 일반 지식" on screen and said as "PDF에는 따로 설명이 없어요.
   일반적으로는 …"; the server drops one that carries numbers, methods,
   quantities or safety words (`answer_checks.outside_pdf_violations`).
3. **Safety precautions and completion criteria are never written by the
   AI**, and a reviewer cannot rewrite a safety statement
   (`protocol_catalog._FIXABLE_KINDS` has no `warning`). A model answer's
   safety instruction without a source is removed and replaced by "이 상황의
   안전 안내는 원문에 없어요." (`llm_router.without_ungrounded_safety`).
4. Web explanations, drawn diagrams and supplemental model text are
   reference context only; they never override the protocol's safety
   statements or the facility's approved safety documents (the SOP corpus).
5. **Emergency precedence.** An emergency phrase (fire, gas, injury, "도와주세요")
   gets a fixed reply: stop, step away from the hazard, contact the lab's
   emergency channel (`emergency.recognize_emergency`). The product makes no
   safety decision and never authorises resuming work.

## 2. Workflow authority

1. A protocol starts only when the experimenter presses the start wizard's
   last button; a step completes only on the experimenter's confirmation;
   model output only proposes a change, which the server validates and the
   one state machine carries out (`AGENTS.md`, rules 2–4).
2. There is no approval gate. An administrator's "확인함" on a notebook or a
   review version is a record and blocks nothing.
3. No autonomous restart: model or voice output never lifts a pause or
   resumes a blocked experiment on its own.

## 3. Secrets

1. API keys live only in `.env` (or the process environment): provider keys
   such as `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ELEVENLABS_API_KEY`,
   `VOINEY_LAB_GOOGLE_SPEECH_API_KEY`, the OCR credentials, and
   `VOINEY_LAB_ADMIN_TOKEN`. Never log them, render them in the browser, put
   them in URLs, metric labels, exception text or browser storage, or commit
   them. All provider calls are made server-side.
2. Passwords are scrypt hashes; session and invite tokens are stored as
   SHA-256 hashes (`accounts.py`). Invite tokens are removed from the access
   log line, `/api/auth/*` validation errors do not echo the input, and a
   failed login's name is kept only as a short hash for the 15-minute lock
   window.
3. The admin token is compared with constant-time digests
   (`hmac.compare_digest`). The admin metrics page keeps a typed token in the
   page's memory only and sends it as a header; an admin account needs none
   (`static/admin-metrics.html`).
4. Tests run with provider keys removed and outside connections blocked
   (`tests/conftest.py`).

## 4. Research data and privacy

1. Do not send proprietary formulas, unpublished data or personal identifiers
   to third parties beyond what the configured roles need. What leaves the
   machine, per role, is listed in `docs/FEATURES.md` F18; each provider's
   retention terms are checked by a person before a pilot.
2. Raw audio is not kept. STT diagnostic recordings are opt-in
   (`VOINEY_LAB_STT_DIAGNOSTICS_ENABLED`), capped, ignored by git and not for
   private lab work without an approved policy.
3. Operational aggregates (`/api/admin/metrics`, pilot metrics) hold
   numbers, codes and times only — no transcripts, free text, protocol
   titles, prompts or model reasoning. The one free text is the optional line
   under the end-of-run rating, which never enters a metric.
4. Deletion: `scripts/pilot_data_delete.py` (preview, confirm word, delete,
   log) and an account's own deletion remove the same scope; shared protocols
   stay. Neither wipes disk blocks.

## 5. Network boundaries

1. **External images**: never hotlink. Display needs a free licence, HTTPS,
   size, type and dimension checks and same-origin serving
   (`web_explanations.py`); without that, only a cited link.
2. **Link import**: only `drive.google.com`, `docs.google.com`,
   `drive.usercontent.google.com` (plus Google's export hosts on redirect),
   at most 5 redirects, every resolved and connected address public, no
   cookies, auth or proxy settings carried, the same size limits as upload,
   format judged from the bytes (`protocol_link_import.py`).
3. **Accounts**: invite-only sign-up; HttpOnly, SameSite=Lax cookie, Secure
   over HTTPS; login lock after 5 failures in 15 minutes per name and per
   client (the client is the first `X-Forwarded-For` value when present, so
   this holds only behind a proxy that sets it). App origins in
   `VOINEY_LAB_APP_ORIGINS` may use a bearer token.
4. Use parameterised SQL and validate external data at ingress.

## 6. Audit trail

The experiment record (`experiment_report_events`) and the workspace session
events (`experiment_session_events`) are append-only. The workspace enforces
it with SQL triggers; the experiment record enforces it in code (events keyed
and inserted once, no update or delete path). Corrections and reverts are new
events; nothing recorded is rewritten. The only deletion path is the data
deletion above, which logs what it removed.
