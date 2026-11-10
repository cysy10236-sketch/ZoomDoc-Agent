from __future__ import annotations

import base64
import io
import time
from pathlib import Path
from typing import Annotated, Literal, Protocol, Union

from openai import OpenAI
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
        completion = self.client.chat.completions.create(
            model=self.settings.vlm_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.0,
            seed=self.settings.vlm_seed,
            max_tokens=self.settings.vlm_max_tokens,
        )
        latency_ms = (time.perf_counter() - start_time) * 1000.0

        choice = completion.choices[0]
        text_output = choice.message.content or ""

        usage = completion.usage
        vlm_usage = VLMUsage(
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            latency_ms=latency_ms,
            model=self.settings.vlm_model,
        )

        return VLMResponse(text=text_output, usage=vlm_usage)
