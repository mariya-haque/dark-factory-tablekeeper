# Stage 1 acceptance tests (tablekeeper reservations API)

Black-box pytest suite written only from the stage 1 specification. Every test cites the
ledger ids it proves (`ledger/stage-1/requirements.md`, ids `S1-Rnn`) in its docstring.
The tests talk to a running service over HTTP only and call `POST /_test/reset` before each
test, so they need a service started on its own (it is never started for you).

Run from the repository root (`C:/df/band-work/result-2`):

    BASE_URL=http://127.0.0.1:18100 C:/df/tools/.venv/Scripts/python.exe -m pytest stage-1/acceptance -q

- `BASE_URL` defaults to `http://127.0.0.1:8080` when unset.
- Needs only `pytest` and `httpx`, both in the tools venv. No time zone database is needed on
  the test host: expected UTC offsets are written out literally.
- Wall-clock "now" is used only for the cutoff rule: past starts use 2026-09-24 (already
  started), "inside the cutoff" uses restaurant `r_strict` (UTC, 14-day cutoff) three days
  from today, and everything else uses 2030 dates.
- The concurrency tests (`test_concurrency.py`, plus one each in `test_idempotency.py` and
  `test_auth.py`) send up to 50 simultaneous requests.
