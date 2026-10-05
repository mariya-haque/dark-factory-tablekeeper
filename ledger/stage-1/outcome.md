# Stage 1 outcome — tablekeeper (reservations API)

- Stage: 1
- Start (UTC): 2026-10-05T14:30Z (ledger request sent)
- End (UTC): 2026-10-05T16:03Z (Reviewer ACCEPT received)
- Accepted commit: 9d393a317fb2eed91ea690f1e9b8d4b2dd0031ed (Reviewer ACCEPT, review round 2)

## Work items

| Item | Owner | Requirements | Commits | Review rounds |
|---|---|---|---|---|
| SA-1 ledger + acceptance tests | Spec Auditor | S1-R01..S1-R117 | 8f99e2b5cf452cccdda7169064172e2d5556b503, fix ca58bf3b39c0a979c6995e0f1078ef9fc2359669 | (input to review) |
| BE-1 core service | Backend Engineer | S1-R01..S1-R92 | fbe121359926c5d68737d9b415c7de58b6e9cc4a | reviewed with BE-2 |
| BE-2 export/import + atomic moves | Backend Engineer | S1-R93..S1-R117 | 8def78b988b66bc47607510010e4bf931ca1284b, rework 9d393a317fb2eed91ea690f1e9b8d4b2dd0031ed | 2 (1 REJECT, 1 ACCEPT) |

## Rejections and what they changed

1. Acceptance-test defect (raised by Backend Engineer before review): test_availability.py::test_booking_removes_table_from_overlapping_slots_only expected a table free in a slot whose occupancy overlapped an existing booking, contradicting §1/§8. Spec Auditor corrected it in ca58bf3; no other test had the same mistake.
2. Reviewer REJECT of 8def78b (round 1), S1-R66: POST /_test/reset accepted seeded reservations with references "x", "lower01", "TOO-LONG-WITH-DASH" (3 shipped checks failing, 117/120). Rework 9d393a3: reset and import validate seeded references against `^[A-Z0-9]{6,12}$`; reset also rejects overlapping confirmed seeds, duplicate ids/references, unknown or mismatched user/restaurant/table, invalid times and party sizes (422, state unchanged); /health returns 503 if the zone database is missing; RUN.md documents tzdata for non-Docker runs.

## Open defects

None recorded against the submission.

## Last check summary reported by the Reviewer (round 2, clean worktree of 9d393a3)

- `docker build --no-cache` from scratch: OK; container healthy, /health {"status":"ok"}.
- Spec Auditor acceptance suite against the container: 244 passed.
- Check command (LOCAL MODE): `stage 1: pass`; `suite 1: 100% of checks passed (bar: 50%)`; `next-stage probe (suite 2): fails, as it should`; `claimed stage: 1 on the shipped checks (LOCAL MODE, no Docker: Dockerfile and offline start NOT verified)`.
- The harness's isolated mode was not verified: in round 1 its own `docker build` of the harness runner exceeded 1800 s and ended in state "error". The image build and healthy start were verified by the Reviewer's own docker build/run; offline start (`--network none`) was verified in round 1.

## Environment notes

- The harness python venv lacks tzdata; the Reviewer supplied it via PYTHONPATH from a scratch copy for LOCAL MODE runs (82 checks error otherwise). Any final check run needs tzdata importable by the harness python.
- Stray harness/docker-build processes from the Reviewer's round-1 run remain alive; stopping them was denied by the permission layer.
