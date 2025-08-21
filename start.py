#!/usr/bin/env python3
"""Startup script selecting mode + loading snapshot."""
import os, json, logging, time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger("start")

DATA_DIR = Path(os.getenv("SCAN_DIR", "/app/data/incoming-scan")).parent
STATE_DIR = DATA_DIR / "state"
SNAPSHOT_FILE = STATE_DIR / "jobs_snapshot.json"

for d in [os.getenv('SCAN_DIR'), os.getenv('FULLY_INDEXED_DIR'), os.getenv('PARTIAL_INDEXED_DIR'), os.getenv('FAILED_DIR'), STATE_DIR]:
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)

os.environ.setdefault("SNAPSHOT_PATH", str(SNAPSHOT_FILE))

if __name__ == "__main__":
    log.info("Launching MerbanTechOCR (mode=%s)" % os.getenv("PROCESS_MODE", "immediate"))
    try:
        import service
        service.start()
    except Exception as e:
        log.exception("Fatal startup error: %s", e)
        raise
