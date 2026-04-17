"""FastAPI routes — POST /execute, GET /health, GET /status."""

import subprocess
import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from .models import ExecuteRequest, ExecuteResponse, HealthResponse, StatusResponse
from ..executor.runner import execute_task

router = APIRouter()

# Track current execution state
_current_task: Optional[Dict[str, Any]] = None
_task_lock = threading.Lock()

VERSION = "1.0.0"


@router.get("/health", response_model=HealthResponse)
async def health():
    """Health check — returns service status and connected device count."""
    try:
        result = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5,
        )
        device_count = sum(
            1 for line in result.stdout.splitlines()[1:]
            if "\tdevice" in line
        )
    except Exception:
        device_count = 0

    return HealthResponse(
        status="healthy",
        version=VERSION,
        adb_devices_connected=device_count,
    )


@router.get("/status", response_model=StatusResponse)
async def status():
    """Current execution status."""
    with _task_lock:
        if _current_task:
            return StatusResponse(busy=True, current_task=_current_task)
        return StatusResponse(busy=False)


@router.post("/execute", response_model=ExecuteResponse)
async def execute(request: ExecuteRequest):
    """Execute a phone automation task on a specific device."""
    with _task_lock:
        if _current_task:
            raise HTTPException(
                status_code=409,
                detail=f"Busy — currently executing on {_current_task.get('device_serial')}",
            )
        _current_task_info = {
            "type": request.type,
            "device_serial": request.device_serial,
            "platforms": request.platforms,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    # Build payload dict from request
    payload = {
        "type": request.type,
        "device_serial": request.device_serial,
        "serial": request.serial,
        "platforms": request.platforms,
        "client": request.client.model_dump(),
        "keyword": request.keyword.model_dump(),
        "prompt": request.prompt,
        "follow_up": request.follow_up,
        "backlinks": request.backlinks,
        "use_adb": request.use_adb,
        "port": request.port,
    }

    try:
        with _task_lock:
            globals()["_current_task"] = _current_task_info

        result = execute_task(payload)

        return ExecuteResponse(
            status=result.status,
            type=result.task_type,
            device_serial=result.device_serial,
            duration_seconds=result.duration_seconds,
            results=[r.to_dict() for r in result.results],
            proxy=result.proxy,
            error=result.error,
        )
    finally:
        with _task_lock:
            globals()["_current_task"] = None
