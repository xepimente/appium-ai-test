"""FastAPI routes for the executor service.

Endpoints:
  POST /v1/jobs   — run one Job synchronously, return JobResult
  GET  /health    — liveness + device pool size + in-flight count
  GET  /status    — list of in-flight jobs

Concurrency: N jobs can run simultaneously (one per device). 2nd job on a
busy device returns 409. See docs/EXECUTOR_PAYLOAD.md for Job/JobResult.
"""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from .models import (
    HealthResponse,
    InFlightJob,
    Job,
    JobResult,
    StatusResponse,
)
from ..executor.runner import (
    DeviceBusy,
    DeviceNotInPool,
    DeviceUnreachable,
    NoFreeDevice,
    device_pool_size,
    devices_available,
    execute_job,
    in_flight_jobs,
)

router = APIRouter()

VERSION = "2.2.0"


# ── Per-HTTP-request metadata (for /status) ────────────────────────────────────
# Keyed by job_id so /status can surface what the scheduler pushed. Distinct
# from the runner's _devices_in_use which is keyed by device_id.

_request_meta: Dict[str, Dict[str, Any]] = {}
_request_meta_lock = threading.Lock()


def _register(job: Job) -> None:
    with _request_meta_lock:
        _request_meta[job.job_id] = {
            "job_id":        job.job_id,
            "device_id":     job.device_id,
            "device_serial": job.device_serial,
            "platform":      job.platform,
            "client_id":     job.client_id,
            "started_at":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


def _deregister(job_id: str) -> None:
    with _request_meta_lock:
        _request_meta.pop(job_id, None)


def _snapshot_meta() -> List[Dict[str, Any]]:
    with _request_meta_lock:
        return list(_request_meta.values())


# ── Helpers ───────────────────────────────────────────────────────────────────


def _adb_device_count() -> int:
    try:
        result = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5,
        )
        return sum(1 for line in result.stdout.splitlines()[1:] if "\tdevice" in line)
    except Exception:
        return 0


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    in_flight = len(in_flight_jobs())
    return HealthResponse(
        status="healthy",
        version=VERSION,
        adb_devices_connected=_adb_device_count(),
        device_pool_size=device_pool_size(),
        in_flight=in_flight,
        available=devices_available(),
    )


@router.get("/status", response_model=StatusResponse)
async def status() -> StatusResponse:
    return StatusResponse(in_flight=[InFlightJob(**m) for m in _snapshot_meta()])


@router.post("/v1/jobs", response_model=JobResult)
async def run_job(job: Job) -> JobResult:
    """Run one Job. Blocks until the session finishes (sync HTTP).

    Returns 200 with a structured JobResult when the job completed, even if
    `status: error` — HTTP 200 means "executor ran it and has a result for you".

    Returns 4xx at the HTTP layer only for pre-execution errors:
      - 400: device_id doesn't match pool; or device_serial is unreachable via ADB
      - 409: target device currently running another job
      - 503: no free devices and auto-pick pool is empty
    """
    _register(job)
    try:
        try:
            result = execute_job(job.model_dump())
        except DeviceBusy as e:
            raise HTTPException(status_code=409, detail=str(e))
        except DeviceNotInPool as e:
            raise HTTPException(status_code=400, detail=str(e))
        except DeviceUnreachable as e:
            raise HTTPException(status_code=400, detail=str(e))
        except NoFreeDevice as e:
            raise HTTPException(status_code=503, detail=str(e))

        return JobResult(**result)
    finally:
        _deregister(job.job_id)
