from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pymupdf as fitz
from PIL import Image

from zoomdoc.index.tree import build_document_tree
from zoomdoc.schemas import DocumentTree, PageAsset
from zoomdoc.vision.render import resize_long_side, sha256_file

if TYPE_CHECKING:
    from zoomdoc.config import Settings


class ContentAddressedCache:
    """Content-addressed cross-trace cache for DocumentTrees and rendered page assets."""

    def __init__(self, cache_dir: str = ".cache/zoomdoc", redis_url: str | None = None) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.redis_client: Any = None
        if redis_url:
            try:
                import redis

                r = redis.Redis.from_url(redis_url, socket_connect_timeout=1.0)
                r.ping()
                self.redis_client = r
            except (OSError, ValueError, ImportError):
                self.redis_client = None

    def _sync_tree_to_out_dir(self, tree_json: str, doc_hash: str, out_dir: str | None) -> None:
        if out_dir is None:
            return
        out_tree_file = Path(out_dir) / "assets" / doc_hash / "tree.json"
        if out_tree_file.is_file():
            return
        out_tree_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out_tree_file.parent / f"tree.json.tmp.{time.time()}"
        try:
            tmp_out.write_text(tree_json, encoding="utf-8")
            tmp_out.replace(out_tree_file)
        except OSError:
            if tmp_out.exists():
                tmp_out.unlink(missing_ok=True)

    def get_or_build_tree(
        self,
        pdf_path: str,
        max_leaf_pages: int = 20,
        out_dir: str | None = None,
    ) -> tuple[DocumentTree, bool]:
        """Get cached DocumentTree or build and persist it atomically.

        Returns:
            (tree, cache_hit)
        """
        doc_hash = sha256_file(pdf_path)[:16]
        tree_dir = self.cache_dir / doc_hash
        tree_dir.mkdir(parents=True, exist_ok=True)
        tree_file = tree_dir / f"tree_leaf{max_leaf_pages}.json"
        redis_key = f"tree:{doc_hash}:leaf{max_leaf_pages}"

        # 1. Try Redis
        if self.redis_client is not None:
            try:
                val = self.redis_client.get(redis_key)
                if val:
                    val_str = val.decode("utf-8") if isinstance(val, bytes) else str(val)
                    tree = DocumentTree.model_validate_json(val_str)
                    self._sync_tree_to_out_dir(val_str, doc_hash, out_dir)
                    return tree, True
            except (OSError, ValueError, TypeError):
                pass

        # 2. Try Disk
        if tree_file.is_file():
            try:
                with open(tree_file, "r", encoding="utf-8") as f:
                    content = f.read()
                tree = DocumentTree.model_validate_json(content)
                self._sync_tree_to_out_dir(content, doc_hash, out_dir)
                if self.redis_client is not None:
                    try:
                        self.redis_client.set(redis_key, content, ex=86400)
                    except (OSError, ValueError):
                        pass
                return tree, True
            except (OSError, ValueError):
                pass

        # 3. Build tree
        tree = build_document_tree(pdf_path, max_leaf_pages=max_leaf_pages, out_dir=out_dir)
        tree_json = tree.model_dump_json(indent=2)

        # Atomic disk write
        temp_file = tree_dir / f"{tree_file.name}.tmp.{time.time()}"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(tree_json)
            temp_file.replace(tree_file)
        except OSError:
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)

        # Redis set
        if self.redis_client is not None:
            try:
                self.redis_client.set(redis_key, tree_json, ex=86400)
            except (OSError, ValueError):
                pass

        return tree, False

    def get_or_render_pages(
        self,
        pdf_path: str,
        page_numbers: list[int],
        out_dir: str,
        settings: Settings,
    ) -> tuple[dict[int, PageAsset], bool]:
        """Get cached page assets or render missing pages into content-addressed storage.

        Returns:
            (pages_dict, all_cache_hit)
        """
        doc_hash = sha256_file(pdf_path)[:16]
        spec_key = f"dpi{settings.source_dpi}_v{settings.view_long_side}_t{settings.thumb_long_side}"
        spec_dir = self.cache_dir / doc_hash / spec_key
        spec_dir.mkdir(parents=True, exist_ok=True)

        unique_pages = sorted(list(set(page_numbers)))
        if not unique_pages:
            return {}, True

        cached_pages: dict[int, PageAsset] = {}
        missing_pages: list[int] = []

        for p in unique_pages:
            s_p = spec_dir / f"p{p:03d}_source.png"
            v_p = spec_dir / f"p{p:03d}_view.png"
            t_p = spec_dir / f"p{p:03d}_thumb.png"
            meta_p = spec_dir / f"p{p:03d}_meta.json"
            redis_page_key = f"page:{doc_hash}:{spec_key}:p{p:03d}"

            if s_p.is_file() and v_p.is_file() and t_p.is_file():
                meta: dict[str, Any] | None = None
                if self.redis_client is not None:
                    try:
                        r_val = self.redis_client.get(redis_page_key)
                        if r_val:
                            meta = json.loads(r_val.decode("utf-8") if isinstance(r_val, bytes) else str(r_val))
                    except (OSError, ValueError, TypeError):
                        meta = None

                if meta is None and meta_p.is_file():
                    try:
                        with open(meta_p, "r", encoding="utf-8") as mf:
                            meta = json.load(mf)
                    except (OSError, ValueError):
                        meta = None

                if meta is not None:
                    try:
                        cached_pages[p] = PageAsset(
                            page=p,
                            source_path=str(s_p.resolve()),
                            view_path=str(v_p.resolve()),
                            thumb_path=str(t_p.resolve()),
                            source_size=tuple(meta["source_size"]),
                            view_size=tuple(meta["view_size"]),
                            thumb_size=tuple(meta["thumb_size"]),
                        )
                        continue
                    except (ValueError, KeyError, TypeError):
                        pass
            missing_pages.append(p)

        all_hit = (len(missing_pages) == 0 and len(unique_pages) > 0)

        if missing_pages:
            doc = fitz.open(pdf_path)
            try:
                matrix = fitz.Matrix(settings.source_dpi / 72.0, settings.source_dpi / 72.0)

                for p in missing_pages:
                    if not (1 <= p <= len(doc)):
                        continue
                    page = doc.load_page(p - 1)
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    source_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

                    view_img = resize_long_side(source_img, settings.view_long_side)
                    thumb_img = resize_long_side(source_img, settings.thumb_long_side)

                    s_p = spec_dir / f"p{p:03d}_source.png"
                    v_p = spec_dir / f"p{p:03d}_view.png"
                    t_p = spec_dir / f"p{p:03d}_thumb.png"
                    meta_p = spec_dir / f"p{p:03d}_meta.json"
                    redis_page_key = f"page:{doc_hash}:{spec_key}:p{p:03d}"

                    source_img.save(s_p, format="PNG")
                    view_img.save(v_p, format="PNG")
                    thumb_img.save(t_p, format="PNG")

                    meta_payload = {
                        "source_size": source_img.size,
                        "view_size": view_img.size,
                        "thumb_size": thumb_img.size,
                    }
                    meta_json = json.dumps(meta_payload)
                    tmp_meta = spec_dir / f"{meta_p.name}.tmp.{time.time()}"
                    try:
                        with open(tmp_meta, "w", encoding="utf-8") as mf:
                            mf.write(meta_json)
                        tmp_meta.replace(meta_p)
                    except OSError:
                        if tmp_meta.exists():
                            tmp_meta.unlink(missing_ok=True)

                    if self.redis_client is not None:
                        try:
                            self.redis_client.set(redis_page_key, meta_json, ex=86400)
                        except (OSError, ValueError):
                            pass

                    cached_pages[p] = PageAsset(
                        page=p,
                        source_path=str(s_p.resolve()),
                        view_path=str(v_p.resolve()),
                        thumb_path=str(t_p.resolve()),
                        source_size=source_img.size,
                        view_size=view_img.size,
                        thumb_size=thumb_img.size,
                    )
            finally:
                doc.close()

        result = {p: cached_pages[p] for p in unique_pages if p in cached_pages}
        return result, all_hit


def get_cached_or_build_tree(
    pdf_path: str,
    settings: Settings,
    out_dir: str | None = None,
) -> tuple[DocumentTree, bool]:
    """Helper to get or build DocumentTree with ContentAddressedCache."""
    cache = ContentAddressedCache(
        cache_dir=getattr(settings, "cache_dir", ".cache/zoomdoc"),
        redis_url=getattr(settings, "redis_url", None),
    )
    return cache.get_or_build_tree(
        pdf_path=pdf_path,
        max_leaf_pages=settings.max_leaf_pages,
        out_dir=out_dir,
    )


def get_cached_or_render_pages(
    pdf_path: str,
    page_numbers: list[int],
    out_dir: str,
    settings: Settings,
) -> tuple[dict[int, PageAsset], bool]:
    """Helper to get or render PageAssets with ContentAddressedCache."""
    cache = ContentAddressedCache(
        cache_dir=getattr(settings, "cache_dir", ".cache/zoomdoc"),
        redis_url=getattr(settings, "redis_url", None),
    )
    return cache.get_or_render_pages(
        pdf_path=pdf_path,
        page_numbers=page_numbers,
        out_dir=out_dir,
        settings=settings,
    )
