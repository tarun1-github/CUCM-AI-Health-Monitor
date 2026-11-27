"""Health check execution, background task tracking, section retrieval, history, and AI summarization routes."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.ai.report_summarizer import ReportSummarizer
from backend.analyzers.health_analyzer import HealthAnalyzer
from backend.api.dependencies import ALL_SECTIONS, get_snapshot_store
from backend.collectors.cucm_health import CUCMHealthCollector
from backend.models.health import HealthSnapshot, SectionStatus, TaskProgressResponse
from backend.utils.config_loader import get_settings, load_commands_yaml

router = APIRouter(prefix="/api/health", tags=["health"])

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cucm-health")
_TASKS_LOCK = threading.RLock()
_TASKS: Dict[str, TaskProgressResponse] = {}
_CANCEL_EVENTS: Dict[str, threading.Event] = {}

_COMMAND_TO_SECTIONS: Dict[str, Set[str]] = {
    "show_version_active": {"system"},
    "show_status": {"system", "cpu", "memory", "disk"},
    "show_hardware": {"system", "disk"},
    "show_network_eth0": {"network"},
    "show_process_load": {"cpu", "memory"},
    "show_diskusage_common": {"disk"},
    "show_perf_processor": {"cpu"},
    "show_perf_memory": {"memory"},
    "utils_service_list": {"services"},
    "utils_diagnose_test": {"network"},
}


class RunHealthRequest(BaseModel):
    host: Optional[str] = None
    command_ids: Optional[List[str]] = None
    llm_provider: Optional[str] = None


def _infer_updated_sections(snapshot: HealthSnapshot, command_ids: Optional[List[str]]) -> tuple[Set[str], Dict[str, str]]:
    """Determine which sections were updated and which failed."""
    updated: Set[str] = {"connectivity"}
    errors: Dict[str, str] = {}

    if not snapshot.connectivity.ssh_connected and snapshot.connectivity.ssh_error:
        errors["connectivity"] = snapshot.connectivity.ssh_error

    if command_ids:
        for cid in command_ids:
            updated.update(_COMMAND_TO_SECTIONS.get(cid, set()))
    else:
        updated.update(ALL_SECTIONS)

    if snapshot.connectivity.axl_connected:
        updated.add("cluster")
    elif snapshot.connectivity.axl_error and not command_ids:
        errors["cluster"] = snapshot.connectivity.axl_error

    for cmd in snapshot.commands:
        if not cmd.success and not cmd.skipped and cmd.error:
            for sec in _COMMAND_TO_SECTIONS.get(cmd.command_id, set()):
                # Only mark section failed if no other command for that section succeeded
                sec_ok = any(
                    c.success and sec in _COMMAND_TO_SECTIONS.get(c.command_id, set())
                    for c in snapshot.commands
                )
                if not sec_ok:
                    errors[sec] = cmd.error

    return updated, errors


def _finalize_snapshot(
    partial: HealthSnapshot,
    command_ids: Optional[List[str]],
    llm_provider: Optional[str] = None,
) -> HealthSnapshot:
    store = get_snapshot_store()
    updated_sections, section_errors = _infer_updated_sections(partial, command_ids)

    if command_ids:
        snapshot = store.merge_partial(partial, updated_sections=updated_sections, section_errors=section_errors)
    else:
        snapshot = partial
        now = snapshot.collected_at or datetime.now(timezone.utc)
        for sec in ALL_SECTIONS:
            if sec in section_errors:
                snapshot.sections[sec] = SectionStatus(
                    section=sec,
                    status="FAILED",
                    last_attempted_at=now,
                    error=section_errors[sec],
                    data_freshness="Failed",
                )
            elif sec != "ai_summary":
                snapshot.sections[sec] = SectionStatus(
                    section=sec,
                    status="COLLECTED",
                    last_successful_at=now,
                    last_attempted_at=now,
                    data_freshness="Live",
                )

    analyzer = HealthAnalyzer()
    snapshot = analyzer.analyze(snapshot, persist=False)
    summarizer = ReportSummarizer()
    snapshot = summarizer.summarize(snapshot, provider=llm_provider)
    now_ai = datetime.now(timezone.utc)
    snapshot.sections["ai_summary"] = SectionStatus(
        section="ai_summary",
        status="COLLECTED" if snapshot.ai_summary.overall_assessment else "FAILED",
        last_successful_at=now_ai if snapshot.ai_summary.overall_assessment else None,
        last_attempted_at=now_ai,
        data_freshness="Live",
    )
    # Recompute score completion_pct now that ai_summary section is marked
    snapshot = analyzer.analyze(snapshot, persist=False)
    analyzer._save_processed(snapshot)
    store.add(snapshot)
    return snapshot


@router.get("/commands")
def list_commands() -> List[Dict[str, Any]]:
    return load_commands_yaml()


@router.get("/config-summary")
def get_config_summary() -> Dict[str, Any]:
    s = get_settings()
    return {
        "app_env": s.app_env,
        "cucm_host": s.cucm_host,
        "cucm_ssh_port": s.cucm_ssh_port,
        "cucm_axl_port": s.cucm_port,
        "llm_provider": s.llm_provider,
        "verify_ssl": s.effective_verify_ssl,
        "strict_host_key": s.effective_strict_host_key,
        "dashboard_public_url": s.dashboard_public_url,
    }


@router.post("/run", response_model=HealthSnapshot)
def run_health_check(req: RunHealthRequest | None = None) -> HealthSnapshot:
    req = req or RunHealthRequest()
    collector = CUCMHealthCollector()
    partial = collector.collect(host=req.host, command_ids=req.command_ids)
    return _finalize_snapshot(partial, command_ids=req.command_ids, llm_provider=req.llm_provider)


@router.post("/full", response_model=TaskProgressResponse)
def start_full_health_check(req: RunHealthRequest | None = None) -> TaskProgressResponse:
    req = req or RunHealthRequest()
    task_id = uuid.uuid4().hex[:12]
    cancel_event = threading.Event()
    enabled_count = sum(1 for c in load_commands_yaml() if c.get("enabled", True))
    task = TaskProgressResponse(
        task_id=task_id,
        status="queued",
        progress_percent=0,
        current_step="Queued",
        completed_steps=0,
        total_steps=enabled_count + 1,
        message="Full CUCM health check queued",
    )
    with _TASKS_LOCK:
        _TASKS[task_id] = task
        _CANCEL_EVENTS[task_id] = cancel_event

    def _worker() -> None:
        with _TASKS_LOCK:
            _TASKS[task_id].status = "running"
            _TASKS[task_id].current_step = "Starting collection"
            _TASKS[task_id].message = "Initializing CUCM health check"

        def _on_progress(completed: int, total: int, step_label: str, msg: str) -> None:
            pct = min(95, int((completed / max(total, 1)) * 95))
            with _TASKS_LOCK:
                t = _TASKS[task_id]
                t.completed_steps = completed
                t.total_steps = total
                t.progress_percent = pct
                t.current_step = step_label
                t.message = msg

        try:
            collector = CUCMHealthCollector()
            partial = collector.collect(
                host=req.host,
                command_ids=req.command_ids,
                progress_callback=_on_progress,
                cancel_event=cancel_event,
            )
            with _TASKS_LOCK:
                _TASKS[task_id].current_step = "Analyzing & summarizing"
                _TASKS[task_id].message = "Computing health score and AI summary"
                _TASKS[task_id].progress_percent = 97

            final_snap = _finalize_snapshot(partial, command_ids=req.command_ids, llm_provider=req.llm_provider)
            failed_cmds = [c for c in final_snap.commands if not c.success and not c.skipped]
            is_cancelled = cancel_event.is_set()
            final_status = (
                "cancelled"
                if is_cancelled
                else "failed"
                if (not final_snap.connectivity.ssh_connected and not final_snap.connectivity.axl_connected)
                else "partial"
                if failed_cmds
                else "completed"
            )
            with _TASKS_LOCK:
                t = _TASKS[task_id]
                t.status = final_status
                t.cancelled = is_cancelled
                t.progress_percent = 100
                t.completed_steps = t.total_steps
                t.snapshot_id = final_snap.snapshot_id
                t.current_step = "Completed" if final_status == "completed" else final_status.title()
                t.message = (
                    f"Health check {final_status} ({len(failed_cmds)} command issue(s))"
                    if failed_cmds
                    else "Health check completed"
                )
        except Exception as exc:
            with _TASKS_LOCK:
                t = _TASKS[task_id]
                t.status = "failed"
                t.error = str(exc)
                t.current_step = "Failed"
                t.message = f"Health check failed: {exc}"

    _EXECUTOR.submit(_worker)
    return task


@router.get("/tasks/{task_id}", response_model=TaskProgressResponse)
def get_health_task_status(task_id: str) -> TaskProgressResponse:
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
        return task.model_copy()


@router.post("/tasks/{task_id}/cancel", response_model=TaskProgressResponse)
def cancel_health_task(task_id: str) -> TaskProgressResponse:
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
        ev = _CANCEL_EVENTS.get(task_id)
        if ev:
            ev.set()
        task.cancelled = True
        task.message = "Cancellation requested"
        return task.model_copy()


@router.post("/summarize", response_model=HealthSnapshot)
def regenerate_summary(llm_provider: Optional[str] = None) -> HealthSnapshot:
    store = get_snapshot_store()
    latest = store.latest()
    if latest is None:
        raise HTTPException(status_code=404, detail="No health snapshot available yet.")
    summarizer = ReportSummarizer()
    updated = summarizer.summarize(latest, provider=llm_provider)
    now_ai = datetime.now(timezone.utc)
    updated.sections["ai_summary"] = SectionStatus(
        section="ai_summary",
        status="COLLECTED",
        last_successful_at=now_ai,
        last_attempted_at=now_ai,
        data_freshness="Live",
    )
    HealthAnalyzer._save_processed(updated)
    return updated


@router.get("/latest", response_model=Optional[HealthSnapshot])
def get_latest_health() -> Optional[HealthSnapshot]:
    return get_snapshot_store().latest()


@router.get("/system")
def get_system_health() -> Dict[str, Any]:
    latest = get_snapshot_store().latest()
    if latest is None:
        return {"status": "NOT COLLECTED", "system": None, "sections": {}}
    return {
        "snapshot_id": latest.snapshot_id,
        "collected_at": latest.collected_at.isoformat(),
        "system": latest.system.model_dump(),
        "classification": {
            "cpu_health": latest.classification.cpu_health.value,
            "memory_health": latest.classification.memory_health.value,
            "disk_health": latest.classification.disk_health.value,
        },
        "sections": {k: v.model_dump() for k, v in latest.sections.items() if k in ("system", "cpu", "memory", "disk")},
    }


@router.get("/services")
def get_services_health() -> Dict[str, Any]:
    latest = get_snapshot_store().latest()
    if latest is None:
        return {"status": "NOT COLLECTED", "services": []}
    return {
        "snapshot_id": latest.snapshot_id,
        "collected_at": latest.collected_at.isoformat(),
        "services_health": latest.classification.services_health.value,
        "services": [s.model_dump() for s in latest.services],
        "section": latest.sections.get("services").model_dump() if "services" in latest.sections else None,
    }


@router.get("/network")
def get_network_health() -> Dict[str, Any]:
    latest = get_snapshot_store().latest()
    if latest is None:
        return {"status": "NOT COLLECTED", "network": None}
    return {
        "snapshot_id": latest.snapshot_id,
        "collected_at": latest.collected_at.isoformat(),
        "network_health": latest.classification.network_health.value,
        "network": latest.network.model_dump(),
        "section": latest.sections.get("network").model_dump() if "network" in latest.sections else None,
    }


@router.get("/cluster")
def get_cluster_health() -> Dict[str, Any]:
    latest = get_snapshot_store().latest()
    if latest is None:
        return {"status": "NOT COLLECTED", "cluster": None}
    return {
        "snapshot_id": latest.snapshot_id,
        "collected_at": latest.collected_at.isoformat(),
        "cluster": latest.cluster.model_dump(),
        "section": latest.sections.get("cluster").model_dump() if "cluster" in latest.sections else None,
    }


@router.get("/history")
def get_health_history() -> List[Dict[str, Any]]:
    history = get_snapshot_store().history()
    return [
        {
            "snapshot_id": s.snapshot_id,
            "collected_at": s.collected_at.isoformat(),
            "overall_health": s.classification.overall_health.value,
            "health_score": s.classification.score.health_score,
            "data_coverage_pct": s.classification.score.data_coverage_pct,
            "cpu_pct": s.system.cpu_utilization_pct,
            "memory_pct": s.system.memory_utilization_pct,
            "disk_pct": s.system.disk_utilization_pct,
            "is_simulated": s.is_simulated,
        }
        for s in history
    ]
