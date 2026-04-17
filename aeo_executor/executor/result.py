"""Result dataclasses for executor responses."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PlatformResult:
    platform: str
    status: str  # "success" | "error"
    steps: List[str] = field(default_factory=list)
    duration_s: float = 0.0
    error: Optional[str] = None
    # Audit only
    response_text: Optional[str] = None
    screenshot_path: Optional[str] = None
    ranking: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "platform": self.platform,
            "status": self.status,
            "steps": self.steps,
            "duration_s": self.duration_s,
            "error": self.error,
        }
        if self.response_text is not None:
            d["response_text"] = self.response_text
        if self.screenshot_path is not None:
            d["screenshot_path"] = self.screenshot_path
        if self.ranking is not None:
            d["ranking"] = self.ranking
        return d


@dataclass
class TaskResult:
    status: str  # "success" | "partial_success" | "failed"
    task_type: str
    device_serial: str
    duration_seconds: float = 0.0
    results: List[PlatformResult] = field(default_factory=list)
    proxy: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "type": self.task_type,
            "device_serial": self.device_serial,
            "duration_seconds": self.duration_seconds,
            "results": [r.to_dict() for r in self.results],
            "proxy": self.proxy,
            "error": self.error,
        }
