"""Pydantic request/response models for the API."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ClientPayload(BaseModel):
    name: str
    url: str = ""
    id: Optional[int] = None


class KeywordPayload(BaseModel):
    text: str
    city: str = ""
    state: str = ""
    id: Optional[int] = None


class ProxyPayload(BaseModel):
    username: str = ""
    session_id: str = ""


class ExecuteRequest(BaseModel):
    type: str = Field(..., description="Task type: 'audit' or 'session'")
    device_serial: str = Field(..., description="Full ADB transport name for ADB commands")
    serial: str = Field(default="", description="Short serial for Appium (auto-derived if empty)")
    platforms: List[str] = Field(
        default=["gemini", "chatgpt", "perplexity"],
        description="AI platforms to run",
    )
    client: ClientPayload
    keyword: KeywordPayload
    prompt: str = Field(default="", description="Prompt text (auto-generated for audit if empty)")
    follow_up: str = Field(default="", description="Followup prompt (session only)")
    backlinks: List[str] = Field(default_factory=list, description="Backlink URLs (session only)")
    use_adb: Optional[bool] = Field(default=None, description="Force ADB-only mode (auto-detected from brand if not set)")
    port: int = Field(default=4723, description="Appium server port")
    proxy: Optional[ProxyPayload] = None


class ExecuteResponse(BaseModel):
    status: str
    type: str
    device_serial: str
    duration_seconds: float
    results: List[Dict[str, Any]]
    proxy: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    adb_devices_connected: int


class StatusResponse(BaseModel):
    busy: bool
    current_task: Optional[Dict[str, Any]] = None
