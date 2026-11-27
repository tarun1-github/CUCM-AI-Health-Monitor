"""Shared state and dependency providers for FastAPI routes."""

import json
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from backend.models.health import HealthSnapshot, SectionStatus
from backend.utils.config_loader import PROJECT_ROOT

ALL_SECTIONS = (
    "connectivity",
    "system",
    "cpu",
    "memory",
    "disk",
    "services",
    "network",
    "cluster",
    "ai_summary",
)


def default_sections(now: Optional[datetime] = None) -> Dict[str, SectionStatus]:
    return {sec: SectionStatus(section=sec, status="NOT COLLECTED") for sec in ALL_SECTIONS}


class SnapshotStore:
    """Thread-safe file-backed in-memory store with consolidated section-level merging."""

    def __init__(self, max_items: int = 50):
        self.max_items = max_items
        self._lock = threading.RLock()
        self._snapshots: List[HealthSnapshot] = []
        self._load_existing()

    def _load_existing(self) -> None:
        proc_dir = PROJECT_ROOT / "data" / "processed"
        if not proc_dir.exists():
            return
        files = sorted(proc_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[: self.max_items]
        for f in reversed(files):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                snap = HealthSnapshot(**data)
                self._backfill_sections(snap)
                self._snapshots.append(snap)
            except Exception:
                continue

    @staticmethod
    def _backfill_sections(snap: HealthSnapshot) -> None:
        if not snap.sections:
            snap.sections = default_sections()
        for sec in ALL_SECTIONS:
            if sec not in snap.sections:
                snap.sections[sec] = SectionStatus(section=sec, status="NOT COLLECTED")
        ts = snap.collected_at
        if snap.connectivity.ssh_connected or snap.connectivity.axl_connected:
            if snap.sections["connectivity"].status == "NOT COLLECTED":
                snap.sections["connectivity"] = SectionStatus(section="connectivity", status="COLLECTED", last_successful_at=ts, last_attempted_at=ts, data_freshness="Snapshot")
        if snap.system.hostname or snap.system.cucm_version:
            if snap.sections["system"].status == "NOT COLLECTED":
                snap.sections["system"] = SectionStatus(section="system", status="COLLECTED", last_successful_at=ts, last_attempted_at=ts, data_freshness="Snapshot")
        if snap.system.cpu_utilization_pct is not None:
            if snap.sections["cpu"].status == "NOT COLLECTED":
                snap.sections["cpu"] = SectionStatus(section="cpu", status="COLLECTED", last_successful_at=ts, last_attempted_at=ts, data_freshness="Snapshot")
        if snap.system.memory_utilization_pct is not None:
            if snap.sections["memory"].status == "NOT COLLECTED":
                snap.sections["memory"] = SectionStatus(section="memory", status="COLLECTED", last_successful_at=ts, last_attempted_at=ts, data_freshness="Snapshot")
        if snap.system.disk_utilization_pct is not None:
            if snap.sections["disk"].status == "NOT COLLECTED":
                snap.sections["disk"] = SectionStatus(section="disk", status="COLLECTED", last_successful_at=ts, last_attempted_at=ts, data_freshness="Snapshot")
        if snap.services:
            if snap.sections["services"].status == "NOT COLLECTED":
                snap.sections["services"] = SectionStatus(section="services", status="COLLECTED", last_successful_at=ts, last_attempted_at=ts, data_freshness="Snapshot")
        if snap.network.ip_address or snap.network.diagnostics:
            if snap.sections["network"].status == "NOT COLLECTED":
                snap.sections["network"] = SectionStatus(section="network", status="COLLECTED", last_successful_at=ts, last_attempted_at=ts, data_freshness="Snapshot")
        if snap.cluster.nodes:
            if snap.sections["cluster"].status == "NOT COLLECTED":
                snap.sections["cluster"] = SectionStatus(section="cluster", status="COLLECTED", last_successful_at=ts, last_attempted_at=ts, data_freshness="Snapshot")
        if snap.ai_summary.overall_assessment:
            if snap.sections["ai_summary"].status == "NOT COLLECTED":
                snap.sections["ai_summary"] = SectionStatus(section="ai_summary", status="COLLECTED", last_successful_at=ts, last_attempted_at=ts, data_freshness="Snapshot")

    def add(self, snapshot: HealthSnapshot) -> None:
        with self._lock:
            self._backfill_sections(snapshot)
            self._snapshots.append(snapshot)
            if len(self._snapshots) > self.max_items:
                self._snapshots = self._snapshots[-self.max_items :]

    def merge_partial(
        self,
        partial: HealthSnapshot,
        updated_sections: Set[str],
        section_errors: Optional[Dict[str, str]] = None,
    ) -> HealthSnapshot:
        """Merge a partial collection into the latest consolidated snapshot without clearing untouched sections."""
        with self._lock:
            base = self.latest()
            if base is None:
                self._backfill_sections(partial)
                return partial

            merged = base.model_copy(deep=True)
            self._backfill_sections(merged)
            now = partial.collected_at or datetime.now(timezone.utc)
            merged.snapshot_id = partial.snapshot_id
            merged.collected_at = now
            merged.device_host = partial.device_host
            merged.is_simulated = partial.is_simulated

            if "connectivity" in updated_sections:
                merged.connectivity = partial.connectivity

            if any(s in updated_sections for s in ("system", "cpu", "memory", "disk")):
                sys_dict = merged.system.model_dump()
                for k, v in partial.system.model_dump().items():
                    if v is not None and (not isinstance(v, list) or len(v) > 0):
                        sys_dict[k] = v
                merged.system = type(merged.system)(**sys_dict)

            if "services" in updated_sections and partial.services:
                merged.services = partial.services

            if "network" in updated_sections:
                net_dict = merged.network.model_dump()
                for k, v in partial.network.model_dump().items():
                    if v is not None and (not isinstance(v, list) or len(v) > 0):
                        net_dict[k] = v
                merged.network = type(merged.network)(**net_dict)

            if "cluster" in updated_sections and partial.cluster.nodes:
                merged.cluster = partial.cluster

            # Merge command execution results by command_id (preserving prior commands from other sections)
            cmd_map = {c.command_id: c for c in merged.commands}
            for c in partial.commands:
                cmd_map[c.command_id] = c
            merged.commands = list(cmd_map.values())

            errs = section_errors or {}
            for sec in updated_sections:
                prev = merged.sections.get(sec, SectionStatus(section=sec))
                if sec in errs:
                    merged.sections[sec] = SectionStatus(
                        section=sec,
                        status="FAILED",
                        last_successful_at=prev.last_successful_at,
                        last_attempted_at=now,
                        error=errs[sec],
                        data_freshness="Stale" if prev.last_successful_at else "Failed",
                    )
                else:
                    merged.sections[sec] = SectionStatus(
                        section=sec,
                        status="COLLECTED",
                        last_successful_at=now,
                        last_attempted_at=now,
                        error=None,
                        data_freshness="Live",
                    )

            return merged

    def latest(self) -> Optional[HealthSnapshot]:
        with self._lock:
            return self._snapshots[-1] if self._snapshots else None

    def history(self) -> List[HealthSnapshot]:
        with self._lock:
            return list(self._snapshots)


_STORE: Optional[SnapshotStore] = None


def get_snapshot_store() -> SnapshotStore:
    global _STORE
    if _STORE is None:
        _STORE = SnapshotStore()
    return _STORE
