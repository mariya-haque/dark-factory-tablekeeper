FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /srv

# All runtime dependencies (including the tzdata zone database) are installed at
# build time; the container needs no network at run time.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import zoneinfo; zoneinfo.ZoneInfo('Europe/Berlin'); zoneinfo.ZoneInfo('America/New_York')" \
    && useradd --system --no-create-home tablekeeper

COPY serve.sh ./
COPY app ./app

USER tablekeeper
EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8080'), timeout=2)"

CMD ["sh", "serve.sh"]
