"""Approved read-only Informix SQL query templates and parameter sanitization for CUCM 15 AXL."""

import re
from typing import Any, Dict, List, Optional

from backend.connectivity.axl_client import CUCMAXLClient
from backend.connectivity.exceptions import CUCMAXLSoapFaultError
from backend.utils.validators import validate_readonly_sql


def sanitize_sql_param(param: str) -> str:
    """Sanitize string parameters to prevent SQL injection in Informix queries."""
    if not param:
        return ""
    cleaned = re.sub(r"['\";\-\-/\*\\]", "", param.strip())
    return cleaned[:128]


APPROVED_SQL_TEMPLATES: Dict[str, str] = {
    "cluster_nodes": (
        "SELECT pn.name, pn.description, np.name AS node_role "
        "FROM processnode pn "
        "LEFT JOIN typenodeusage np ON pn.tknodeusage = np.enum "
        "WHERE pn.systemnode = 'f' "
        "ORDER BY pn.name"
    ),
    "phone_by_name": (
        "SELECT d.name, d.description, tm.name AS model, dp.name AS device_pool, css.name AS css "
        "FROM device d "
        "LEFT JOIN typemodel tm ON d.tkmodel = tm.enum "
        "LEFT JOIN devicepool dp ON d.fkdevicepool = dp.pkid "
        "LEFT JOIN callingsearchspace css ON d.fkcallingsearchspace = css.pkid "
        "WHERE UPPER(d.name) = UPPER('{device_name}')"
    ),
    "phones_by_dn": (
        "SELECT d.name AS device_name, d.description, tm.name AS model, dp.name AS device_pool, "
        "np.dnorpattern AS pattern, rp.name AS route_partition "
        "FROM device d "
        "INNER JOIN devicenumplanmap dnpm ON d.pkid = dnpm.fkdevice "
        "INNER JOIN numplan np ON dnpm.fknumplan = np.pkid "
        "LEFT JOIN typemodel tm ON d.tkmodel = tm.enum "
        "LEFT JOIN devicepool dp ON d.fkdevicepool = dp.pkid "
        "LEFT JOIN routepartition rp ON np.fkroutepartition = rp.pkid "
        "WHERE np.dnorpattern = '{pattern}' "
        "ORDER BY d.name"
    ),
    "cti_route_points": (
        "SELECT d.name, d.description, dp.name AS device_pool, loc.name AS location, "
        "np.dnorpattern AS directory_number "
        "FROM device d "
        "INNER JOIN typeclass tc ON d.tkclass = tc.enum "
        "LEFT JOIN devicepool dp ON d.fkdevicepool = dp.pkid "
        "LEFT JOIN location loc ON d.fklocation = loc.pkid "
        "LEFT JOIN devicenumplanmap dnpm ON d.pkid = dnpm.fkdevice "
        "LEFT JOIN numplan np ON dnpm.fknumplan = np.pkid "
        "WHERE tc.name = 'CTI Route Point' "
        "ORDER BY d.name"
    ),
    "sip_trunks": (
        "SELECT d.name, d.description, dp.name AS device_pool, sp.name AS sip_profile, css.name AS css "
        "FROM device d "
        "INNER JOIN typeclass tc ON d.tkclass = tc.enum "
        "LEFT JOIN devicepool dp ON d.fkdevicepool = dp.pkid "
        "LEFT JOIN sipprofile sp ON d.fksipprofile = sp.pkid "
        "LEFT JOIN callingsearchspace css ON d.fkcallingsearchspace = css.pkid "
        "WHERE tc.name = 'SIP Trunk' "
        "ORDER BY d.name"
    ),
    "device_pools": (
        "SELECT dp.name, cmg.name AS call_manager_group, dtg.name AS datetime_group, r.name AS region "
        "FROM devicepool dp "
        "LEFT JOIN callmanagergroup cmg ON dp.fkcallmanagergroup = cmg.pkid "
        "LEFT JOIN datetimesetting dtg ON dp.fkdatetimesetting = dtg.pkid "
        "LEFT JOIN region r ON dp.fkregion = r.pkid "
        "ORDER BY dp.name"
    ),
    "line_by_pattern": (
        "SELECT np.dnorpattern AS pattern, rp.name AS route_partition, np.description, "
        "np.alertingname AS alerting_name, css.name AS css "
        "FROM numplan np "
        "LEFT JOIN routepartition rp ON np.fkroutepartition = rp.pkid "
        "LEFT JOIN callingsearchspace css ON np.fkcallingsearchspace_sharedlineappear = css.pkid "
        "WHERE np.dnorpattern = '{pattern}'"
    ),
}


class ApprovedSQLExecutor:
    """Executes strictly approved read-only SELECT templates via AXL executeSQLQuery."""

    def __init__(self, axl_client: Optional[CUCMAXLClient] = None):
        self.client = axl_client or CUCMAXLClient()

    def execute_template(
        self,
        template_name: str,
        params: Optional[Dict[str, str]] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        if template_name not in APPROVED_SQL_TEMPLATES:
            raise CUCMAXLSoapFaultError(f"SQL template '{template_name}' is not in the approved read-only registry.")

        sql = APPROVED_SQL_TEMPLATES[template_name]
        safe_params = {k: sanitize_sql_param(str(v)) for k, v in (params or {}).items()}
        if safe_params:
            sql = sql.format(**safe_params)

        allowed, reason = validate_readonly_sql(sql)
        if not allowed:
            raise CUCMAXLSoapFaultError(reason)

        rows = self.client.execute_sql_query(sql)
        return rows[: max(1, min(limit, 1000))]
