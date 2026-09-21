FROM python:3.13-slim-bookworm
WORKDIR /app
COPY backend/requirements.txt backend/requirements-test.txt backend/constraints.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt \
    && useradd --uid 10001 --create-home app \
    && mkdir /data && chown app:app /data
COPY --chown=app:app backend/ ./
USER app
EXPOSE 8000
CMD ["python", "-m", "app"]
