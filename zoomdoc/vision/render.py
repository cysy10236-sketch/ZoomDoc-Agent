from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import pymupdf as fitz
from PIL import Image

from zoomdoc.schemas import PageAsset

if TYPE_CHECKING:
    from zoomdoc.config import Settings


def sha256_file(path: str) -> str:
    """Calculate SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def resize_long_side(img: Image.Image, long_side: int) -> Image.Image:
    """Resize image preserving aspect ratio such that the longer dimension equals long_side (downscale only)."""
    w, h = img.size
    max_dim = max(w, h)
    if max_dim <= long_side:
        return img.copy()

    scale = long_side / float(max_dim)
    new_w = max(1, round(w * scale))
    new_h = max(1, round(h * scale))
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def get_pdf_page_count(pdf_path: str) -> int:
    """Return total page count of a PDF using PyMuPDF and close immediately."""
    doc = fitz.open(pdf_path)
    count = len(doc)
    doc.close()
    return count


def render_pdf_pages(
    pdf_path: str,
    page_numbers: list[int],
    out_dir: str,
    settings: Settings,
) -> dict[int, PageAsset]:
    """On-demand render specified 1-based physical pages.

    Preconditions:
    - len(page_numbers) <= settings.max_pages
    - each p in [1, total_pages]
    Raises ValueError on violation.
    """
    unique_pages = sorted(list(set(page_numbers)))
    if len(unique_pages) > settings.max_pages:
        raise ValueError(
            f"Requested {len(unique_pages)} pages exceeds max_pages={settings.max_pages}."
        )

    if not unique_pages:
        return {}

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    for p in unique_pages:
        if not (1 <= p <= total_pages):
            doc.close()
            raise ValueError(f"Invalid page {p}, document has {total_pages} pages.")

    doc_hash = sha256_file(pdf_path)
    assets_dir = Path(out_dir) / "assets" / doc_hash[:16]
    assets_dir.mkdir(parents=True, exist_ok=True)

    results: dict[int, PageAsset] = {}
    matrix = fitz.Matrix(settings.source_dpi / 72.0, settings.source_dpi / 72.0)

    for page_num in unique_pages:
        source_path = assets_dir / f"p{page_num:03d}_source.png"
        view_path = assets_dir / f"p{page_num:03d}_view.png"
        thumb_path = assets_dir / f"p{page_num:03d}_thumb.png"

        if source_path.exists() and view_path.exists() and thumb_path.exists():
            with Image.open(source_path) as s_img, Image.open(view_path) as v_img, Image.open(thumb_path) as t_img:
                results[page_num] = PageAsset(
                    page=page_num,
                    source_path=str(source_path.resolve()),
                    view_path=str(view_path.resolve()),
                    thumb_path=str(thumb_path.resolve()),
                    source_size=s_img.size,
                    view_size=v_img.size,
                    thumb_size=t_img.size,
                )
            continue

        # Render page via PyMuPDF (0-based page index)
        page = doc.load_page(page_num - 1)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        source_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        view_img = resize_long_side(source_img, settings.view_long_side)
        thumb_img = resize_long_side(source_img, settings.thumb_long_side)

        source_img.save(source_path, format="PNG")
        view_img.save(view_path, format="PNG")
        thumb_img.save(thumb_path, format="PNG")

        results[page_num] = PageAsset(
            page=page_num,
            source_path=str(source_path.resolve()),
            view_path=str(view_path.resolve()),
            thumb_path=str(thumb_path.resolve()),
            source_size=source_img.size,
            view_size=view_img.size,
            thumb_size=thumb_img.size,
        )

    doc.close()
    return results


def render_pdf(pdf_path: str, out_dir: str, settings: Settings) -> list[PageAsset]:
    """Render PDF pages at source DPI, plus view and thumbnail downscaled variants.

    Idempotent: skips page rendering if files already exist.
    Raises ValueError if total pages > settings.max_pages.
    """
    total_pages = get_pdf_page_count(pdf_path)
    if total_pages > settings.max_pages:
        raise ValueError(
            f"M1 supports <= {settings.max_pages} pages, but PDF has {total_pages} pages."
        )

    if total_pages == 0:
        return []

    pages_dict = render_pdf_pages(
        pdf_path=pdf_path,
        page_numbers=list(range(1, total_pages + 1)),
        out_dir=out_dir,
        settings=settings,
    )
    return [pages_dict[p] for p in sorted(pages_dict.keys())]
