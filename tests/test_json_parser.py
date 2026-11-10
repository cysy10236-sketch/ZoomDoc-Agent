from __future__ import annotations

import json

import pytest

from zoomdoc.llm.json_parser import extract_json


def test_extract_json_markdown_wrapped() -> None:
    text = """```json
    {
        "reason": "Found on page 1",
        "choice": [0]
    }
    ```"""
    data = extract_json(text)
    assert data["choice"] == [0]
    assert data["reason"] == "Found on page 1"


def test_extract_json_surrounding_commentary() -> None:
    text = """Here is my detailed analysis step-by-step:
    I inspected the images and decided:
    {"action": "answer", "reason": "visible clearly", "answer": "42%", "reference": [0]}
    Hope this helps!"""
    data = extract_json(text)
    assert data["action"] == "answer"
    assert data["answer"] == "42%"


def test_extract_json_no_brackets_raises() -> None:
    text = "Sorry, I cannot answer this question."
    with pytest.raises(json.JSONDecodeError):
        extract_json(text)


def test_extract_json_list_raises_value_error() -> None:
    text = "[1, 2, 3]"
    with pytest.raises(ValueError, match="dictionary"):
        extract_json(text)
