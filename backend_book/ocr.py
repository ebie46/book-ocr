"""OCR service that talks to a local Ollama vision model (e.g. minicpm-v)."""
from __future__ import annotations

import base64
import io
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from PIL import Image

log = logging.getLogger("book_ocr.ocr")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "300"))
# Keep the model loaded in Ollama between page calls so the second page
# onward doesn't pay the cold-start cost again. Short by default so VRAM
# is freed quickly after a job finishes. Override via env if needed
# (e.g. "30s", "5m", or "0" to unload immediately).
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "2m")

DEFAULT_PROMPT = (
    "Anda adalah mesin OCR untuk halaman buku hasil scan. "
    "Salin SELURUH teks pada gambar persis seperti yang tertulis. "
    "Pertahankan baris baru, paragraf, judul, dan urutan baca. "
    "JANGAN menambahkan label, judul section, komentar, catatan, terjemahan, "
    "ringkasan, atau tanda markdown apa pun. "
    "JANGAN menambahkan tanda kutip, code fence, atau penjelasan. "
    "Jika gambar kosong atau tidak terbaca, kembalikan string kosong. "
    "Keluarkan HANYA teks mentah yang tertulis di gambar."
)


@dataclass
class OcrResult:
    text: str
    model: str
    prompt: str
    latency_s: float = 0.0
    eval_count: int = 0
    eval_duration_ns: int = 0
    prompt_eval_count: int = 0
    total_duration_ns: int = 0


def _normalize_image(raw: bytes, max_side: int = 1600) -> bytes:
    """Decode image, auto-rotate via EXIF, downscale long side, re-encode as JPEG.

    Keeps payloads small for the Ollama call and avoids odd formats the
    backend might choke on.
    """
    with Image.open(io.BytesIO(raw)) as img:
        try:
            from PIL import ImageOps

            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        w, h = img.size
        long_side = max(w, h)
        if long_side > max_side:
            scale = max_side / float(long_side)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue()


async def run_ocr(
    image_bytes: bytes,
    prompt: Optional[str] = None,
    model: Optional[str] = None,
) -> OcrResult:
    """Send the image to Ollama and return the extracted text."""
    used_prompt = prompt.strip() if prompt and prompt.strip() else DEFAULT_PROMPT
    used_model = model or OLLAMA_MODEL

    normalized = _normalize_image(image_bytes)
    b64 = base64.b64encode(normalized).decode("ascii")

    payload = {
        "model": used_model,
        "prompt": used_prompt,
        "images": [b64],
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": 0.1,
            "num_ctx": 4096,
        },
    }

    url = f"{OLLAMA_HOST.rstrip('/')}/api/generate"
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    latency = time.perf_counter() - t0

    text = (data.get("response") or "").strip()

    eval_count = int(data.get("eval_count") or 0)
    eval_duration_ns = int(data.get("eval_duration") or 0)
    prompt_eval_count = int(data.get("prompt_eval_count") or 0)
    total_duration_ns = int(data.get("total_duration") or 0)

    # tokens/sec from Ollama's own timing (eval phase = generation)
    tps = (
        eval_count / (eval_duration_ns / 1e9)
        if eval_count and eval_duration_ns
        else 0.0
    )
    log.info(
        "OCR ok · %.2fs · model=%s · in=%d tok · out=%d tok · %.1f tok/s · %d chars",
        latency,
        used_model,
        prompt_eval_count,
        eval_count,
        tps,
        len(text),
    )

    return OcrResult(
        text=text,
        model=used_model,
        prompt=used_prompt,
        latency_s=latency,
        eval_count=eval_count,
        eval_duration_ns=eval_duration_ns,
        prompt_eval_count=prompt_eval_count,
        total_duration_ns=total_duration_ns,
    )


async def list_models() -> list[str]:
    url = f"{OLLAMA_HOST.rstrip('/')}/api/tags"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
