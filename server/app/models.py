import math
import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MIN_C, MAX_C = -200.0, 850.0  # the firmware's own valid range for a PT100/PT1000
MAX_FUTURE = timedelta(minutes=5)
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class Channel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ch: int = Field(ge=1, le=8)
    type: str
    temp_c: float | None

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in ("PT100", "PT1000"):
            raise ValueError("type must be PT100 or PT1000")
        return v

    @field_validator("temp_c")
    @classmethod
    def _range(cls, v: float | None) -> float | None:
        if v is not None and (not math.isfinite(v) or not MIN_C <= v <= MAX_C):
            raise ValueError("temp_c out of range")
        return v


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ts: str
    channels: list[Channel]

    @field_validator("ts")
    @classmethod
    def _ts(cls, v: str) -> str:
        if not _TS_RE.match(v):
            raise ValueError("ts must look like 2026-09-09T14:32:10Z")
        parsed = datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if parsed > datetime.now(timezone.utc) + MAX_FUTURE:
            raise ValueError("ts is in the future")
        return v

    @model_validator(mode="after")
    def _eight_channels(self) -> "Row":
        if [c.ch for c in self.channels] != list(range(1, 9)):
            raise ValueError("channels must be ch 1..8 in order")
        return self


class Envelope(BaseModel):
    """Top level of the POST body. Rows stay raw here so one bad row only rejects that row."""

    model_config = ConfigDict(extra="ignore")
    device_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    fw_version: str = Field(min_length=1, max_length=32)
    readings: list[dict] = Field(min_length=1, max_length=100)
