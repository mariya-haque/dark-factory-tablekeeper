#!/bin/sh
# Single start command: runs the API on 0.0.0.0:${PORT:-8080} with the python on PATH.
set -e
cd "$(dirname "$0")"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1 --no-access-log
