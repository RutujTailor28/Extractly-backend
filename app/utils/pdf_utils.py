"""PDF helper utilities using PyMuPDF."""

from __future__ import annotations

from io import BytesIO

import fitz
from PIL import Image

from app.core.config import settings


def get_pdf_page_count(content: bytes) -> int:
    """Return the number of pages in a PDF."""
    with fitz.open(stream=content, filetype="pdf") as document:
        return document.page_count


def render_pdf_pages(
    content: bytes,
    *,
    dpi: int | None = None,
) -> list[bytes]:
    """
    Render each PDF page to PNG bytes at the configured DPI.

    Returns a list of PNG byte strings, one per page (1-indexed order).
    """
    render_dpi = dpi or settings.PDF_RENDER_DPI
    zoom = render_dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pages: list[bytes] = []

    with fitz.open(stream=content, filetype="pdf") as document:
        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            png_bytes = pix.tobytes("png")
            # Ensure Pillow can open the result (normalize)
            image = Image.open(BytesIO(png_bytes))
            image.load()
            buffer = BytesIO()
            image.convert("RGB").save(buffer, format="PNG")
            pages.append(buffer.getvalue())

    return pages
