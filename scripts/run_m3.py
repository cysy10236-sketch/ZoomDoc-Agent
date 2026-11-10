from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from zoomdoc.config import Settings
from zoomdoc.graph.supervisor import run_m3


def main() -> None:
    load_dotenv(override=False)

    parser = argparse.ArgumentParser(
        description="ZoomDoc-Agent Milestone 3: Hierarchical Tree Index & Navigator Document Reasoning CLI"
    )
    parser.add_argument("--pdf", required=True, help="Path to input PDF file")
    parser.add_argument("--question", required=True, help="Question to answer based on the document")
    parser.add_argument("--model", help="VLM Model name (overrides ZOOMDOC_VLM_MODEL)")
    parser.add_argument("--base-url", help="OpenAI-compatible Base URL (overrides ZOOMDOC_VLM_BASE_URL)")
    parser.add_argument("--api-key", help="API Key (overrides ZOOMDOC_VLM_API_KEY)")
    parser.add_argument("--runs-dir", default="runs", help="Directory to store run assets and traces")

    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        print(f"Error: PDF file does not exist: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    overrides = {}
    if args.model:
        overrides["vlm_model"] = args.model
    if args.base_url:
        overrides["vlm_base_url"] = args.base_url
    if args.api_key:
        overrides["vlm_api_key"] = args.api_key
    if args.runs_dir:
        overrides["runs_dir"] = args.runs_dir

    settings = Settings(**overrides)

    try:
        result, _ = run_m3(
            question=args.question,
            pdf_path=str(pdf_path),
            settings=settings,
        )
        print(result.model_dump_json(indent=2))
    except Exception as exc:
        print(f"Execution Error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
