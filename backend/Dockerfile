FROM python:3.12-slim-bookworm
WORKDIR /app

# GigaAM decodes browser WebM/Ogg recordings through ffmpeg.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home app \
    && mkdir /data /opt/asr-model \
    && chown app:app /data

# ASR is an immutable part of the image. If Hugging Face/model download is
# unavailable, docker build fails before any participant can reach M4.
ARG GIGAAM_REPO=ai-sage/GigaAM-v3
ARG GIGAAM_REVISION=e2e_rnnt
RUN GIGAAM_REPO="$GIGAAM_REPO" GIGAAM_REVISION="$GIGAAM_REVISION" python - <<'PY'
import os
from pathlib import Path
from huggingface_hub import snapshot_download

repo = os.environ.get("GIGAAM_REPO", "ai-sage/GigaAM-v3")
revision = os.environ.get("GIGAAM_REVISION", "e2e_rnnt")
target = Path("/opt/asr-model")
snapshot_download(repo_id=repo, revision=revision, local_dir=target)
required = ("config.json", "modeling_gigaam.py", "pytorch_model.bin", "tokenizer.model")
missing = [name for name in required if not (target / name).is_file()]
if missing:
    raise SystemExit(f"Incomplete GigaAM snapshot: {missing}")
cache = target / ".cache"
if cache.exists():
    import shutil
    shutil.rmtree(cache)
PY
# Validate the downloaded custom modeling code and its declared runtime imports
# while the image is still being built. This catches missing optional GigaAM
# dependencies before a participant reaches M4 without loading the model weights.
RUN HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python - <<'PY'
from transformers import AutoConfig

AutoConfig.from_pretrained(
    "/opt/asr-model",
    trust_remote_code=True,
    local_files_only=True,
)
print("GigaAM custom modeling dependencies: OK")
PY
RUN rm -rf /root/.cache/huggingface \
    && chmod -R a=rX /opt/asr-model

COPY --chown=app:app backend/ ./
# Windows worktrees may contain CRLF even when Git attributes request LF.
RUN sed -i 's/\r$//' scripts/*.sh
USER app
RUN sh scripts/invite.sh --help >/dev/null
EXPOSE 8000
CMD ["python", "-m", "app"]
