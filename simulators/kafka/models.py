"""Shared configuration models for the machine simulators.

This file is copied verbatim across the simulator apps (opcua, mqtt, kafka, ...);
keep changes in sync. Only the protocol section of each app's settings.py differs.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Waveform = Literal["sine", "ramp", "square", "random_walk", "random", "constant"]
TagType = Literal["float", "int", "bool"]

_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")


class Simulation(BaseModel):
    tick: float = Field(default=1.0, gt=0)  # seconds between value updates
    seed: int = 42                          # per-asset seeds derive from this


class TagSpec(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    waveform: Waveform = "constant"
    type: TagType = "float"
    min: float = 0.0
    max: float = 100.0
    period: float = Field(default=60.0, gt=0)   # seconds per cycle (sine/ramp/square)
    noise: float = Field(default=0.0, ge=0)     # gaussian noise on top of the waveform
    initial: Optional[float | bool] = None      # starting value; the whole story for constant/writable
    writable: bool = False                      # True -> the simulator never touches it; clients write it
    unit: Optional[str] = None
    description: Optional[str] = None

    @model_validator(mode="after")
    def _validate(self) -> "TagSpec":
        if self.max <= self.min:
            raise ValueError(f"max ({self.max}) must be greater than min ({self.min})")
        if self.writable and self.waveform != "constant":
            raise ValueError("writable tags cannot have a waveform (the simulator never updates them)")
        # bool tags can only alternate (square), pick at random, or hold a value
        if self.type == "bool" and self.waveform not in ("square", "random", "constant"):
            raise ValueError(f"bool tags support square/random/constant waveforms, not '{self.waveform}'")
        return self


class AssetGroup(BaseModel):
    """A template for `count` identical assets sharing one tag set."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1)     # asset type / instance prefix, e.g. "BeamPump"
    count: int = Field(default=1, ge=1)
    tags: dict[str, TagSpec] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _valid_asset_name(cls, v: str) -> str:
        # Asset names become identifiers on every protocol (NodeIds, topics, keys).
        if not _NAME_RE.fullmatch(v):
            raise ValueError(f"invalid asset name '{v}' (use letters, digits, '_', '.', '-')")
        return v

    @field_validator("tags")
    @classmethod
    def _valid_tag_names(cls, v: dict[str, TagSpec]) -> dict[str, TagSpec]:
        for name in v:
            if not _NAME_RE.fullmatch(name):
                raise ValueError(f"invalid tag name '{name}' (use letters, digits, '_', '.', '-')")
        return v


def validate_unique_assets(assets: list[AssetGroup]) -> list[AssetGroup]:
    """Reject duplicate asset names across groups.

    Names become identifiers (lowercased on some protocols), so the check is
    case-insensitive. Called from each app's Settings model validator.

    Parameters:
        assets: The configured asset groups.

    Returns:
        The assets, unchanged, when all names are unique.

    Raises:
        ValueError: Naming the duplicated asset.
    """
    seen: dict[str, str] = {}
    for group in assets:
        key = group.name.lower()
        if key in seen:
            raise ValueError(f"duplicate asset name '{group.name}' (conflicts with '{seen[key]}')")
        seen[key] = group.name
    return assets
