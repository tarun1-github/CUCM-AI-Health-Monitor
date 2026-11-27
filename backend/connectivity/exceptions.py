"""Connectivity exception hierarchy for CUCM SSH and AXL."""


class CUCMConnectivityError(Exception):
    """Base connectivity error."""


class CUCMSSHConnectionError(CUCMConnectivityError):
    """Raised when SSH connection fails or drops."""


class CUCMSSHAuthenticationError(CUCMConnectivityError):
    """Raised when SSH authentication fails."""


class CUCMSSHHostKeyError(CUCMConnectivityError):
    """Raised when SSH host key verification fails."""


class CUCMSSHTimeoutError(CUCMConnectivityError):
    """Raised when SSH prompt or command times out."""


class CUCMSSHCommandError(CUCMConnectivityError):
    """Raised when a CLI command fails or is rejected."""


class CUCMAXLError(CUCMConnectivityError):
    """Base AXL SOAP error."""


class CUCMAXLConnectionError(CUCMAXLError):
    """Raised when HTTPS AXL endpoint is unreachable."""


class CUCMAXLAuthenticationError(CUCMAXLError):
    """Raised when AXL authentication fails (401/Unauthorized)."""


class CUCMAXLSoapFaultError(CUCMAXLError):
    """Raised when CUCM AXL returns a SOAP Fault."""
