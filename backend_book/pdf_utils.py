"""PDF helpers: rasterize pages of a scanned PDF into JPEG bytes."""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterator

import fitz  # PyMuPDF
from PIL import Image


@dataclass
class PdfPage:
    index: int  # zero-based
    number: int  # one-based for display
    image_bytes: bytes  # JPEG
    width: int
    height: int


def iter_pdf_pages(
    pdf_bytes: bytes = None,
    pdf_path: str = None,
    start_page: int = 0,
    dpi: int = 200,
    max_side: int = 1800,
    jpeg_quality: int = 90,
) -> Iterator[PdfPage]:
    """Yield each PDF page as a JPEG-encoded bytes payload.

    Pass either pdf_bytes or pdf_path. start_page is zero-based and lets
    callers resume mid-document.
    """
    if pdf_path is not None:
        doc = fitz.open(pdf_path)
    elif pdf_bytes is not None:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    else:
        raise ValueError("pdf_bytes or pdf_path is required")
    try:
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for i in range(start_page, doc.page_count):
            page = doc[i]
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            mode = "RGB" if pix.n < 4 else "RGBA"
            img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
            if img.mode != "RGB":
                img = img.convert("RGB")

            w, h = img.size
            long_side = max(w, h)
            if long_side > max_side:
                scale = max_side / float(long_side)
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
            yield PdfPage(
                index=i,
                number=i + 1,
                image_bytes=buf.getvalue(),
                width=img.size[0],
                height=img.size[1],
            )
    finally:
        doc.close()


def count_pdf_pages(pdf_bytes: bytes = None, pdf_path: str = None) -> int:
    if pdf_path is not None:
        doc = fitz.open(pdf_path)
    else:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return doc.page_count
    finally:
        doc.close()
