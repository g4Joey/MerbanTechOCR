<div align="center">

# MerbanTechOCR

Robust, containerized OCR & document classification microservice for MerbanHub.

</div>

## ✨ Features

- FastAPI based, production ready
- Tesseract + Poppler PDF rasterization
- Intelligent filename normalization & indexing (fully / partial / failed)
- Dual processing modes: immediate (sync) or async queue
- Search, stats, health & diagnostics endpoints
- Optional synchronous one-shot endpoint (`/process-sync`)
- Cross-origin friendly (CORS * by default – can be restricted via env)

## 🧱 Architecture Overview

```
Client (Frontend / Backend) --> /api/files/upload  --> Save + (Immediate Process | Queue)
																\-> Classified to fully_indexed | partially_indexed | failed
																	  + Metadata in in-memory index
```

## 🌐 Core Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | /api/files/upload | Upload file (processes immediately or enqueues) |
| POST | /process-sync | Force synchronous inline OCR (returns extracted data) |
| GET | /api/files/list | List files (all or by status=fully|partial|failed|scan) |
| GET | /api/files/{filename} | Download stored/processed file |
| GET | /api/files/{filename}/metadata | Metadata (size, modified, directory) |
| GET | /status/{filename} | Current status for original filename |
| GET | /results/{filename} | Processed result (classification + derived fields) |
| GET | /search?q=term | Filename substring search |
| GET | /stats | Folder counts |
| GET | /health | Health probe |
| GET | /_routes | Diagnostics: enumerate registered routes |

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| HOST | 0.0.0.0 | Bind address |
| PORT | 8000 | Service port |
| PROCESS_MODE | immediate | immediate | async |
| SCAN_DIR | /app/data/incoming-scan | Incoming files |
| FULLY_INDEXED_DIR | /app/data/fully_indexed | Classified full matches |
| PARTIAL_INDEXED_DIR | /app/data/partially_indexed | Name or account only |
| FAILED_DIR | /app/data/failed | Unprocessable files |
| LOG_LEVEL | INFO | Logging verbosity |
| OCR_DPI | 300 | PDF rasterization DPI |
| TESSERACT_CMD | /usr/bin/tesseract | Tesseract binary |
| POPPLER_PATH | /usr/bin | Poppler bin path (Windows only) |
| ALLOW_ORIGINS | * | Comma list for CORS |

## 🚀 Quick Start (Local)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python start.py
```

Visit: http://localhost:8000/docs

## 🐳 Docker

```bash
docker build -t merbantech-ocr .
docker run -p 8000:8000 -e PROCESS_MODE=immediate merbantech-ocr
```

## 🔄 Processing Modes

- immediate: Process during upload request – caller receives classification in one response.
- async: Upload returns quickly; background worker handles OCR; poll `/status/{filename}` or `/results/{filename}`.

Switch via: `PROCESS_MODE=async`.

## 🧪 Health & Diagnostics

| Check | Command |
|-------|---------|
| Health | curl http://localhost:8000/health |
| Routes | curl http://localhost:8000/_routes |
| Stats | curl http://localhost:8000/stats |

## 🧬 Classification Logic (Summary)

1. Extract text (PDF → images via Poppler → Tesseract)
2. Parse candidate labels (Name, Account, ID variants)
3. Validate account patterns (>=10 digits)
4. Determine bucket:
	- name + account → fully_indexed
	- name OR account → partially_indexed
	- none → failed
5. Convert images to PDF for uniformity

## 🛡️ Production Tips

- Mount a persistent volume for /app/data/* if running on restarts.
- Restrict CORS with ALLOW_ORIGINS.
- Add API key layer (future enhancement) if exposed publicly.
- Scale horizontally only after moving in-memory indexes to a shared store (Redis/Postgres).

## 📌 Roadmap

- Persistent metadata store
- Webhook callbacks
- Pluggable NLP enrichment
- Rate limiting / API key auth

## 📄 License

MIT (adapt as needed).

---

Need help integrating with MerbanHub backend? Provide the backend’s expected callback contract and extend `/process-sync` or add webhooks.
