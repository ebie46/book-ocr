"""Background OCR job manager.

Each job lives on disk under JOBS_DIR/<job_id>/ with this layout:

    input.<ext>          original uploaded file (PDF or image)
    state.json           job metadata + progress (atomic writes)
    pages/0001.txt       OCR text for page 1 (created when finished)
    pages/0001.err       error message for page 1 (if it failed)

That makes the work resumable: on startup we look for any state.json
with status=running|queued and pick up from the next missing page.

The endpoint API is async (FastAPI), but the worker itself runs in a
dedicated asyncio task per job so it can call run_ocr without blocking
HTTP handlers. Workers are started with asyncio.create_task and
referenced from a process-local registry; that's enough for a
single-worker uvicorn deployment which is what we recommend here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .ocr import OLLAMA_MODEL, run_ocr
from .pdf_utils import count_pdf_pages, iter_pdf_pages

log = logging.getLogger("book_ocr.jobs")

JOBS_DIR = Path(os.getenv("JOBS_DIR", str(Path(__file__).resolve().parent.parent / "data" / "jobs")))
JOBS_DIR.mkdir(parents=True, exist_ok=True)

# In-process registry of running asyncio tasks per job_id.
_running: dict[str, asyncio.Task] = {}
# Per-job locks so state.json writes don't race.
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(job_id: str) -> asyncio.Lock:
    lock = _locks.get(job_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[job_id] = lock
    return lock


@dataclass
class JobState:
    id: str
    filename: str
    kind: str  # "pdf" | "image"
    input_path: str
    total_pages: int
    done_pages: int = 0
    failed_pages: int = 0
    status: str = "queued"  # queued | running | done | error | canceled
    error: str = ""
    model: str = ""
    prompt: str = ""
    dpi: int = 200
    created_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())
    finished_at: Optional[float] = None

    def to_public(self) -> dict:
        d = asdict(self)
        # Don't leak absolute path
        d.pop("input_path", None)
        return d


def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _state_path(job_id: str) -> Path:
    return _job_dir(job_id) / "state.json"


def _pages_dir(job_id: str) -> Path:
    return _job_dir(job_id) / "pages"


def _page_txt(job_id: str, page_number: int) -> Path:
    return _pages_dir(job_id) / f"{page_number:04d}.txt"


def _page_err(job_id: str, page_number: int) -> Path:
    return _pages_dir(job_id) / f"{page_number:04d}.err"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_state(job_id: str) -> Optional[JobState]:
    p = _state_path(job_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return JobState(**data)
    except Exception as e:
        log.warning("Failed to load state for %s: %s", job_id, e)
        return None


def _save_state(state: JobState) -> None:
    state.updated_at = time.time()
    payload = json.dumps(asdict(state), ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write(_state_path(state.id), payload)


async def _save_state_locked(state: JobState) -> None:
    async with _lock_for(state.id):
        _save_state(state)


def list_jobs() -> list[dict]:
    out = []
    if not JOBS_DIR.exists():
        return out
    for entry in sorted(JOBS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        st = _load_state(entry.name)
        if st is None:
            continue
        out.append(st.to_public())
    out.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    return out


def get_job(job_id: str) -> Optional[JobState]:
    return _load_state(job_id)


def get_text(job_id: str, *, start: int = 1, limit: Optional[int] = None) -> str:
    """Concatenate page texts from page `start` to `start+limit-1` (inclusive).

    With no args returns the full text. Limit is useful for clients that
    only want a recent window during streaming so they don't re-download
    the whole book on every poll.
    """
    state = _load_state(job_id)
    if state is None:
        return ""
    parts: list[str] = []
    pages_dir = _pages_dir(job_id)
    if not pages_dir.exists():
        return ""
    multi = state.total_pages > 1
    end = state.total_pages if limit is None else min(state.total_pages, start + limit - 1)
    for n in range(max(1, start), end + 1):
        f = _page_txt(job_id, n)
        if not f.exists():
            continue
        body = f.read_text(encoding="utf-8")
        if multi:
            parts.append(f"===== Halaman {n} =====\n\n{body}")
        else:
            parts.append(body)
        ferr = _page_err(job_id, n)
        if ferr.exists():
            parts.append(f"[ERROR halaman {n}: {ferr.read_text(encoding='utf-8')}]")
    return "\n\n".join(parts).strip() + ("\n" if parts else "")


def get_page_text(job_id: str, page_number: int) -> Optional[str]:
    f = _page_txt(job_id, page_number)
    if f.exists():
        return f.read_text(encoding="utf-8")
    err = _page_err(job_id, page_number)
    if err.exists():
        return f"[ERROR: {err.read_text(encoding='utf-8')}]"
    return None


async def create_job(
    *,
    filename: str,
    raw: bytes,
    is_pdf: bool,
    model: Optional[str],
    prompt: Optional[str],
    dpi: int,
) -> JobState:
    job_id = uuid.uuid4().hex[:12]
    jdir = _job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    _pages_dir(job_id).mkdir(parents=True, exist_ok=True)

    ext = ".pdf" if is_pdf else _guess_image_ext(filename)
    input_path = jdir / f"input{ext}"
    _atomic_write(input_path, raw)

    if is_pdf:
        try:
            total = count_pdf_pages(pdf_path=str(input_path))
        except Exception as e:
            shutil.rmtree(jdir, ignore_errors=True)
            raise RuntimeError(f"Tidak bisa membaca PDF: {e}") from e
    else:
        total = 1

    state = JobState(
        id=job_id,
        filename=filename or ("document.pdf" if is_pdf else "image"),
        kind="pdf" if is_pdf else "image",
        input_path=str(input_path),
        total_pages=total,
        model=model or OLLAMA_MODEL,
        prompt=prompt or "",
        dpi=int(dpi or 200),
        status="queued",
    )
    _save_state(state)
    _spawn_worker(state)
    return state


async def create_job_from_path(
    *,
    filename: str,
    tmp_path: str,
    is_pdf: bool,
    model: Optional[str],
    prompt: Optional[str],
    dpi: int,
) -> JobState:
    """Like create_job but takes an already-on-disk file. Avoids buffering
    multi-hundred-MB uploads in memory."""
    job_id = uuid.uuid4().hex[:12]
    jdir = _job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    _pages_dir(job_id).mkdir(parents=True, exist_ok=True)

    ext = ".pdf" if is_pdf else _guess_image_ext(filename)
    input_path = jdir / f"input{ext}"
    try:
        os.replace(tmp_path, input_path)  # cheap rename when on same fs
    except OSError:
        # different filesystems → fall back to copy
        shutil.copyfile(tmp_path, input_path)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if is_pdf:
        try:
            total = count_pdf_pages(pdf_path=str(input_path))
        except Exception as e:
            shutil.rmtree(jdir, ignore_errors=True)
            raise RuntimeError(f"Tidak bisa membaca PDF: {e}") from e
    else:
        total = 1

    state = JobState(
        id=job_id,
        filename=filename or ("document.pdf" if is_pdf else "image"),
        kind="pdf" if is_pdf else "image",
        input_path=str(input_path),
        total_pages=total,
        model=model or OLLAMA_MODEL,
        prompt=prompt or "",
        dpi=int(dpi or 200),
        status="queued",
    )
    _save_state(state)
    _spawn_worker(state)
    return state


def _guess_image_ext(filename: str) -> str:
    name = (filename or "").lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"):
        if name.endswith(ext):
            return ext
    return ".img"


def _spawn_worker(state: JobState) -> None:
    if state.id in _running and not _running[state.id].done():
        return
    loop = asyncio.get_event_loop()
    task = loop.create_task(_run_job(state.id), name=f"ocr-job-{state.id}")
    _running[state.id] = task


async def cancel_job(job_id: str) -> bool:
    task = _running.get(job_id)
    state = _load_state(job_id)
    if state is None:
        return False
    if task and not task.done():
        task.cancel()
    state.status = "canceled"
    state.finished_at = time.time()
    await _save_state_locked(state)
    return True


def delete_job(job_id: str) -> bool:
    task = _running.get(job_id)
    if task and not task.done():
        task.cancel()
    _running.pop(job_id, None)
    _locks.pop(job_id, None)
    jdir = _job_dir(job_id)
    if not jdir.exists():
        return False
    shutil.rmtree(jdir, ignore_errors=True)
    return True


async def _run_job(job_id: str) -> None:
    state = _load_state(job_id)
    if state is None:
        log.warning("Job %s missing state, abort", job_id)
        return

    state.status = "running"
    state.error = ""
    state.finished_at = None
    await _save_state_locked(state)

    try:
        # Figure out where to resume from
        completed = _scan_completed_pages(job_id, state.total_pages)
        state.done_pages = sum(1 for ok in completed.values() if ok is True)
        state.failed_pages = sum(1 for ok in completed.values() if ok is False)
        await _save_state_locked(state)

        if state.kind == "pdf":
            # Resume from the lowest page number that has neither a .txt nor .err
            start = _next_pending_index(completed, state.total_pages)
            if start >= state.total_pages:
                state.status = "done"
                state.finished_at = time.time()
                await _save_state_locked(state)
                return

            # Pipeline: rasterize page N+1 in a background thread while
            # page N is being OCR'd. Saves the per-page raster cost for
            # large books.
            page_iter = iter_pdf_pages(
                pdf_path=state.input_path,
                start_page=start,
                dpi=state.dpi,
            )

            def _next_page():
                try:
                    return next(page_iter)
                except StopIteration:
                    return None

            current = await asyncio.to_thread(_next_page)
            while current is not None:
                ahead_task = asyncio.create_task(asyncio.to_thread(_next_page))
                try:
                    await _process_one(state, current.number, current.image_bytes)
                finally:
                    current = await ahead_task

        else:  # single image
            if not _page_txt(job_id, 1).exists():
                raw = Path(state.input_path).read_bytes()
                await _process_one(state, 1, raw)

        # final status
        state = _load_state(job_id) or state
        if state.failed_pages == 0:
            state.status = "done"
        elif state.done_pages == 0:
            state.status = "error"
            state.error = "Semua halaman gagal"
        else:
            state.status = "done"
        state.finished_at = time.time()
        await _save_state_locked(state)

    except asyncio.CancelledError:
        log.info("Job %s canceled", job_id)
        st = _load_state(job_id)
        if st and st.status not in ("done", "error", "canceled"):
            st.status = "canceled"
            st.finished_at = time.time()
            await _save_state_locked(st)
        raise
    except Exception as e:
        log.exception("Job %s failed", job_id)
        st = _load_state(job_id) or state
        st.status = "error"
        st.error = str(e)
        st.finished_at = time.time()
        await _save_state_locked(st)
    finally:
        _running.pop(job_id, None)


async def _process_one(state: JobState, page_number: int, image_bytes: bytes) -> None:
    job_id = state.id
    txt_path = _page_txt(job_id, page_number)
    err_path = _page_err(job_id, page_number)

    if txt_path.exists():
        return  # already done

    try:
        result = await run_ocr(
            image_bytes,
            prompt=state.prompt or None,
            model=state.model or None,
        )
        _atomic_write(txt_path, (result.text or "").encode("utf-8"))
        if err_path.exists():
            err_path.unlink(missing_ok=True)
        st = _load_state(job_id) or state
        st.done_pages += 1
        # if we just succeeded a previously-failed page, decrement failed
        # (we don't track that precisely; recount instead for accuracy)
        completed = _scan_completed_pages(job_id, st.total_pages)
        st.done_pages = sum(1 for ok in completed.values() if ok is True)
        st.failed_pages = sum(1 for ok in completed.values() if ok is False)
        await _save_state_locked(st)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("Page %s of %s failed: %s", page_number, job_id, e)
        _atomic_write(err_path, str(e).encode("utf-8"))
        st = _load_state(job_id) or state
        completed = _scan_completed_pages(job_id, st.total_pages)
        st.done_pages = sum(1 for ok in completed.values() if ok is True)
        st.failed_pages = sum(1 for ok in completed.values() if ok is False)
        await _save_state_locked(st)


def _scan_completed_pages(job_id: str, total: int) -> dict[int, bool]:
    """Return {page_number: True if .txt, False if .err, missing keys = pending}."""
    out: dict[int, bool] = {}
    pdir = _pages_dir(job_id)
    if not pdir.exists():
        return out
    for n in range(1, total + 1):
        if _page_txt(job_id, n).exists():
            out[n] = True
        elif _page_err(job_id, n).exists():
            out[n] = False
    return out


def _next_pending_index(completed: dict[int, bool], total: int) -> int:
    """Zero-based index of the first page that's neither .txt nor .err.

    Returns total if everything is processed (success or failure) — in
    that case we do not reprocess failures automatically; user can retry
    by deleting and re-uploading. Keeps the work bounded.
    """
    for n in range(1, total + 1):
        if n not in completed:
            return n - 1
    return total


def resume_pending_jobs() -> int:
    """Restart any job that was running/queued at last shutdown."""
    if not JOBS_DIR.exists():
        return 0
    count = 0
    for entry in JOBS_DIR.iterdir():
        if not entry.is_dir():
            continue
        st = _load_state(entry.name)
        if st is None:
            continue
        if st.status in ("queued", "running"):
            log.info("Resuming job %s (%s)", st.id, st.status)
            _spawn_worker(st)
            count += 1
    return count
