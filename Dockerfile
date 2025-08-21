FROM python:3.13.4-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    PROCESS_MODE=immediate \
    SCAN_DIR=/app/data/incoming-scan \
    FULLY_INDEXED_DIR=/app/data/fully_indexed \
    PARTIAL_INDEXED_DIR=/app/data/partially_indexed \
    FAILED_DIR=/app/data/failed \
    LOG_LEVEL=INFO \
    OCR_DPI=300 \
    SNAPSHOT_INTERVAL=30 \
    API_KEY="" \
    TESSERACT_CMD=/usr/bin/tesseract \
    POPPLER_PATH=/usr/bin \
    ALLOW_ORIGINS=*

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng poppler-utils curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Pre-create data dirs
RUN mkdir -p /app/data/incoming-scan /app/data/fully_indexed /app/data/partially_indexed /app/data/failed /app/data/state

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD curl -fsS http://localhost:${PORT}/health || exit 1

CMD ["python", "start.py"]
