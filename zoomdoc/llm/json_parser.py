from __future__ import annotations

import json
import re


def extract_json(text: str) -> dict:
    """Extract and parse first top-level JSON object from model textual response.

    Ported and strengthened from ViDoRAG utils/parse_tool.py:19-27.
    Raises:
        json.JSONDecodeError: if JSON syntax is invalid or no brackets found.
        ValueError: if parsed JSON is not a dictionary.
    """
    clean_text = text.replace("```json", "").replace("```", "").strip()

    start_idx = clean_text.find("{")
    end_idx = clean_text.rfind("}")

    if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
        json_str = clean_text[start_idx : end_idx + 1]
        obj = json.loads(json_str)
    else:
        obj = json.loads(clean_text)

    if not isinstance(obj, dict):
        raise ValueError(f"Parsed JSON must be a dictionary, got {type(obj).__name__}: {obj}")

    return obj
