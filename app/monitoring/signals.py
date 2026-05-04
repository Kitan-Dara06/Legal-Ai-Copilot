import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

import sentry_sdk


Sample = Tuple[float, float, int]  # (timestamp_s, duration_ms, status_code)


def _percentile(sorted_values: list[float], p: float) -> Optional[float]:
    if not sorted_values:
        return None
    if p <= 0:
        return sorted_values[0]
    if p >= 1:
        return sorted_values[-1]
    k = (len(sorted_values) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


@dataclass
class ScaleSignalSnapshot:
    window_seconds: int
    in_flight: int
    p50_latency_ms: Optional[float]
    p95_latency_ms: Optional[float]
    rps: float
    error_rate_5xx: float
    scale_needed: bool
    reasons: list[str]
    suggested_uvicorn_workers: Optional[int]


class ApiSignals:
    """
    Minimal, in-process signals for deciding when API worker count is too low.

    This is intentionally dependency-free (no Prometheus required) and can emit a
    warning to Sentry when sustained pressure is detected.
    """

    def __init__(self) -> None:
        self._window_s = int(os.getenv("SIGNALS_WINDOW_SECONDS", "300"))
        self._emit_cooldown_s = int(os.getenv("SIGNALS_SENTRY_COOLDOWN_SECONDS", "60"))

        self._p95_limit_ms = float(os.getenv("SIGNALS_P95_LIMIT_MS", "900"))
        self._in_flight_limit = int(os.getenv("SIGNALS_IN_FLIGHT_LIMIT", "60"))
        self._error_rate_limit = float(os.getenv("SIGNALS_5XX_RATE_LIMIT", "0.05"))

        # Rough rule-of-thumb: "comfortable" concurrent requests per Uvicorn worker.
        self._target_in_flight_per_worker = int(os.getenv("SIGNALS_TARGET_IN_FLIGHT_PER_WORKER", "25"))

        self._samples: Deque[Sample] = deque()
        self._in_flight = 0
        self._last_sentry_emit_at = 0.0

    def on_request_start(self) -> None:
        self._in_flight += 1

    def on_request_end(self, duration_ms: float, status_code: int) -> None:
        now = time.time()
        self._samples.append((now, duration_ms, status_code))
        self._in_flight = max(0, self._in_flight - 1)
        self._purge(now)

    def snapshot(self) -> ScaleSignalSnapshot:
        now = time.time()
        self._purge(now)

        durations = [d for (_ts, d, _sc) in self._samples]
        durations.sort()
        p50 = _percentile(durations, 0.50)
        p95 = _percentile(durations, 0.95)

        total = len(self._samples)
        window = max(1, self._window_s)
        rps = total / window

        errors_5xx = sum(1 for (_ts, _d, sc) in self._samples if 500 <= sc <= 599)
        error_rate = (errors_5xx / total) if total else 0.0

        reasons: list[str] = []
        if p95 is not None and p95 >= self._p95_limit_ms:
            reasons.append(f"p95_latency_ms={p95:.0f}>=limit({self._p95_limit_ms:.0f})")
        if self._in_flight >= self._in_flight_limit:
            reasons.append(f"in_flight={self._in_flight}>=limit({self._in_flight_limit})")
        if error_rate >= self._error_rate_limit and total >= 20:
            reasons.append(f"error_rate_5xx={error_rate:.3f}>=limit({self._error_rate_limit:.3f})")

        scale_needed = len(reasons) > 0

        suggested_workers: Optional[int] = None
        if self._target_in_flight_per_worker > 0:
            suggested_workers = max(1, (self._in_flight + self._target_in_flight_per_worker - 1) // self._target_in_flight_per_worker)

        snap = ScaleSignalSnapshot(
            window_seconds=self._window_s,
            in_flight=self._in_flight,
            p50_latency_ms=p50,
            p95_latency_ms=p95,
            rps=rps,
            error_rate_5xx=error_rate,
            scale_needed=scale_needed,
            reasons=reasons,
            suggested_uvicorn_workers=suggested_workers,
        )

        if scale_needed:
            self._maybe_emit_sentry(snap)

        return snap

    def _purge(self, now: float) -> None:
        cutoff = now - self._window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def _maybe_emit_sentry(self, snap: ScaleSignalSnapshot) -> None:
        dsn = os.getenv("SENTRY_DSN")
        if not dsn:
            return
        now = time.time()
        if (now - self._last_sentry_emit_at) < self._emit_cooldown_s:
            return

        self._last_sentry_emit_at = now
        sentry_sdk.capture_message(
            "API scale signal triggered",
            level="warning",
        )
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("monitoring.kind", "scale_signal")
            scope.set_extra("window_seconds", snap.window_seconds)
            scope.set_extra("in_flight", snap.in_flight)
            scope.set_extra("p50_latency_ms", snap.p50_latency_ms)
            scope.set_extra("p95_latency_ms", snap.p95_latency_ms)
            scope.set_extra("rps", snap.rps)
            scope.set_extra("error_rate_5xx", snap.error_rate_5xx)
            scope.set_extra("reasons", snap.reasons)
            scope.set_extra("suggested_uvicorn_workers", snap.suggested_uvicorn_workers)
            sentry_sdk.capture_message("API pressure details", level="warning")

