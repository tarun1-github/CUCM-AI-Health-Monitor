"""Unified CUCM AXL Toolkit client combining schema, SOAP, SQL, and RISPort70."""

from typing import Optional

from backend.connectivity.axl_client import AXLConfig, CUCMAXLClient
from backend.integrations.cucm_axl.risport_client import RisPort70Client
from backend.integrations.cucm_axl.schema_manager import AXLSchemaManager
from backend.integrations.cucm_axl.soap_executor import AXLSoapExecutor
from backend.integrations.cucm_axl.sql_executor import ApprovedSQLExecutor


class CUCMToolkitAXLClient:
    """High-level CUCM AXL + RISPort70 client for read-only CUCM tools."""

    def __init__(self, config: Optional[AXLConfig] = None):
        self.config = config or AXLConfig.from_settings()
        self.axl = CUCMAXLClient(self.config)
        self.schema = AXLSchemaManager(axl_version=self.config.axl_version)
        self.soap = AXLSoapExecutor(self.axl)
        self.sql = ApprovedSQLExecutor(self.axl)
        self.risport = RisPort70Client(self.config)
