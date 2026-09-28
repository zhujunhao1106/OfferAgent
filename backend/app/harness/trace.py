"""Harness trace events mirroring Go harness/trace.go."""
from __future__ import annotations

from dataclasses import dataclass

from .. import executiontrace


TRACE_QUEUED = "queued"
TRACE_STARTED = "started"
TRACE_SUCCEEDED = "succeeded"
TRACE_ERROR = "error"


@dataclass
class TraceEvent:
    trace_id: str
    agent_id: str
    type: str
    at: float
    tool_name: str = ""
    wait: float = 0.0
    duration: float = 0.0
    error: str = ""


def emit_trace_event(runtime, event: TraceEvent) -> None:
    """Append to the ring buffer, forward to the sink, and mirror into executiontrace."""
    runtime._append_trace(event)
    if runtime._trace_sink is not None:
        try:
            runtime._trace_sink(event)
        except Exception:
            pass

    if event.type == TRACE_QUEUED:
        status = executiontrace.Status.QUEUED
    elif event.type == TRACE_STARTED:
        status = executiontrace.Status.RUNNING
    elif event.type == TRACE_SUCCEEDED:
        status = executiontrace.Status.COMPLETED
    else:
        status = executiontrace.Status.FAILED

    stage = "tool" if event.tool_name else "agent"
    label = "Run Function Tool" if event.tool_name else "Run Harness agent"
    request_event = executiontrace.Event(
        id=event.trace_id,
        stage=stage,
        label=label,
        status=status,
        agent=event.agent_id,
        at=executiontrace.now_iso(),
        duration_ms=int(event.duration * 1000),
    )
    if event.type == TRACE_ERROR:
        request_event.detail = "function tool call failed" if event.tool_name else "agent call failed"
    executiontrace.emit(request_event)
