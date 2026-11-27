"""Multi-server CUCM Device Manager and independent SSH/AXL/RISPort70/PerfMon test routes."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.devices.device_manager import get_device_manager
from backend.utils.config_loader import get_settings

router = APIRouter(prefix="/api/devices", tags=["devices"])


class DeviceCreateRequest(BaseModel):
    name: str
    host: str
    cucm_version: str = "15.0"
    role: str = "Publisher"
    axl_port: int = 8443
    ssh_port: int = 22
    axl_username: str = "Administrator"
    axl_password: Optional[str] = None
    cli_username: str = "Administrator"
    cli_password: Optional[str] = None
    verify_ssl: bool = False
    strict_host_key: bool = False
    timeout: int = 30
    enabled: bool = True
    is_default: bool = False


class DeviceUpdateRequest(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    cucm_version: Optional[str] = None
    role: Optional[str] = None
    axl_port: Optional[int] = None
    ssh_port: Optional[int] = None
    axl_username: Optional[str] = None
    axl_password: Optional[str] = None
    cli_username: Optional[str] = None
    cli_password: Optional[str] = None
    verify_ssl: Optional[bool] = None
    strict_host_key: Optional[bool] = None
    timeout: Optional[int] = None
    enabled: Optional[bool] = None


@router.get("")
def list_devices() -> List[Dict[str, Any]]:
    return get_device_manager().list_servers()


@router.post("")
def add_device(req: DeviceCreateRequest) -> Dict[str, Any]:
    try:
        prof = get_device_manager().add_server(**req.model_dump())
        return prof.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{server_id}")
def update_device(server_id: str, req: DeviceUpdateRequest) -> Dict[str, Any]:
    try:
        prof = get_device_manager().update_server(server_id, req.model_dump(exclude_none=True))
        return prof.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{server_id}/default")
def set_default_device(server_id: str) -> Dict[str, Any]:
    try:
        prof = get_device_manager().set_default_server(server_id)
        return prof.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{server_id}/enable")
def enable_device(server_id: str) -> Dict[str, Any]:
    try:
        return get_device_manager().update_server(server_id, {"enabled": True}).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{server_id}/disable")
def disable_device(server_id: str) -> Dict[str, Any]:
    try:
        return get_device_manager().update_server(server_id, {"enabled": False}).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{server_id}")
def delete_device(server_id: str) -> Dict[str, Any]:
    if not get_device_manager().delete_server(server_id):
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found.")
    return {"deleted": True, "server_id": server_id}


@router.post("/{server_id}/test-ssh")
def test_device_ssh(server_id: str) -> Dict[str, Any]:
    return get_device_manager().test_ssh(server_id)


@router.post("/{server_id}/test-axl")
def test_device_axl(server_id: str) -> Dict[str, Any]:
    return get_device_manager().test_axl(server_id)


@router.post("/{server_id}/test-risport")
def test_device_risport(server_id: str) -> Dict[str, Any]:
    return get_device_manager().test_risport(server_id)


@router.post("/{server_id}/test-perfmon")
def test_device_perfmon(server_id: str) -> Dict[str, Any]:
    return get_device_manager().test_perfmon(server_id)


@router.post("/test-connectivity")
def test_connectivity(host: str | None = None, server_id: str | None = None) -> Dict[str, Any]:
    """Independently test SSH (Netmiko + Paramiko) and HTTPS AXL connectivity."""
    dm = get_device_manager()
    res = dm.resolve_target(host or "", explicit_server_id=server_id)
    if res.resolved and res.profile:
        prof = res.profile
        ssh_result = dm.test_ssh(prof.server_id)
        axl_result = dm.test_axl(prof.server_id)
        return {
            "host": prof.host,
            "server_id": prof.server_id,
            "ssh": ssh_result,
            "axl": axl_result,
            "credentials_configured": {
                "cli": dm.credentials.has_secret(prof.cli_credential_ref),
                "axl": dm.credentials.has_secret(prof.axl_credential_ref),
            },
        }
    s = get_settings()
    return {
        "host": host or s.cucm_host,
        "ssh": {"connected": False, "error": res.error},
        "axl": {"connected": False, "error": res.error},
        "credentials_configured": {"cli": s.has_cli_credentials(), "axl": s.has_axl_credentials()},
    }
