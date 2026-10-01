FROM python:3.12-slim

ARG APP_BUILD_ID=unverified
ARG APP_BUILD_COMMIT=unknown
ARG APP_BUILD_TIME=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CODEX_HOME=/data/codex \
    HF_HOME=/data/embeddings/huggingface \
    EMBEDDING_CACHE_DIR=/data/embeddings \
    UPLOAD_DIR=/data/uploads \
    APP_BUILD_ID=${APP_BUILD_ID} \
    APP_BUILD_COMMIT=${APP_BUILD_COMMIT} \
    APP_BUILD_TIME=${APP_BUILD_TIME}

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libseccomp2 poppler-utils tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /data/codex /data/embeddings /data/uploads \
    && chown -R app:app /data

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .
USER app

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
