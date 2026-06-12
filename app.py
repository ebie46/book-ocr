"""Book OCR web app.

Job-based architecture: clients upload a file (PDF or image), the server
persists it to disk and processes it in the background. Clients poll
job status. This survives flaky links (Tailscale, etc.) since the OCR
itself does not depend on the HTTP connection staying alive.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from backend_book import jobs as jobs_mod
from backend_book.epub_utils import text_to_epub
from backend_book.ocr import OLLAMA_HOST, OLLAMA_MODEL, list_models

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("book_ocr")

ROOT = Path(__file__).parent
FRONTEND_DIR = ROOT / "frontend_book"

PDF_MIME = "application/pdf"
IMAGE_PREFIX = "image/"

app = FastAPI(title="Book OCR", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _on_startup():
    # Pick up any jobs that were running when we last shut down.
    resumed = jobs_mod.resume_pending_jobs()
    if resumed:
        log.info("Resumed %s pending job(s)", resumed)


def _is_pdf(file: UploadFile, raw: bytes) -> bool:
    if (file.content_type or "").lower() == PDF_MIME:
        return True
    if (file.filename or "").lower().endswith(".pdf"):
        return True
    return raw[:5] == b"%PDF-"


def _is_image(file: UploadFile) -> bool:
    return (file.content_type or "").startswith(IMAGE_PREFIX)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "ollama_host": OLLAMA_HOST,
        "default_model": OLLAMA_MODEL,
    }


@app.get("/api/models")
async def models() -> dict:
    try:
        names = await list_models()
    except Exception as e:
        log.warning("Failed to list Ollama models: %s", e)
        return {"models": [], "error": str(e), "default": OLLAMA_MODEL}
    return {"models": names, "default": OLLAMA_MODEL}


# --- Job API ----------------------------------------------------------------


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    prompt: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    dpi: int = Form(200),
):
    # Stream upload directly to a temp file so multi-hundred-MB PDFs don't
    # sit in RAM. We sniff the first chunk for content-type detection.
    import tempfile

    tmp = tempfile.NamedTemporaryFile(prefix="upload-", suffix=".bin", delete=False)
    head = b""
    total = 0
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            if not head:
                head = chunk[:8]
            tmp.write(chunk)
            total += len(chunk)
        tmp.close()
    except Exception:
        tmp.close()
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except Exception:
            pass
        raise

    if total == 0:
        Path(tmp.name).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Empty file.")

    is_pdf = (
        (file.content_type or "").lower() == PDF_MIME
        or (file.filename or "").lower().endswith(".pdf")
        or head[:5] == b"%PDF-"
    )
    is_image = (file.content_type or "").startswith(IMAGE_PREFIX)
    if not (is_pdf or is_image):
        Path(tmp.name).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Hanya menerima file gambar atau PDF.")

    try:
        state = await jobs_mod.create_job_from_path(
            filename=file.filename or "",
            tmp_path=tmp.name,
            is_pdf=is_pdf,
            model=model,
            prompt=prompt,
            dpi=int(dpi or 200),
        )
    except Exception as e:
        Path(tmp.name).unlink(missing_ok=True)
        log.exception("Failed to create job")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return JSONResponse(state.to_public(), status_code=201)


@app.get("/api/jobs")
async def list_jobs():
    return {"jobs": jobs_mod.list_jobs()}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    st = jobs_mod.get_job(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return st.to_public()


@app.get("/api/jobs/{job_id}/text")
async def get_job_text(job_id: str, since: int = 0, tail: int = 0):
    """Return job text.

    - default: full text (use only for small jobs / final download).
    - ?since=N: pages strictly after N (for incremental polling).
    - ?tail=N: only the last N completed pages (preview).
    """
    st = jobs_mod.get_job(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if since > 0:
        text = jobs_mod.get_text(job_id, start=since + 1)
    elif tail > 0:
        start = max(1, st.done_pages - tail + 1)
        text = jobs_mod.get_text(job_id, start=start)
    else:
        text = jobs_mod.get_text(job_id)
    return JSONResponse(
        {
            "id": job_id,
            "status": st.status,
            "done_pages": st.done_pages,
            "failed_pages": st.failed_pages,
            "total_pages": st.total_pages,
            "since": since,
            "tail": tail,
            "text": text,
        }
    )


@app.get("/api/jobs/{job_id}/download")
async def download_job_text(job_id: str):
    st = jobs_mod.get_job(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail="Job not found")
    base = Path(st.filename or "ocr").stem or "ocr"

    # Stream pages from disk so the full text never sits in memory.
    def gen():
        for n in range(1, st.total_pages + 1):
            chunk = jobs_mod.get_page_text(job_id, n) or ""
            if not chunk:
                continue
            if st.total_pages > 1:
                yield f"===== Halaman {n} =====\n\n".encode("utf-8")
            yield chunk.encode("utf-8")
            yield b"\n\n"

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        gen(),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{base}.txt"'},
    )


@app.get("/api/jobs/{job_id}/epub")
async def download_job_epub(job_id: str):
    st = jobs_mod.get_job(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail="Job not found")
    text = jobs_mod.get_text(job_id)
    if not text.strip():
        raise HTTPException(status_code=400, detail="Belum ada teks untuk diekspor.")
    base = Path(st.filename or "ocr").stem or "ocr"
    title = base.replace("_", " ").strip() or "Book OCR"
    try:
        epub_bytes = await asyncio.to_thread(text_to_epub, text, title=title)
    except Exception as e:
        log.exception("EPUB conversion failed")
        raise HTTPException(status_code=500, detail=f"EPUB gagal dibuat: {e}") from e
    return Response(
        content=epub_bytes,
        media_type="application/epub+zip",
        headers={"Content-Disposition": f'attachment; filename="{base}.epub"'},
    )


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    ok = await jobs_mod.cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "canceled"}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    ok = jobs_mod.delete_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "deleted"}


# --- static frontend --------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return JSONResponse({"message": "Book OCR API running.", "docs": "/docs"})


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8003"))
    host = os.getenv("HOST", "0.0.0.0")
    # Single worker on purpose: jobs are tracked in-process. Multiple
    # workers would each try to resume the same jobs.
    uvicorn.run("app:app", host=host, port=port, reload=False, workers=1)
