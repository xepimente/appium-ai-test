"""Job / JobResult schemas — the scheduler contract.

See docs/EXECUTOR_PAYLOAD.md for the authoritative version of this contract.
Changes here must stay in sync with that document.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ── Job (scheduler → executor) ────────────────────────────────────────────────


class ProxyConfig(BaseModel):
    country: str = "us"
    zip: str = "10001"
    session_duration: int = 30
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timezone: Optional[str] = None


class Job(BaseModel):
    job_id: str = Field(..., description="Scheduler's unique job id (echoed back)")
    client_id: int
    business_id: int
    keyword_id: int
    keyword_text: str
    platform: Literal["ChatGPT", "Gemini", "Perplexity"]

    prompt: str
    follow_up: Optional[str] = None
    backlinks: List[str] = Field(default_factory=list)

    proxy: Optional[ProxyConfig] = None
    device_id: Optional[str] = Field(
        default=None,
        description="Pin to a specific device_id from active_devices.json; "
                    "omit to auto-pick the first free device from the pool.",
    )


# ── JobResult (executor → scheduler) ──────────────────────────────────────────


class ProxyResolved(BaseModel):
    status: str = "SKIPPED"
    gost_endpoint: Optional[str] = Field(
        default=None,
        description="host:port of the Mac-side gost listener the phone connected to",
    )
    upstream_session: Optional[str] = None
    exit_ip: Optional[str] = None
    exit_city: Optional[str] = None
    exit_region: Optional[str] = None
    exit_zip: Optional[str] = None
    mocked_latitude: Optional[float] = None
    mocked_longitude: Optional[float] = None
    mocked_timezone: Optional[str] = None


class JobResult(BaseModel):
    # Echoed Job fields
    job_id: str
    client_id: int
    business_id: int
    keyword_id: int
    keyword_text: str
    platform: str
    prompt: str
    follow_up: Optional[str] = None
    backlinks: List[str] = Field(default_factory=list)
    proxy: Optional[ProxyConfig] = None
    device_id: Optional[str] = None

    # Execution
    status: Literal["success", "error"]
    error: Optional[str] = None
    started_at: str
    finished_at: str
    duration_s: float

    # Device that ran it
    device_serial: Optional[str] = None

    # Resolved proxy (null when proxy was null in Job)
    proxy_resolved: Optional[ProxyResolved] = None

    # Evidence
    response_preview: Optional[str] = None
    backlink_clicked: Optional[str] = None

    # Trace
    steps: List[str] = Field(default_factory=list)


# ── Control endpoints ─────────────────────────────────────────────────────────


class InFlightJob(BaseModel):
    job_id: str
    device_id: Optional[str]
    platform: str
    client_id: int
    started_at: str


class HealthResponse(BaseModel):
    status: str
    version: str
    adb_devices_connected: int
    device_pool_size: int
    in_flight: int
    available: int


class StatusResponse(BaseModel):
    in_flight: List[InFlightJob] = Field(default_factory=list)
