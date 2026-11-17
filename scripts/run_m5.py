from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIError

from zoomdoc.config import Settings
from zoomdoc.errors import ZoomDocError
from zoomdoc.graph.supervisor import run_m5


def main() -> None:
    load_dotenv(override=False)

    parser = argparse.ArgumentParser(
        description="ZoomDoc-Agent Milestone 5 (FoveaDoc): Topological Foveation, S-BOED & Residual Verification CLI"
    )
    parser.add_argument("--pdf", required=True, help="Path to input PDF file")
    parser.add_argument("--question", required=True, help="Question to answer based on the document")
    parser.add_argument("--model", help="VLM Model name (overrides ZOOMDOC_VLM_MODEL)")
    parser.add_argument("--base-url", help="OpenAI-compatible Base URL (overrides ZOOMDOC_VLM_BASE_URL)")
    parser.add_argument("--api-key", help="API Key (overrides ZOOMDOC_VLM_API_KEY)")
    parser.add_argument("--runs-dir", default="runs", help="Directory to store run assets and traces")
    parser.add_argument("--cache-dir", help="Content-addressed cache directory (overrides ZOOMDOC_CACHE_DIR)")
    parser.add_argument("--no-cache", action="store_true", help="Disable cross-trace content-addressed cache")

    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        print(f"Error: PDF file does not exist: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    overrides: dict[str, Any] = {}
    if args.model:
        overrides["vlm_model"] = args.model
    if args.base_url:
        overrides["vlm_base_url"] = args.base_url
    if args.api_key:
        overrides["vlm_api_key"] = args.api_key
    if args.runs_dir:
        overrides["runs_dir"] = args.runs_dir
    if args.cache_dir:
        overrides["cache_dir"] = args.cache_dir
    if args.no_cache:
        overrides["enable_content_cache"] = False

    settings = Settings(**overrides)

    try:
        result, _ = run_m5(
            question=args.question,
            pdf_path=str(pdf_path),
            settings=settings,
        )
        print(result.model_dump_json(indent=2))
    except (ZoomDocError, APIError, OSError, ValueError) as exc:
        print(f"Execution Error ({type(exc).__name__}): {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
