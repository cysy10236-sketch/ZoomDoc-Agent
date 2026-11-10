from __future__ import annotations

import functools
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BBoxFormat = Literal["xyxy_1000", "yxyx_1000", "xyxy_abs"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZOOMDOC_", env_file=".env", extra="forbid")

    vlm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vlm_api_key: str = "mock-key"
    vlm_model: str = "qwen2.5-vl-72b-instruct"
    vlm_seed: int = 42
    vlm_max_tokens: int = 1024
    vlm_timeout_s: float = 120.0
    bbox_format: BBoxFormat = "xyxy_1000"
    image_encoding: Literal["png", "jpeg"] = "png"
    jpeg_quality: int = Field(90, ge=50, le=100)

    source_dpi: int = Field(200, ge=72, le=400)
    view_long_side: int = Field(1024, ge=256)
    thumb_long_side: int = Field(448, ge=128)
    grid_line_width: int = 40

    zoom_pad_ratio: float = Field(0.10, ge=0.0, le=1.0)
    zoom_min_norm_side: float = Field(0.05, gt=0.0, le=1.0)
    zoom_target_long_side: int = 1024
    zoom_max_upscale: float = 4.0

    max_pages: int = 20
    max_steps: int = 8
    max_zooms: int = 3
    max_inspector_images: int = 6
    max_attempts: int = 3

    # Milestone 2 parameters
    max_inspect_rounds: int = 2
    max_seek_rounds: int = 2
    max_sub_steps: int = 6
    critic_min_confidence: float = Field(0.6, ge=0.0, le=1.0)
    keep_sub_artifacts: bool = True

    runs_dir: str = "runs"


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
