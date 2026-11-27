"""Device inventory and independent SSH/AXL connectivity test routes."""

from typing import Any, Dict, List

from fastapi import APIRouter

from backend.connectivity.axl_client import AXLConfig, CUCMAXLClient
from backend.connectivity.netmiko_client import NetmikoCUCMClient, SSHConnectionConfig
from backend.connectivity.ssh_client import ParamikoCUCMClient
from backend.models.device import DeviceInfo
from backend.utils.config_loader import get_settings, load_devices_yaml

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("", response_model=List[DeviceInfo])
def list_devices() -> List[DeviceInfo]:
    devices = load_devices_yaml()
    if devices:
        return [DeviceInfo(**d) for d in devices]
    s = get_settings()
    return [DeviceInfo(host=s.cucm_host, ssh_port=s.cucm_ssh_port, axl_port=s.cucm_port)]


@router.post("/test-connectivity")
def test_connectivity(host: str | None = None) -> Dict[str, Any]:
    """Independently test SSH (Netmiko + Paramiko) and HTTPS AXL connectivity."""
    settings = get_settings()
    target = host or settings.cucm_host

    ssh_result: Dict[str, Any] = {"connected": False, "transport": "none", "prompt": None, "error": None}
    cfg = SSHConnectionConfig.from_settings(host=target)
    try:
        nm = NetmikoCUCMClient(cfg)
        nm.connect()
        ssh_result = {"connected": True, "transport": "netmiko", "prompt": nm.get_prompt(), "error": None}
        nm.disconnect()
    except Exception as nm_err:
        try:
            pm = ParamikoCUCMClient(cfg)
            pm.connect()
            ssh_result = {"connected": True, "transport": "paramiko", "prompt": pm.get_prompt(), "error": None}
            pm.disconnect()
        except Exception as pm_err:
            ssh_result["error"] = f"Netmiko: {nm_err} | Paramiko: {pm_err}"

    axl_result: Dict[str, Any] = {"connected": False, "version": None, "error": None}
    try:
        axl = CUCMAXLClient(AXLConfig.from_settings(host=target))
        info = axl.test_connection()
        axl_result = {"connected": True, "version": info.get("version"), "error": None}
    except Exception as axl_err:
        axl_result["error"] = str(axl_err)

    return {
        "host": target,
        "ssh": ssh_result,
        "axl": axl_result,
        "credentials_configured": {
            "cli": settings.has_cli_credentials(),
            "axl": settings.has_axl_credentials(),
        },
    }
