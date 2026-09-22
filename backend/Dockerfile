FROM python:3.13-slim-bookworm
WORKDIR /app
COPY backend/requirements.txt backend/requirements-test.txt backend/constraints.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt \
    && useradd --uid 10001 --create-home app \
    && mkdir /data && chown app:app /data
COPY --chown=app:app backend/ ./
# Windows worktrees may contain CRLF even when Git attributes request LF.
RUN sed -i 's/\r$//' scripts/*.sh
USER app
# Exercise the documented sh entry point without reading secrets or issuing links.
RUN sh scripts/invite.sh --help >/dev/null
EXPOSE 8000
CMD ["python", "-m", "app"]
