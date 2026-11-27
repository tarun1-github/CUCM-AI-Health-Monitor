"""Configurable threshold evaluation engine."""

from typing import Any, Dict, Optional

from backend.models.health import HealthStatus
from backend.utils.config_loader import load_settings_yaml


class ThresholdEngine:
    """Evaluates numeric metrics against configurable warning/critical thresholds."""

    DEFAULT_THRESHOLDS = {
        "cpu_utilization": {"warning": 75.0, "critical": 90.0},
        "memory_utilization": {"warning": 80.0, "critical": 92.0},
        "disk_utilization": {"warning": 80.0, "critical": 90.0},
        "load_average_1m": {"warning": 4.0, "critical": 8.0},
    }

    def __init__(self, thresholds: Optional[Dict[str, Dict[str, float]]] = None):
        yaml_cfg = load_settings_yaml()
        self.thresholds = thresholds or yaml_cfg.get("thresholds", self.DEFAULT_THRESHOLDS)

    def evaluate(self, metric_key: str, value: Optional[float]) -> HealthStatus:
        """Classify a metric value; missing values are always UNKNOWN."""
        if value is None:
            return HealthStatus.UNKNOWN

        bounds = self.thresholds.get(metric_key) or self.DEFAULT_THRESHOLDS.get(metric_key)
        if not bounds:
            return HealthStatus.UNKNOWN

        crit = float(bounds.get("critical", 90.0))
        warn = float(bounds.get("warning", 75.0))

        if value >= crit:
            return HealthStatus.CRITICAL
        if value >= warn:
            return HealthStatus.WARNING
        return HealthStatus.HEALTHY
