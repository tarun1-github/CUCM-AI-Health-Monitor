"""Main CUCM health collection orchestrator combining SSH CLI and AXL API."""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.collectors.cpu_memory import (
    parse_perf_memory,
    parse_perf_processor,
    parse_process_load,
)
from backend.collectors.disk_health import parse_diskusage_common
from backend.collectors.network_health import parse_diagnose_test, parse_network_eth0
from backend.collectors.service_health import parse_service_list
from backend.collectors.system_health import (
    parse_show_hardware,
    parse_show_status,
    parse_show_version_active,
)
from backend.connectivity.axl_client import AXLConfig, CUCMAXLClient
from backend.connectivity.exceptions import CUCMConnectivityError
from backend.connectivity.netmiko_client import NetmikoCUCMClient, SSHConnectionConfig
from backend.connectivity.ssh_client import ParamikoCUCMClient
from backend.models.device import CommandDefinition
from backend.models.health import (
    ClusterHealthMetrics,
    ClusterNodeInfo,
    CommandExecutionResult,
    ConnectivityStatus,
    HealthSnapshot,
    NetworkHealthMetrics,
    SystemHealthMetrics,
)
from backend.utils.config_loader import (
    PROJECT_ROOT,
    get_settings,
    load_commands_yaml,
    load_settings_yaml,
)
from backend.utils.logger import get_logger

logger = get_logger("cucm_health", category="collector")
cmd_logger = get_logger("command_exec", category="commands")


class CUCMHealthCollector:
    """Collects health diagnostics from CUCM via SSH (Netmiko/Paramiko) and AXL."""

    def __init__(
        self,
        ssh_client: Any = None,
        axl_client: Optional[CUCMAXLClient] = None,
    ):
        self._ssh_client = ssh_client
        self._axl_client = axl_client
        self._yaml_settings = load_settings_yaml()

    def _connect_ssh(self, host: str, ssh_port: int) -> tuple[Any, str]:
        """Connect using Netmiko first, falling back to Paramiko if needed."""
        if self._ssh_client is not None:
            if not self._ssh_client.is_connected():
                self._ssh_client.connect()
            return self._ssh_client, type(self._ssh_client).__name__

        cfg = SSHConnectionConfig.from_settings(host=host, port=ssh_port)
        try:
            netmiko = NetmikoCUCMClient(cfg)
            netmiko.connect()
            return netmiko, "netmiko"
        except Exception as netmiko_err:
            logger.warning("Netmiko connection failed (%s); attempting Paramiko fallback.", netmiko_err)
            paramiko_client = ParamikoCUCMClient(cfg)
            paramiko_client.connect()
            return paramiko_client, "paramiko"

    def collect(
        self,
        host: Optional[str] = None,
        ssh_port: Optional[int] = None,
        axl_port: Optional[int] = None,
        device_id: str = "cucm-primary",
        device_role: str = "Publisher",
        command_ids: Optional[List[str]] = None,
        progress_callback: Optional[Any] = None,
        cancel_event: Optional[Any] = None,
    ) -> HealthSnapshot:
        """Execute read-only health check and return structured HealthSnapshot."""
        settings = get_settings()
        target_host = host or settings.cucm_host
        target_ssh_port = ssh_port or settings.cucm_ssh_port
        target_axl_port = axl_port or settings.cucm_port

        snapshot = HealthSnapshot(
            snapshot_id=uuid.uuid4().hex[:12],
            device_id=device_id,
            device_host=target_host,
            device_role=device_role,
            collected_at=datetime.now(timezone.utc),
        )

        all_yaml_cmds = [CommandDefinition(**c) for c in load_commands_yaml()]
        if command_ids:
            id_set = set(command_ids)
            enabled_cmds = [c for c in all_yaml_cmds if c.id in id_set and c.enabled]
            disabled_cmds = [c for c in all_yaml_cmds if c.id in id_set and not c.enabled]
        else:
            enabled_cmds = [c for c in all_yaml_cmds if c.enabled]
            disabled_cmds = [c for c in all_yaml_cmds if not c.enabled]

        for d_cmd in disabled_cmds:
            snapshot.commands.append(
                CommandExecutionResult(
                    command_id=d_cmd.id,
                    command=d_cmd.command,
                    category=d_cmd.category,
                    success=False,
                    skipped=True,
                    supported=True,
                    error="Command disabled in commands.yaml (skipped).",
                )
            )

        total_steps = len(enabled_cmds) + 1  # +1 for AXL step
        completed = 0

        def _notify(step_label: str, msg: str) -> None:
            if progress_callback:
                progress_callback(completed, total_steps, step_label, msg)

        # 1. SSH Collection
        client = None
        try:
            _notify("Connecting to CUCM SSH", f"Opening SSH session to {target_host}:{target_ssh_port}")
            client, transport_name = self._connect_ssh(target_host, target_ssh_port)
            snapshot.connectivity.ssh_connected = True
            snapshot.connectivity.ssh_transport = transport_name
            snapshot.connectivity.ssh_prompt = client.get_prompt()

            for cmd_def in enabled_cmds:
                if cancel_event is not None and cancel_event.is_set():
                    snapshot.commands.append(
                        CommandExecutionResult(
                            command_id=cmd_def.id,
                            command=cmd_def.command,
                            category=cmd_def.category,
                            success=False,
                            skipped=True,
                            error="Cancelled by user.",
                        )
                    )
                    continue

                _notify(f"Executing: {cmd_def.command}", cmd_def.description or cmd_def.command)
                res, needs_reconnect = self._run_single_command(client, cmd_def)
                snapshot.commands.append(res)
                if res.success:
                    self._apply_parsed_data(snapshot, cmd_def.parser, res.parsed_data, res.raw_output)
                elif needs_reconnect and self._ssh_client is None:
                    logger.warning("Reconnecting SSH session after timeout/desync on '%s'", cmd_def.command)
                    try:
                        client.disconnect()
                    except Exception:
                        pass
                    try:
                        client, transport_name = self._connect_ssh(target_host, target_ssh_port)
                    except Exception as recon_err:
                        logger.error("SSH reconnect failed: %s", recon_err)
                        snapshot.connectivity.ssh_error = f"Reconnect failed after '{cmd_def.command}': {recon_err}"
                        break

                completed += 1
                _notify(f"Completed: {cmd_def.command}", f"{completed}/{total_steps} steps finished")
        except Exception as exc:
            snapshot.connectivity.ssh_connected = False
            snapshot.connectivity.ssh_error = str(exc)
            logger.error("SSH collection error for %s: %s", target_host, exc)
        finally:
            if client is not None and self._ssh_client is None:
                try:
                    client.disconnect()
                except Exception:
                    pass

        # 2. AXL Collection (kept strictly separate from SSH results)
        if cancel_event is None or not cancel_event.is_set():
            _notify("Collecting AXL cluster state", f"Querying AXL SOAP on {target_host}:{target_axl_port}")
            self._collect_axl(snapshot, target_host, target_axl_port)
            completed += 1
            _notify("AXL collection complete", "Finalizing health analysis")

        # 3. Persist raw diagnostic snapshot JSON
        self._save_raw_snapshot(snapshot)
        return snapshot

    def _run_single_command(self, client: Any, cmd_def: CommandDefinition) -> tuple[CommandExecutionResult, bool]:
        t0 = time.perf_counter()
        try:
            raw = client.send_command(cmd_def.command, timeout=cmd_def.timeout)
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

            # Check if CUCM reported an unsupported/invalid command
            unsupported = any(
                marker in raw.lower()
                for marker in ("executed command unsuccessfully", "invalid command", "syntax error")
            )
            parsed = self._parse_output(cmd_def.parser, raw) if not unsupported else {}
            cmd_logger.info(
                "CMD=%s | status=%s | time_ms=%.1f",
                cmd_def.command,
                "UNSUPPORTED" if unsupported else "OK",
                elapsed_ms,
            )
            return (
                CommandExecutionResult(
                    command_id=cmd_def.id,
                    command=cmd_def.command,
                    category=cmd_def.category,
                    success=not unsupported,
                    supported=not unsupported,
                    execution_time_ms=elapsed_ms,
                    raw_output=raw,
                    parsed_data=parsed,
                    error="Command not supported on this CUCM release" if unsupported else None,
                ),
                False,
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            err_str = str(exc)
            err_lower = err_str.lower()
            is_timeout = any(
                k in err_lower
                for k in ("timed out", "timeout", "pattern not detected", "command echo was not detected")
            )
            cmd_logger.error("CMD=%s | status=%s | error=%s", cmd_def.command, "TIMEOUT" if is_timeout else "FAIL", exc)
            return (
                CommandExecutionResult(
                    command_id=cmd_def.id,
                    command=cmd_def.command,
                    category=cmd_def.category,
                    success=False,
                    timed_out=is_timeout,
                    execution_time_ms=elapsed_ms,
                    error=err_str,
                ),
                is_timeout,
            )

    def _parse_output(self, parser_name: str, raw: str) -> Dict[str, Any]:
        if parser_name == "version":
            return parse_show_version_active(raw)
        if parser_name == "status":
            return parse_show_status(raw)
        if parser_name == "hardware":
            return parse_show_hardware(raw)
        if parser_name == "process_load":
            return parse_process_load(raw)
        if parser_name == "perf_processor":
            return parse_perf_processor(raw)
        if parser_name == "perf_memory":
            return parse_perf_memory(raw)
        if parser_name == "diskusage":
            return parse_diskusage_common(raw)
        if parser_name == "network_eth0":
            return parse_network_eth0(raw)
        if parser_name == "diagnose_test":
            return {"diagnostics": parse_diagnose_test(raw)}
        if parser_name == "service_list":
            critical = set(self._yaml_settings.get("critical_services", []))
            important = set(self._yaml_settings.get("important_services", []))
            optional = set(self._yaml_settings.get("optional_services", []))
            items = parse_service_list(
                raw,
                critical_services=critical,
                important_services=important,
                optional_services=optional,
            )
            return {"services": [s.model_dump() for s in items]}
        return {}

    def _apply_parsed_data(
        self,
        snapshot: HealthSnapshot,
        parser_name: str,
        parsed: Dict[str, Any],
        raw: str,
    ) -> None:
        if parser_name in ("version", "status", "hardware", "process_load", "perf_processor", "perf_memory", "diskusage"):
            sys_dict = snapshot.system.model_dump()
            for k, v in parsed.items():
                if v is not None and (sys_dict.get(k) is None or k == "disk_partitions"):
                    sys_dict[k] = v
            snapshot.system = SystemHealthMetrics(**sys_dict)
        elif parser_name == "network_eth0":
            net_dict = snapshot.network.model_dump()
            net_dict.update({k: v for k, v in parsed.items() if v is not None})
            snapshot.network = NetworkHealthMetrics(**net_dict)
        elif parser_name == "diagnose_test":
            snapshot.network.diagnostics = parsed.get("diagnostics", [])
        elif parser_name == "service_list":
            critical = set(self._yaml_settings.get("critical_services", []))
            important = set(self._yaml_settings.get("important_services", []))
            optional = set(self._yaml_settings.get("optional_services", []))
            snapshot.services = parse_service_list(
                raw,
                critical_services=critical,
                important_services=important,
                optional_services=optional,
            )

    def _collect_axl(self, snapshot: HealthSnapshot, host: str, axl_port: int) -> None:
        axl = self._axl_client or CUCMAXLClient(AXLConfig.from_settings(host=host, port=axl_port))
        try:
            ver = axl.get_cucm_version()
            snapshot.connectivity.axl_connected = True
            snapshot.connectivity.axl_version = ver
            if not snapshot.system.cucm_version:
                snapshot.system.cucm_version = ver

            nodes = axl.get_cluster_nodes()
            cluster_nodes = [
                ClusterNodeInfo(
                    name=n.get("name") or "Unknown",
                    role=n.get("node_role") or "Unknown",
                    status="CONFIGURED",
                )
                for n in nodes
            ]
            pub = next((n.name for n in cluster_nodes if "pub" in n.role.lower()), None)
            snapshot.cluster = ClusterHealthMetrics(
                source="axl",
                publisher=pub,
                nodes=cluster_nodes,
                replication_status="NOT VERIFIED VIA AXL",
            )
        except Exception as exc:
            snapshot.connectivity.axl_connected = False
            snapshot.connectivity.axl_error = str(exc)
            logger.warning("AXL collection unavailable for %s: %s", host, exc)

    def _save_raw_snapshot(self, snapshot: HealthSnapshot) -> None:
        raw_dir = PROJECT_ROOT / "data" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        ts = snapshot.collected_at.strftime("%Y%m%d_%H%M%S")
        safe_host = snapshot.device_host.replace(".", "_").replace(":", "_")
        path = raw_dir / f"{safe_host}_{ts}_{snapshot.snapshot_id}.json"
        path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
