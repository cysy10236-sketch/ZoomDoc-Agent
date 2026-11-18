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

    # Milestone 3 parameters
    max_leaf_pages: int = Field(20, ge=1, le=20)
    max_nav_rounds: int = Field(3, ge=1)
    max_nav_steps: int = Field(4, ge=1)
    use_tree_index: bool = False

    runs_dir: str = "runs"

    # Milestone 5 & 6 (FoveaDoc & Cache) parameters
    zoom_mode: Literal["crop", "fovea"] = "crop"
    fovea_header_ratio: float = Field(0.12, ge=0.0, le=0.35)
    fovea_stub_ratio: float = Field(0.15, ge=0.0, le=0.40)
    fovea_focus_share: float = Field(0.65, ge=0.40, le=0.85)
    fovea_anchor_share: float = Field(0.15, ge=0.0, le=0.30)
    fovea_auto_anchors: bool = False
    fovea_adaptive_aspect: bool = False

    enable_logprobs_entropy: bool = False
    enable_sboed_gate: bool = False
    entropy_zoom_threshold: float = Field(0.15, ge=0.0, le=5.0)
    force_zoom_on_alnum_code: bool = False

    enable_residual_verification: bool = False
    residual_max_optical_distance: float = Field(1.5, ge=0.0, le=3.0)

    enable_tree_flattening: bool = False
    tree_flatten_max_nodes: int = Field(40, ge=1)
    annotate_seeker_grid: bool = False

    enable_content_cache: bool = False
    cache_dir: str = ".cache/zoomdoc"
    redis_url: str | None = None


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
