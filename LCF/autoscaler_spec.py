"""Helpers for parsing and validating autoscaler specification strings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AutoscalerSpec:
    min_replicas: int
    max_replicas: int
    metric: str
    target: int
    cooldown_seconds: int


def parse_autoscaler_spec(spec: str) -> AutoscalerSpec:
    """Parse CloudBrew autoscaler syntax: ``min:max@metric:target,cooldown``."""
    if "@" not in spec or ":" not in spec or "," not in spec:
        raise ValueError("Invalid autoscaler spec. Expected format min:max@metric:target,cooldown")

    range_part, rule_part = spec.split("@", 1)
    metric_target, cooldown = rule_part.split(",", 1)
    min_str, max_str = range_part.split(":", 1)
    metric, target = metric_target.split(":", 1)

    return AutoscalerSpec(
        min_replicas=int(min_str),
        max_replicas=int(max_str),
        metric=metric.strip(),
        target=int(target),
        cooldown_seconds=int(cooldown),
    )
