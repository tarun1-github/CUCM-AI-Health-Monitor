"""Device and command configuration models."""

from typing import List, Optional
from pydantic import BaseModel, Field


class DeviceInfo(BaseModel):
    """Configured CUCM node definition."""

    id: str = "cucm-primary"
    name: str = "CUCM Primary Node"
    host: str
    role: str = "Publisher"
    ssh_port: int = 22
    axl_port: int = 8443
    enabled: bool = True
    tags: List[str] = Field(default_factory=list)


class CommandDefinition(BaseModel):
    """Read-only diagnostic command definition."""

    id: str
    command: str
    category: str
    description: str = ""
    timeout: int = 45
    enabled: bool = True
    parser: str = "generic"
