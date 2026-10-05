# Tablekeeper stage 1 — build and run

HTTP API only. Python 3.12, FastAPI + uvicorn (one worker), state in an in-memory
SQLite database (it does not survive a restart, which the spec allows).

## Docker (the delivered way)

Run from this folder (`stage-1/`):

    docker build -t tablekeeper-stage1 .
    docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage1

The image installs every dependency (including the `tzdata` zone database) at build
time and needs no network at run time. `GET /health` answers `{"status": "ok"}` within a
few seconds of start; the image also declares a Docker `HEALTHCHECK` on it.
`POST /_test/reset`, `GET /_test/export` and `POST /_test/import` are enabled in the image.

## Without Docker

Needs Python 3.12+ with the pinned packages installed once:

    python -m pip install -r requirements.txt

This includes `tzdata`, the IANA zone database. Windows has no system zone database,
so without it the service cannot resolve restaurant time zones: it logs an error at
start and `GET /health` answers 503 instead of 200. Check with
`python -c "import zoneinfo; zoneinfo.ZoneInfo('Europe/Berlin')"`.

Then start it from this folder (POSIX sh; works from Windows Git Bash):

    PORT=18200 sh serve.sh

`serve.sh` runs `python -m uvicorn app.main:app` on `0.0.0.0:${PORT:-8080}` with the
`python` on `PATH`. It installs nothing at start.

## Acceptance tests

From the repository root (`C:/df/band-work/result-2`), against a running service:

    BASE_URL=http://127.0.0.1:18200 C:/df/tools/.venv/Scripts/python.exe -m pytest stage-1/acceptance -q
