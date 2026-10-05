@mariya25689/coordinator — FACTORY RUN. This message is the only human input for the entire run.
Do not ask me anything, do not wait for me, and do not request approval at any point.
Resolve every choice from this task, the specifications and the repository. If something
blocks you, record it with evidence in the stage outcome and continue.

## Job

Track: tablekeeper
Build stages 1, 2, 3, 4 in order. Each stage is reviewed and closed before the next begins.
Earlier stages matter most: a stage folder counts only if every earlier folder counts, so
never start stage N+1 without a Reviewer ACCEPT for stage N, and never trade an earlier
stage's correctness for progress on a later one.

## Paths (absolute — use exactly these; the repository path has no spaces)

| What | Path |
|---|---|
| Result repository — commit here and nowhere else | `C:/df/band-work/result-2` |
| Stage folders | `C:/df/band-work/result-2/stage-1` … `C:/df/band-work/result-2/stage-4` |
| Ledger location (requirements + stage outcome) | `C:/df/band-work/result-2/ledger/stage-N/` |
| Acceptance directory (spec auditor tests, screenshots) | `C:/df/band-work/result-2/stage-N/acceptance/` |
| Specifications (read-only) | `C:/df/dark-factory-wearedevs/tablekeeper/spec/stage-N.md` |
| Scratch for review checkouts | `C:/df/band-work/scratch/result-2/` |
| Python for acceptance tests and screenshots (pytest, httpx, playwright installed) | `C:/df/tools/.venv/Scripts/python.exe` |

The coordinator reads each specification file and pastes its **complete text** into
every handoff that needs it (numbered parts if long). Other seats work from the text
they are sent.

**Nobody opens `C:/df/dark-factory-wearedevs/tablekeeper/test/` or the harness source.**
Only the Reviewer runs the check command below, and reports failures as behaviour that
contradicts a requirement. The shipped checks are a small, partial sample; the full
graded suite is hidden and is written entirely from the specification. Passing the
shipped checks is not done — satisfying the ledger is.

## Stack decision (all stages)

- Python 3.12, FastAPI + uvicorn, **one** worker process. Base image `python:3.12-slim`.
  Pin dependencies in `requirements.txt`; install at image build. Include the `tzdata`
  package so IANA zones work in the slim image.
- Storage: stdlib `sqlite3`. Every write that checks-then-acts runs in one
  `BEGIN IMMEDIATE` transaction; idempotency outcomes stored in the same transaction.
  State need not survive a restart.
- Listen on `0.0.0.0:$PORT`, default 8080. Healthy well within 60 s of start.
- Runtime: 2 vCPU, 2 GiB, up to 50 concurrent requests, 5 s per request, **no outbound
  network**. Every font, script and stylesheet is vendored in the image — no CDNs.
- Browser UI (from stage 2): plain HTML, CSS and JavaScript served by the same app; no
  build step, no framework, system font stack. Keep one shared stylesheet as the visual
  system. Warm, confident, presentation-ready — the specification's product direction is
  a requirement.
- Each stage folder contains: source, `requirements.txt`, `Dockerfile`, `RUN.md`
  (exact build + run commands), `serve.sh`, and `acceptance/` with a `README` line giving
  the one command that runs its tests against `BASE_URL`.
- `serve.sh` is the single start command: POSIX `sh`, LF line endings, it `exec`s the server
  on `0.0.0.0:${PORT:-8080}` using whatever `python` is on PATH, and runs from the stage
  folder. The Dockerfile's `CMD` runs `sh serve.sh`. The check command also starts the
  service with it directly when Docker is unavailable on this machine, so it must work on
  Windows Git Bash too (no Linux-only paths, no `sudo`, no package installs at start).

## Ports for local testing (each seat stays in its own range)

| Seat | Ports |
|---|---|
| Spec Auditor | 18100–18199 |
| Backend Engineer | 18200–18299 |
| Frontend Engineer | 18300–18399 |
| Reviewer | 18400–18499 |

Port 8080 is the service default inside the container only; do not use it for local tests.
The check command picks its own free ports.

## Stage folder rules

- Stage 1 starts empty in `stage-1/`.
- Each later stage starts by copying the accepted previous folder to the new folder,
  deleting any nested `.git`, and committing that copy unchanged. Then widen the copy.
- A folder holds the solution to its own stage only and must keep passing every earlier
  stage. Never copy a later stage back into an earlier folder.

## Git

- Commit as your own seat, so history shows who did what:
  `git -c user.name="<Seat Name>" -c user.email="<seat-name-lowercase-hyphenated>.factory@invalid.example" commit -m "<work item>: <what> (<requirement ids>)"`
- `git add <your paths>` only. No amend, rebase, squash, reset or force. No branches
  other than `main`. Commit early and often; every handoff names a full commit id.
- Never commit credentials, `.env` files, virtualenvs or `node_modules`.

## Check command (the Reviewer runs it; always isolated mode, always a new output dir)

From Git Bash:

```
bash C:/df/kit/check.sh tablekeeper <stage number> <path to a clean checkout of the result repository>
```

It builds `stage-N/` from that checkout, runs suites 1..N plus the next suite as an
over-reach probe, and prints `claimed stage: N` when the folder claims its stage. The
next-stage line is expected to FAIL. If this machine's Docker engine is unavailable it
prints a WARNING and runs the same suites against the service started with `serve.sh`
(LOCAL MODE); then the reviewer must also read the Dockerfile and RUN.md line by line
and record in the verdict that the image build was not verified locally. Use a git worktree under the scratch path for the
clean checkout. `bash C:/df/kit/check.sh tablekeeper all <path>` checks every folder.

## Definition of done for a stage

The Reviewer returns ACCEPT on one full commit id where: the image builds from scratch and
becomes healthy; the spec auditor's acceptance tests for this and all earlier stages
pass; the check command prints `claimed stage: N`; (stage 2 and later) every screen was
viewed at 375 px and 1280 px with screenshots saved under `acceptance/screenshots/`.
Then the coordinator commits `ledger/stage-N/outcome.md` and moves to the next stage.

## Final report

After the last stage, the Coordinator posts the final report to every seat and stops.

Handles in this message: only the Coordinator is mentioned. Seats address each other with
the handles in their own instructions; when pasting this task into a handoff, keep it free of
other seats' handles.
