"""Live CUCM diagnostic tools (PerfMon + SSH + AXL) supporting dynamic multi-server profiles."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.analyzers.threshold_engine import ThresholdEngine
from backend.collectors.cpu_memory import parse_perf_memory, parse_perf_processor, parse_process_load
from backend.collectors.network_health import parse_network_eth0
from backend.collectors.service_health import parse_service_list
from backend.collectors.system_health import parse_show_hardware, parse_show_status, parse_show_version_active
from backend.connectivity.axl_client import CUCMAXLClient
from backend.connectivity.netmiko_client import NetmikoCUCMClient
from backend.connectivity.ssh_client import ParamikoCUCMClient
from backend.devices.device_manager import CUCMDeviceManager, get_device_manager
from backend.devices.device_profile import CUCMServerProfile
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry, ToolSpec
from backend.integrations.cucm_perfmon.client import CUCMPerfMonClient
from backend.utils.config_loader import load_settings_yaml

IST_TZ = timezone(timedelta(hours=5, minutes=30))


def _timestamps_payload() -> Dict[str, str]:
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST_TZ)
    return {
        "collected_at_utc": now_utc.isoformat(),
        "collected_at_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "data_freshness": "LIVE",
    }


def _run_ssh_commands(dm: CUCMDeviceManager, profile: CUCMServerProfile, commands: List[str]) -> tuple[Dict[str, str], str]:
    cfg = dm.get_ssh_config(profile)
    outputs: Dict[str, str] = {}
    try:
        with NetmikoCUCMClient(cfg) as nm:
            for cmd in commands:
                outputs[cmd] = nm.send_command(cmd, timeout=profile.timeout)
        return outputs, "SSH (Netmiko)"
    except Exception:
        with ParamikoCUCMClient(cfg) as pm:
            for cmd in commands:
                outputs[cmd] = pm.send_command(cmd, timeout=profile.timeout)
        return outputs, "SSH (Paramiko)"


def _resolve_profile(dm: CUCMDeviceManager, params: Dict[str, Any]) -> CUCMServerProfile:
    sid = params.get("server_id")
    target_q = str(params.get("target_host") or params.get("target") or "")
    res = dm.resolve_target(target_q, explicit_server_id=sid)
    if not res.resolved or not res.profile:
        raise ValueError(res.error or "Unable to resolve registered CUCM server.")
    return res.profile


def register_live_health_tools(registry: AXLToolRegistry, device_manager: Optional[CUCMDeviceManager] = None) -> None:
    def _dm() -> CUCMDeviceManager:
        return device_manager or get_device_manager()

    def handle_live_cpu(params: Dict[str, Any]) -> Dict[str, Any]:
        dm = _dm()
        prof = _resolve_profile(dm, params)
        axl_cfg = dm.get_axl_config(prof)
        thresholds = ThresholdEngine()

        cpu_data: Dict[str, Any] = {}
        source_used = "CUCM PerfMon API"
        perfmon_err: Optional[str] = None
        hostname = prof.name
        version = prof.cucm_version

        # Option A: PerfMon SOAP API
        try:
            pm = CUCMPerfMonClient(axl_cfg)
            cpu_data = pm.collect_processor_counters()
            if cpu_data.get("cpu_utilization_pct") is None or (
                cpu_data.get("cpu_utilization_pct") == 0.0 and cpu_data.get("cpu_idle_pct") == 0.0
            ):
                raise RuntimeError("PerfMon returned un-sampled zero delta counters")
            try:
                version = CUCMAXLClient(axl_cfg).get_cucm_version()
            except Exception:
                pass
        except Exception as exc:
            perfmon_err = str(exc)
            # Option B: Existing CUCM SSH fallback
            outputs, ssh_src = _run_ssh_commands(dm, prof, ["show perf query class Processor", "show status", "show version active"])
            cpu_data = parse_perf_processor(outputs.get("show perf query class Processor", ""))
            st = parse_show_status(outputs.get("show status", ""))
            ver = parse_show_version_active(outputs.get("show version active", ""))
            hostname = st.get("hostname") or hostname
            version = ver.get("cucm_version") or version
            if cpu_data.get("cpu_utilization_pct") is None and st.get("cpu_utilization_pct") is not None:
                cpu_data["cpu_utilization_pct"] = st["cpu_utilization_pct"]
                cpu_data["cpu_idle_pct"] = st.get("cpu_idle_pct")
            source_used = f"{ssh_src} (PerfMon fallback: {perfmon_err})"

        cpu_pct = cpu_data.get("cpu_utilization_pct")
        cpu_status = thresholds.evaluate("cpu_utilization", cpu_pct).value
        dm.mark_connected(prof.server_id, f"CPU OK via {source_used}")
        ts = _timestamps_payload()
        row = {
            "target_ip": prof.host,
            "server_name": prof.name,
            "hostname": hostname,
            "cucm_version": version,
            "cpu_utilization_pct": cpu_pct,
            "cpu_idle_pct": cpu_data.get("cpu_idle_pct"),
            "system_pct": cpu_data.get("system_pct"),
            "user_pct": cpu_data.get("user_pct"),
            "iowait_pct": cpu_data.get("iowait_pct"),
            "health_status": cpu_status,
            "source": source_used,
            **ts,
        }
        return {"source": source_used, "target": prof.host, "cpu": row, "rows": [row]}

    def handle_live_system_status(params: Dict[str, Any]) -> Dict[str, Any]:
        dm = _dm()
        prof = _resolve_profile(dm, params)
        outputs, ssh_src = _run_ssh_commands(dm, prof, ["show version active", "show status", "show hardware", "show process load"])
        ver = parse_show_version_active(outputs.get("show version active", ""))
        st = parse_show_status(outputs.get("show status", ""))
        hw = parse_show_hardware(outputs.get("show hardware", ""))
        ld = parse_process_load(outputs.get("show process load", ""))
        ts = _timestamps_payload()
        row = {
            "target_ip": prof.host,
            "hostname": st.get("hostname") or prof.name,
            "cucm_version": ver.get("cucm_version") or prof.cucm_version,
            "uptime": st.get("uptime"),
            "hardware_model": hw.get("hardware_model"),
            "cpu_count": hw.get("cpu_count"),
            "cpu_utilization_pct": st.get("cpu_utilization_pct") or ld.get("cpu_utilization_pct"),
            "memory_utilization_pct": st.get("memory_utilization_pct") or ld.get("memory_utilization_pct"),
            "disk_utilization_pct": st.get("disk_utilization_pct") or hw.get("disk_utilization_pct"),
            "load_average_1m": ld.get("load_average_1m"),
            "source": ssh_src,
            **ts,
        }
        return {"source": ssh_src, "target": prof.host, "system": row, "rows": [row]}

    def handle_live_services(params: Dict[str, Any]) -> Dict[str, Any]:
        dm = _dm()
        prof = _resolve_profile(dm, params)
        outputs, ssh_src = _run_ssh_commands(dm, prof, ["utils service list"])
        cfg = load_settings_yaml()
        items = parse_service_list(
            outputs.get("utils service list", ""),
            critical_services=set(cfg.get("critical_services", [])),
            important_services=set(cfg.get("important_services", [])),
            optional_services=set(cfg.get("optional_services", [])),
        )
        only_stopped = bool(params.get("only_stopped", False))
        rows = [
            {"name": s.name, "status": s.status, "health": s.health.value, "criticality": s.criticality.value}
            for s in items
            if not only_stopped or s.status != "STARTED"
        ]
        started = sum(1 for s in items if s.status == "STARTED")
        stopped = sum(1 for s in items if s.status != "STARTED")
        return {
            "source": ssh_src,
            "target": prof.host,
            "total_services": len(items),
            "started_count": started,
            "non_started_count": stopped,
            "rows": rows,
            **_timestamps_payload(),
        }

    def handle_live_network(params: Dict[str, Any]) -> Dict[str, Any]:
        dm = _dm()
        prof = _resolve_profile(dm, params)
        outputs, ssh_src = _run_ssh_commands(dm, prof, ["show network eth0"])
        net = parse_network_eth0(outputs.get("show network eth0", ""))
        row = {"target_ip": prof.host, **net, "source": ssh_src, **_timestamps_payload()}
        return {"source": ssh_src, "target": prof.host, "network": row, "rows": [row]}

    def handle_live_memory_disk(params: Dict[str, Any]) -> Dict[str, Any]:
        dm = _dm()
        prof = _resolve_profile(dm, params)
        outputs, ssh_src = _run_ssh_commands(dm, prof, ["show status", "show hardware", "show perf query class Memory"])
        st = parse_show_status(outputs.get("show status", ""))
        hw = parse_show_hardware(outputs.get("show hardware", ""))
        pmem = parse_perf_memory(outputs.get("show perf query class Memory", ""))
        row = {
            "target_ip": prof.host,
            "hostname": st.get("hostname") or prof.name,
            "memory_total_mb": pmem.get("memory_total_mb") or st.get("memory_total_mb") or hw.get("memory_total_mb"),
            "memory_used_mb": pmem.get("memory_used_mb") or st.get("memory_used_mb"),
            "memory_utilization_pct": pmem.get("memory_utilization_pct") or st.get("memory_utilization_pct"),
            "disk_utilization_pct": st.get("disk_utilization_pct") or hw.get("disk_utilization_pct"),
            "disk_total_gb": hw.get("disk_total_gb"),
            "disk_used_gb": hw.get("disk_used_gb"),
            "source": ssh_src,
            **_timestamps_payload(),
        }
        return {"source": ssh_src, "target": prof.host, "rows": [row]}

    registry.register(
        ToolSpec(
            name="cucm_live_cpu_utilization",
            description="Retrieve LIVE CPU utilization (% CPU, Idle, System, User, IOwait) from a registered CUCM server via PerfMon SOAP API with SSH fallback.",
            input_schema={"server_id": "string (optional)", "target_host": "string IP/name (optional)"},
            source_api="CUCM PerfMon / SSH",
        ),
        handle_live_cpu,
    )
    registry.register(
        ToolSpec(
            name="cucm_live_system_status",
            description="Retrieve LIVE system status, CUCM version, uptime, hardware, and load average from a registered CUCM server via SSH.",
            input_schema={"server_id": "string (optional)", "target_host": "string IP/name (optional)"},
            source_api="CUCM SSH",
        ),
        handle_live_system_status,
    )
    registry.register(
        ToolSpec(
            name="cucm_live_services",
            description="Retrieve LIVE running and stopped CUCM services from a registered CUCM server via SSH ('utils service list').",
            input_schema={"server_id": "string (optional)", "target_host": "string IP/name (optional)", "only_stopped": "bool (optional)"},
            source_api="CUCM SSH",
        ),
        handle_live_services,
    )
    registry.register(
        ToolSpec(
            name="cucm_live_network_status",
            description="Retrieve LIVE network eth0 interface status, IP, mask, gateway, and DNS from a registered CUCM server via SSH.",
            input_schema={"server_id": "string (optional)", "target_host": "string IP/name (optional)"},
            source_api="CUCM SSH",
        ),
        handle_live_network,
    )
    registry.register(
        ToolSpec(
            name="cucm_live_memory_disk",
            description="Retrieve LIVE memory and disk utilization from a registered CUCM server via SSH.",
            input_schema={"server_id": "string (optional)", "target_host": "string IP/name (optional)"},
            source_api="CUCM SSH",
        ),
        handle_live_memory_disk,
    )
