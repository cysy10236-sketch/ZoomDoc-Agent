from __future__ import annotations

import base64
import io
import math
import re
import time
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, Union

from openai import APIStatusError, BadRequestError, OpenAI
from PIL import Image
from pydantic import BaseModel, Field

from zoomdoc.config import Settings
from zoomdoc.schemas import VLMUsage


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    type: Literal["image"] = "image"
    path: str  # 只接受本地图片文件绝对路径，避免在内存中传递大型 Base64 或 PIL 对象


Part = Annotated[Union[TextPart, ImagePart], Field(discriminator="type")]


class VLMResponse(BaseModel):
    text: str
    usage: VLMUsage


class VLMClient(Protocol):
    def generate(
        self, parts: list[Part], *, extra_messages: list[dict] | None = None
    ) -> VLMResponse: ...


def encode_image_file(path: str, fmt: Literal["png", "jpeg"] = "png", quality: int = 90) -> str:
    """Read image from path and return base64 data string (fixing ViDoRAG io.BytesIO bug)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Image file does not exist: {path}")

    # If raw format matches, read binary directly
    ext = p.suffix.lower().replace(".", "")
    if ext in ("png", "jpeg", "jpg"):
        with open(p, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    # Otherwise convert via PIL
    with Image.open(p) as img:
        rgb = img.convert("RGB")
        buf = io.BytesIO()
        if fmt == "png":
            rgb.save(buf, format="PNG")
        else:
            rgb.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode("utf-8")


def compute_answer_span_entropy(
    raw_text: str, token_items: list[dict[str, Any] | Any]
) -> tuple[float, float]:
    """Compute maximum and mean normalized Shannon entropy (in bits) over the 'answer' field tokens.

    token_items: list of token logprob items from OpenAI chat completion or fake test dicts.
    Each item has token string, logprob, and top_logprobs.
    """
    if not token_items:
        return 0.0, 0.0

    token_records: list[dict[str, Any]] = []
    for item in token_items:
        tok_str = item["token"] if isinstance(item, dict) else getattr(item, "token", "")
        top_list = (
            item.get("top_logprobs") if isinstance(item, dict) else getattr(item, "top_logprobs", None)
        )

        cand_logprobs: list[float] = []
        if top_list:
            for top in top_list:
                lp = top.get("logprob") if isinstance(top, dict) else getattr(top, "logprob", None)
                if lp is not None:
                    cand_logprobs.append(float(lp))
        else:
            main_lp = item.get("logprob") if isinstance(item, dict) else getattr(item, "logprob", None)
            if main_lp is not None:
                cand_logprobs.append(float(main_lp))

        if cand_logprobs:
            max_lp = max(cand_logprobs)
            exps = [math.exp(lp - max_lp) for lp in cand_logprobs]
            sum_exp = sum(exps)
            if sum_exp > 0:
                probs = [e / sum_exp for e in exps]
                ent = -sum(p * math.log2(p) for p in probs if p > 0)
            else:
                ent = 0.0
        else:
            ent = 0.0

        token_records.append({"token": tok_str, "entropy": ent})

    stream_text = "".join(rec["token"] for rec in token_records)
    ans_pattern = re.compile(r'"answer"\s*:\s*"((?:\\.|[^"\\])*)"')

    stream_matches = list(ans_pattern.finditer(stream_text))
    if stream_matches:
        ans_start, ans_end = stream_matches[-1].span(1)
    else:
        raw_matches = list(ans_pattern.finditer(raw_text))
        if raw_matches:
            ans_val = raw_matches[-1].group(1)
            idx = stream_text.rfind(ans_val) if ans_val else -1
            if idx != -1:
                ans_start, ans_end = idx, idx + len(ans_val)
            else:
                ans_start, ans_end = 0, len(stream_text)
        else:
            ans_start, ans_end = 0, len(stream_text)

    curr_pos = 0
    ans_entropies: list[float] = []
    all_alnum_entropies: list[float] = []

    for rec in token_records:
        tok_str = rec["token"]
        tok_start = curr_pos
        tok_end = tok_start + len(tok_str)
        curr_pos = tok_end

        is_alnum = any(ch.isalnum() for ch in tok_str)
        if is_alnum:
            all_alnum_entropies.append(rec["entropy"])

        overlap = max(tok_start, ans_start) < min(tok_end, ans_end)
        if overlap and is_alnum:
            ans_entropies.append(rec["entropy"])

    target_entropies = ans_entropies or all_alnum_entropies or [r["entropy"] for r in token_records]
    if not target_entropies:
        return 0.0, 0.0

    max_entropy = max(target_entropies)
    mean_entropy = sum(target_entropies) / len(target_entropies)
    return round(float(max_entropy), 4), round(float(mean_entropy), 4)


class OpenAICompatVLM:
    """Standard OpenAI-compatible Multi-modal client (supports Ali Bailian Qwen-VL, Moonshot Kimi, OpenAI)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = OpenAI(
            base_url=settings.vlm_base_url,
            api_key=settings.vlm_api_key,
            timeout=settings.vlm_timeout_s,
        )

    def generate(
        self, parts: list[Part], *, extra_messages: list[dict] | None = None
    ) -> VLMResponse:
        content_items: list[dict] = []
        for part in parts:
            if part.type == "text":
                content_items.append({"type": "text", "text": part.text})
            elif part.type == "image":
                b64 = encode_image_file(
                    part.path,
                    fmt=self.settings.image_encoding,
                    quality=self.settings.jpeg_quality,
                )
                mime = "image/png" if self.settings.image_encoding == "png" else "image/jpeg"
                content_items.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })

        messages = [{"role": "user", "content": content_items}]
        if extra_messages:
            messages.extend(extra_messages)

        start_time = time.perf_counter()
        req_kwargs: dict[str, Any] = {
            "model": self.settings.vlm_model,
            "messages": messages,
            "temperature": 0.0,
            "seed": self.settings.vlm_seed,
            "max_tokens": self.settings.vlm_max_tokens,
        }
        if getattr(self.settings, "enable_logprobs_entropy", False):
            req_kwargs["logprobs"] = True
            req_kwargs["top_logprobs"] = 5

        try:
            completion = self.client.chat.completions.create(**req_kwargs)
        except (BadRequestError, APIStatusError):
            if req_kwargs.get("logprobs"):
                req_kwargs.pop("logprobs", None)
                req_kwargs.pop("top_logprobs", None)
                completion = self.client.chat.completions.create(**req_kwargs)
            else:
                raise
        latency_ms = (time.perf_counter() - start_time) * 1000.0

        choice = completion.choices[0]
        text_output = choice.message.content or ""

        max_ent: float | None = None
        mean_ent: float | None = None
        if getattr(self.settings, "enable_logprobs_entropy", False):
            try:
                logprobs_obj = getattr(choice, "logprobs", None)
                content_list = getattr(logprobs_obj, "content", None) if logprobs_obj else None
                if content_list:
                    token_items: list[dict[str, Any]] = []
                    for item in content_list:
                        tok_str = getattr(item, "token", "")
                        tok_lp = getattr(item, "logprob", 0.0)
                        top_lps: list[dict[str, Any]] = []
                        for top in getattr(item, "top_logprobs", None) or []:
                            top_lps.append({
                                "token": getattr(top, "token", ""),
                                "logprob": getattr(top, "logprob", 0.0),
                            })
                        token_items.append({
                            "token": tok_str,
                            "logprob": tok_lp,
                            "top_logprobs": top_lps,
                        })
                    max_ent, mean_ent = compute_answer_span_entropy(text_output, token_items)
            except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
                max_ent, mean_ent = None, None

        usage = completion.usage
        vlm_usage = VLMUsage(
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            latency_ms=latency_ms,
            model=self.settings.vlm_model,
            max_token_entropy=max_ent,
            mean_token_entropy=mean_ent,
        )

        return VLMResponse(text=text_output, usage=vlm_usage)
