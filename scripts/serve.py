from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _ensure_demo_pdfs() -> None:
    """Auto-generate deterministic benchmark PDFs on fresh git clone if absent."""
    m6_pdf_dir = _REPO_ROOT / "eval" / "m6_matrix" / "pdfs"
    if not m6_pdf_dir.is_dir() or not any(m6_pdf_dir.glob("*.pdf")):
        from scripts.make_eval_m6_matrix import make_eval_m6_matrix

        make_eval_m6_matrix(out_dir=str(_REPO_ROOT / "eval" / "m6_matrix"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch FoveaDoc (ZoomDoc-Agent) Web Observatory & Streaming API Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    _ensure_demo_pdfs()

    uvicorn.run(
        "zoomdoc.server.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
