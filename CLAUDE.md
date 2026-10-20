@AGENTS.md

# CLAUDE.md — how Claude Code works in this repository

The line above imports `AGENTS.md`, the one contract every coding agent
follows: what the code and the product may do, the module map, verification
and the documentation order. Claude Code loads an `@` import at launch
(Claude Code documentation, "Import additional files", checked 2026-10-20).
This file adds only how Claude Code itself operates here; where the two
disagree, stop and ask a person.

## Preflight

Before changing anything in a new session, run and read:

```bash
pwd
git branch --show-current
git status --short
git log --oneline --decorate -10
```

Look into any untracked file or folder you do not recognise before touching
it — it may be someone's work in progress. `docs/demo_script.md` is the known
case; it and the other files no agent touches (`.env`, `data/runtime/`, the
archives) are listed in `AGENTS.md`, "File ownership".

## Lanes and worktrees

- Work arrives as a lane brief (줄): a name, a branch a person created from
  `dev`, the files it may change and the decisions already taken. Change only
  the files the brief allows. When a decision needs a file outside the list,
  finish the change minimally and name each such file in the report (what it
  does, what it cannot change, the risk); do not merge until a person has read
  that note.
- One worker per working tree. Each lane runs in its own git worktree
  (`/home/ubuntu/vl-lane-<lane>`); do not `cd` into another lane's tree or
  the main checkout.
- Copies made for checking (a worktree at an older commit, say) go under
  `~/reports/scratch/lane-<lane>/` and are removed when the lane ends.
- The git stash is shared by every worktree: never use a bare
  `git stash`/`git stash pop`. Prefer a temporary commit.

## Heavy runs and other lanes' processes

- Several lanes run at once on one machine. Wrap every full `pytest` run in
  the shared lock: `flock ~/reports/.heavy.lock env … python -m pytest -q`
  (the flags are in `AGENTS.md`, "Verification"). A queued run can wait a long
  time; measure each commit with the tests it touches, without the lock, and
  run the full suite once at the pull request's head.
- Playwright (`tests/e2e/`) runs in CI, not locally, unless a brief says so.
- Never stop another lane's process (pytest, a benchmark, a dev server). If
  you must stop one, first check that `/proc/<pid>/cwd` is inside your own
  worktree.
- External calls (model, OCR, STT/TTS, web) only within the brief's call
  limit; count them in the report.

## Commits, pull requests and merging

Branches, commit style, the pre-PR checks and file ownership are in
`docs/GIT_WORKFLOW.md`. In short:

- Branch from `dev`, open the pull request into `dev`
  (`gh pr create --base dev`). `main` receives merges from `dev` only, made
  by a person.
- Commit each decision or phase separately, with a message that says what
  changed.
- You may merge your own pull request as a merge commit
  (`gh pr merge <branch> --merge`) only when all four hold: the failure list
  equals the one measured before the work and nothing new fails; no decision
  is left for a person (anything you stopped on and reported blocks the
  merge); only the files the brief allows changed; the base is `dev`. Fetch
  `dev` just before merging and resolve overlaps mechanically only.
  Otherwise open the pull request, do not merge, and say why at the top of
  the report.
- If a merge command is refused by a permission check, stop and report; do
  not work around it.
- Only a person opens or merges a pull request into `main`, force-pushes,
  deletes a branch (`gh pr merge --delete-branch` included), or renames or
  transfers the repository.
- No destructive shortcuts: no `git reset --hard`, `git clean -fd` or force
  push unless a person asks for that exact action.
- Commit messages and pull request bodies carry no Claude attribution (no
  `Co-Authored-By: Claude…`, `Generated with [Claude Code]`, `claude.ai/code`
  link or `Claude-Session:` line). The local `commit-msg` hook strips some of
  them from commits only; run the two `grep` checks in
  `docs/GIT_WORKFLOW.md` before `gh pr create`.
- `gh pr edit` fails on this repository (a deprecated Projects field); edit a
  body with `gh api -X PATCH repos/jaeiko/voiney-lab/pulls/<n> -F body=@<file>`.

## Reports and pull request bodies

- The lane report is `~/reports/lane_<lane>_report.md`. At the top: merged or
  not (and why not), the pull request number, tests added, and the full-suite
  count with its condition (A or B, see `AGENTS.md`) and date. Then what the
  brief asked for, what is left for a person, and anything marked "확인 안 됨".
- The pull request body is written in Korean, appended to
  `~/reports/pr_lane_<lane>.md`, so another team member can carry the work on
  from it alone. Sections, in the order `docs/GIT_WORKFLOW.md` lists: 왜
  고쳤나 · 바뀐 것(커밋별 표) · 바뀌지 않은 것 · 테스트 · 사용자 입장에서
  달라지는 것 · 알려진 한계·다음 할 일 · 담당과 핵심 함수 위치.
- Numbers only as measured, with date and condition. "Live-tested" only when
  a real external request succeeded during the work; otherwise
  "contract-tested" (`AGENTS.md`, rule 11).
