import os, re, threading, time, json, logging, shutil
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn

try:
    import pytesseract
    from pdf2image import convert_from_path
    from PIL import Image
except Exception as e:
    raise RuntimeError("Missing OCR dependencies: %s" % e)

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
class Cfg:
    HOST = os.getenv('HOST', '0.0.0.0')
    PORT = int(os.getenv('PORT', '8000'))
    PROCESS_MODE = os.getenv('PROCESS_MODE', 'immediate')  # immediate | async
    SCAN_DIR = Path(os.getenv('SCAN_DIR', '/app/data/incoming-scan'))
    FULL = Path(os.getenv('FULLY_INDEXED_DIR', '/app/data/fully_indexed'))
    PART = Path(os.getenv('PARTIAL_INDEXED_DIR', '/app/data/partially_indexed'))
    FAIL = Path(os.getenv('FAILED_DIR', '/app/data/failed'))
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    DPI = int(os.getenv('OCR_DPI', '300'))
    SNAPSHOT_INTERVAL = int(os.getenv('SNAPSHOT_INTERVAL', '30'))
    SNAPSHOT_PATH = Path(os.getenv('SNAPSHOT_PATH', '/app/data/state/jobs_snapshot.json'))
    API_KEY = os.getenv('API_KEY', '')
    ALLOW_ORIGINS = [o.strip() for o in os.getenv('ALLOW_ORIGINS', '*').split(',') if o.strip()]
    TESSERACT_CMD = os.getenv('TESSERACT_CMD', '/usr/bin/tesseract')
    POPPLER_PATH = os.getenv('POPPLER_PATH', '/usr/bin')

pytesseract.pytesseract.tesseract_cmd = Cfg.TESSERACT_CMD
for d in [Cfg.SCAN_DIR, Cfg.FULL, Cfg.PART, Cfg.FAIL, Cfg.SNAPSHOT_PATH.parent]:
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=getattr(logging, Cfg.LOG_LEVEL, logging.INFO), format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger('ocr-service')

# ----------------------------------------------------------------------------
# Job store with snapshot
# ----------------------------------------------------------------------------
class JobStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        if Cfg.SNAPSHOT_PATH.exists():
            try:
                with open(Cfg.SNAPSHOT_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._jobs = data
                    log.info("Loaded %d jobs snapshot" % len(self._jobs))
            except Exception as e:
                log.warning("Failed loading snapshot: %s", e)

    def snapshot_loop(self):
        while True:
            time.sleep(Cfg.SNAPSHOT_INTERVAL)
            self.save()

    def save(self):
        tmp = Cfg.SNAPSHOT_PATH.with_suffix('.tmp')
        try:
            with self._lock:
                tmp.write_text(json.dumps(self._jobs, ensure_ascii=False, indent=2), encoding='utf-8')
                tmp.replace(Cfg.SNAPSHOT_PATH)
        except Exception as e:
            log.warning("Snapshot save failed: %s", e)

    def create(self, orig: str):
        now = datetime.utcnow().isoformat() + 'Z'
        with self._lock:
            self._jobs[orig] = {
                'original_filename': orig,
                'status': 'pending',
                'bucket': None,
                'extracted_name': None,
                'extracted_account': None,
                'stored_filename': orig,
                'created_at': now,
                'started_at': None,
                'completed_at': None,
                'error': None
            }

    def update(self, orig: str, **fields):
        with self._lock:
            if orig in self._jobs:
                self._jobs[orig].update(fields)

    def get(self, orig: str) -> Optional[Dict]:
        with self._lock:
            return self._jobs.get(orig)

    def list(self) -> List[Dict]:
        with self._lock:
            return list(self._jobs.values())

job_store = JobStore()
threading.Thread(target=job_store.snapshot_loop, daemon=True).start()

# ----------------------------------------------------------------------------
# Security (optional API Key)
# ----------------------------------------------------------------------------
from fastapi import Security
from fastapi.security.api_key import APIKeyHeader
_api_key_header = APIKeyHeader(name='X-API-Key', auto_error=False)

def require_api_key(api_key: str = Security(_api_key_header)):
    if Cfg.API_KEY:
        if not api_key or api_key != Cfg.API_KEY:
            raise HTTPException(status_code=401, detail='Invalid or missing API key')
    return True

# ----------------------------------------------------------------------------
# OCR & classification helpers
# ----------------------------------------------------------------------------
LABELS = [
    "Name of Account Holder", "First name", "First names", "Surname", "Surnames",
    "Other name", "Other names", "Print name", "Account Name", "Institution Name",
    "Account Number", "Account number", "Account no", "CSD Number", "Client CSD Securities Account No",
    "ID number", "UMB-IHL ID Number", "Name", "Name of Organisation", "Name of Organization"
]
LABEL_NORMS = { re.sub(r'[^a-z0-9]', '', l.lower()) for l in LABELS }

ACCOUNT_LABELS = [
    "Account Number", "Account number", "Account no", "CSD Number", "Client CSD Securities Account No", "ID number", "UMB-IHL ID Number"
]

BLACKLIST_BASE = LABEL_NORMS | { re.sub(r'[^a-z0-9]', '', w.lower()) for w in [
    'branch','account','name','surname','other','print','institution','organization','organisation','no','number','holder','csd','id','client','details','purpose','period','address','tel','email','photo','reference','date','relationship','employer','spouse','failed','partial','indexed','fully','of','the','and','or','as','it','is','are','was','be','on','in','at','to','for','by','with','from','this','that','these','those','a','an'
] }

def normalize(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', s.lower())

def extract_text(path: Path) -> str:
    try:
        if path.suffix.lower() == '.pdf':
            pages = convert_from_path(str(path), dpi=Cfg.DPI, poppler_path=Cfg.POPPLER_PATH if os.name == 'nt' else None)
            return "\n".join(pytesseract.image_to_string(p) for p in pages)
        else:
            img = Image.open(path).convert('RGB')
            return pytesseract.image_to_string(img)
    except Exception as e:
        log.warning("Text extraction failed %s: %s", path, e)
        return ""

def parse_fields(text: str):
    lines = text.splitlines()
    name_val, account_val = None, None
    # Name attempt
    label_regex = re.compile(r'(' + '|'.join([re.escape(l) for l in LABELS]) + r')\s*:?', re.IGNORECASE)
    for idx, line in enumerate(lines):
        m = label_regex.search(line)
        if not m:
            continue
        after = line[m.end():].strip()
        words = []
        for w in after.split():
            nw = normalize(w)
            if nw in LABEL_NORMS or nw in BLACKLIST_BASE:
                break
            words.append(w)
        if not words and idx + 1 < len(lines):
            nxt = lines[idx+1].strip()
            for w in nxt.split():
                nw = normalize(w)
                if nw in LABEL_NORMS or nw in BLACKLIST_BASE:
                    break
                words.append(w)
        candidate = ' '.join(words).strip()
        ncan = normalize(candidate)
        if candidate and ncan not in BLACKLIST_BASE and len(ncan) > 2:
            name_val = candidate
            break
    # Account attempt
    for label in ACCOUNT_LABELS:
        regex = re.compile(re.escape(label) + r'\s*:?\s*([A-Za-z0-9\-]+)', re.IGNORECASE)
        for line in lines:
            mm = regex.search(line)
            if mm:
                cand = mm.group(1).strip()
                nd = normalize(cand)
                digits = ''.join(c for c in cand if c.isdigit())
                if nd not in BLACKLIST_BASE and (10 <= len(digits) <= 20):
                    account_val = cand
                    break
        if account_val:
            break
    return name_val, account_val

def classify_and_store(src_path: Path, original: str):
    job_store.update(original, status='processing', started_at=datetime.utcnow().isoformat() + 'Z')
    text = extract_text(src_path)
    name, account = parse_fields(text)
    ext = src_path.suffix.lower()
    is_image = ext in ['.png', '.jpg', '.jpeg']
    dest_dir = Cfg.FAIL
    stored_name = src_path.name
    bucket = 'failed'

    if not name and not account:
        pass
    else:
        if name and account:
            safe_name = re.sub(r'[\\/:*?"<>|]', '', name)
            safe_acc = re.sub(r'[\\/:*?"<>|]', '', account)
            stored_name = f"{safe_name}_{safe_acc}.pdf" if is_image else f"{safe_name}_{safe_acc}{ext}"
            dest_dir = Cfg.FULL
            bucket = 'fully_indexed'
        else:
            key = name or account
            safe_key = re.sub(r'[\\/:*?"<>|]', '', key)
            stored_name = f"{safe_key}.pdf" if is_image else f"{safe_key}{ext}"
            dest_dir = Cfg.PART
            bucket = 'partially_indexed'

    dest_path = dest_dir / stored_name
    base, extn = os.path.splitext(stored_name)
    counter = 1
    while dest_path.exists():
        dest_path = dest_dir / f"{base}_{counter}{extn}"
        counter += 1

    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        if bucket == 'failed':
            shutil.move(str(src_path), str(dest_path))
        else:
            if is_image:
                try:
                    img = Image.open(src_path).convert('RGB')
                    img.save(dest_path, 'PDF', resolution=100.0)
                    src_path.unlink(missing_ok=True)
                except Exception as e:
                    log.warning("Image->PDF conversion failed %s: %s", src_path, e)
                    shutil.move(str(src_path), str(Cfg.FAIL / src_path.name))
                    bucket = 'failed'
                    dest_path = Cfg.FAIL / src_path.name
            else:
                shutil.move(str(src_path), dest_path)
    except Exception as e:
        log.error("Move failed %s -> %s: %s", src_path, dest_path, e)
        bucket = 'failed'

    job_store.update(original,
        status='completed',
        bucket=bucket,
        extracted_name=name,
        extracted_account=account,
        stored_filename=dest_path.name,
        completed_at=datetime.utcnow().isoformat() + 'Z'
    )
    return job_store.get(original)

# ----------------------------------------------------------------------------
# Async queue
# ----------------------------------------------------------------------------
_queue: List[str] = []
_q_lock = threading.Lock()

def enqueue(original: str, path: Path):
    with _q_lock:
        _queue.append(original)

def worker_loop():
    while True:
        time.sleep(0.5)
        original = None
        with _q_lock:
            if _queue:
                original = _queue.pop(0)
        if not original:
            continue
        # locate file inside scan dir
        src = Cfg.SCAN_DIR / original
        if not src.exists():
            job_store.update(original, status='error', error='file disappeared')
            continue
        classify_and_store(src, original)

if Cfg.PROCESS_MODE == 'async':
    threading.Thread(target=worker_loop, daemon=True).start()

# ----------------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------------
app = FastAPI(title='MerbanTechOCR', version='1.0.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=Cfg.ALLOW_ORIGINS if Cfg.ALLOW_ORIGINS != ['*'] else ['*'],
    allow_methods=['*'],
    allow_headers=['*']
)

# ----------------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------------
@app.get('/health')
def health():
    return {
        'status': 'ok',
        'mode': Cfg.PROCESS_MODE,
        'jobs': len(job_store.list()),
        'time': datetime.utcnow().isoformat() + 'Z'
    }

@app.get('/_routes')
def routes():
    r = []
    for rt in app.router.routes:
        p = getattr(rt, 'path', None)
        if p:
            r.append({'path': p, 'methods': sorted(list(getattr(rt, 'methods', [])))})
    return {'count': len(r), 'routes': sorted(r, key=lambda x: x['path'])}

@app.post('/api/files/upload')
async def upload(file: UploadFile = File(...), ok=Depends(require_api_key)):
    original = file.filename or 'uploaded_file'
    dest = Cfg.SCAN_DIR / original
    Cfg.SCAN_DIR.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    dest.write_bytes(content)
    job_store.create(original)
    if Cfg.PROCESS_MODE == 'immediate':
        result = classify_and_store(dest, original)
        return {'status': 'completed', 'job': result}
    else:
        enqueue(original, dest)
        job_store.update(original, status='queued')
        return {'status': 'queued', 'filename': original}

@app.post('/process-sync')
async def process_sync(file: UploadFile = File(...), ok=Depends(require_api_key)):
    original = file.filename or 'uploaded_file'
    tmp = Cfg.SCAN_DIR / f"_sync_{int(time.time()*1000)}_{original}"
    tmp.write_bytes(await file.read())
    job_store.create(original)
    job_store.update(original, status='processing', started_at=datetime.utcnow().isoformat() + 'Z')
    out = classify_and_store(tmp, original)
    return {'status': 'completed', 'job': out}

@app.get('/api/files/list')
def list_files(status: Optional[str] = Query(None)):
    mapping = {
        'fully': Cfg.FULL,
        'partial': Cfg.PART,
        'failed': Cfg.FAIL,
        'scan': Cfg.SCAN_DIR
    }
    if status:
        base = mapping.get(status, Cfg.SCAN_DIR)
        return sorted([f.name for f in base.iterdir() if f.is_file()])
    results = []
    for base in [Cfg.FULL, Cfg.PART, Cfg.FAIL, Cfg.SCAN_DIR]:
        results.extend([f.name for f in base.iterdir() if f.is_file()])
    return sorted(set(results))

@app.get('/api/files/{filename}')
def get_file(filename: str):
    for base in [Cfg.FULL, Cfg.PART, Cfg.FAIL, Cfg.SCAN_DIR]:
        candidate = base / filename
        if candidate.exists():
            return FileResponse(candidate, filename=filename)
    raise HTTPException(status_code=404, detail='File not found')

@app.get('/api/files/{filename}/metadata')
def meta(filename: str):
    for base in [Cfg.FULL, Cfg.PART, Cfg.FAIL, Cfg.SCAN_DIR]:
        candidate = base / filename
        if candidate.exists():
            st = candidate.stat()
            return {
                'filename': filename,
                'size': st.st_size,
                'modified': datetime.utcfromtimestamp(st.st_mtime).isoformat() + 'Z',
                'directory': str(base)
            }
    raise HTTPException(status_code=404, detail='File not found')

@app.get('/status/{filename}')
def status(filename: str):
    job = job_store.get(filename)
    if not job:
        raise HTTPException(status_code=404, detail='Not found')
    return job

@app.get('/results/{filename}')
def results(filename: str):
    job = job_store.get(filename)
    if not job:
        raise HTTPException(status_code=404, detail='Not found')
    if job.get('status') != 'completed':
        return {'status': job.get('status')}
    return job

@app.get('/search')
def search(q: str = Query("")):
    term = q.strip().lower()
    if not term:
        return []
    hits = []
    for base in [Cfg.FULL, Cfg.PART, Cfg.FAIL, Cfg.SCAN_DIR]:
        for f in base.iterdir():
            if f.is_file() and term in f.name.lower():
                hits.append(f.name)
    return sorted(set(hits))

@app.get('/stats')
def stats():
    return {
        'fully_indexed': len(list(Cfg.FULL.glob('*'))),
        'partially_indexed': len(list(Cfg.PART.glob('*'))),
        'failed': len(list(Cfg.FAIL.glob('*'))),
        'scan': len(list(Cfg.SCAN_DIR.glob('*'))),
        'jobs': len(job_store.list())
    }

@app.get('/')
def root():
    return {'service': 'MerbanTechOCR', 'mode': Cfg.PROCESS_MODE}

# ----------------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------------

def start():
    log.info("Starting server on %s:%s mode=%s" % (Cfg.HOST, Cfg.PORT, Cfg.PROCESS_MODE))
    uvicorn.run(app, host=Cfg.HOST, port=Cfg.PORT, log_level='info')

if __name__ == '__main__':
    start()
