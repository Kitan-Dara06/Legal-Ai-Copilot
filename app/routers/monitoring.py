from fastapi import APIRouter, Request


router = APIRouter(prefix="/monitoring", tags=["Monitoring"])


@router.get("/signals")
async def signals(request: Request):
    """
    Lightweight scaling signals for the HTTP API worker pool.
    """
    api_signals = getattr(request.app.state, "api_signals", None)
    if not api_signals:
        return {"enabled": False}

    snap = api_signals.snapshot()
    return {
        "enabled": True,
        "window_seconds": snap.window_seconds,
        "in_flight": snap.in_flight,
        "rps": round(snap.rps, 3),
        "p50_latency_ms": None if snap.p50_latency_ms is None else round(snap.p50_latency_ms, 1),
        "p95_latency_ms": None if snap.p95_latency_ms is None else round(snap.p95_latency_ms, 1),
        "error_rate_5xx": round(snap.error_rate_5xx, 4),
        "scale_needed": snap.scale_needed,
        "reasons": snap.reasons,
        "suggested_uvicorn_workers": snap.suggested_uvicorn_workers,
    }

