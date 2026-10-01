FROM python:3.12-slim-bookworm
WORKDIR /app

# M4 ASR runs through RouterAI, so the backend no longer embeds Torch or model weights.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home app \
    && mkdir /data \
    && chown app:app /data

COPY --chown=app:app backend/ ./
# Windows worktrees may contain CRLF even when Git attributes request LF.
RUN sed -i 's/\r$//' scripts/*.sh
USER app
RUN sh scripts/invite.sh --help >/dev/null
EXPOSE 8000
CMD ["python", "-m", "app"]
