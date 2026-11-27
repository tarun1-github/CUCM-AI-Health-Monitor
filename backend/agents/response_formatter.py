"""Grounded natural-language and tabular response formatter with LLM provenance."""

import json
import time
from typing import Any, Dict, List, Optional

from backend.ai.llm_factory import BaseLLMClient
from backend.ai.provider_manager import categorize_llm_error, get_llm_provider_manager, sanitize_llm_error


class ResponseFormatter:
    """Formats actual AXL/RISPort70 tool results into natural language + structured tables."""

    def __init__(self, llm_client: Optional[BaseLLMClient] = None):
        self._llm_override = llm_client

    def format(
        self,
        question: str,
        tool_name: str,
        tool_result: Dict[str, Any],
        allow_llm: bool = True,
    ) -> Dict[str, Any]:
        rows = tool_result.get("rows") or []
        table = self._build_table(rows)

        if not tool_result.get("success", True):
            return {
                "answer": f"Unable to complete query via `{tool_name}`: {tool_result.get('error', 'Unknown error')}",
                "table": None,
                "ai_available": False,
                "ai_provider": None,
                "ai_model": None,
                "llm_latency_ms": 0.0,
                "llm_error": None,
                "llm_error_category": None,
            }

        deterministic_text = self._deterministic_answer(tool_name, tool_result)
        mgr = get_llm_provider_manager()
        llm = self._llm_override if self._llm_override is not None else (mgr.get_client() if allow_llm else None)
        if llm is None or not allow_llm:
            return {
                "answer": deterministic_text,
                "table": table,
                "ai_available": False,
                "ai_provider": None,
                "ai_model": None,
                "llm_latency_ms": 0.0,
                "llm_error": None,
                "llm_error_category": None,
            }

        sys_prompt = (
            "You are a Cisco CUCM assistant. Answer the user's question using ONLY the provided JSON tool result. "
            "Never invent device names, counts, or statuses not present in the JSON. Keep it concise and factual."
        )
        compact_payload = {k: v for k, v in tool_result.items() if k != "rows"}
        compact_payload["rows_preview"] = rows[:25]
        compact_payload["total_rows"] = len(rows)
        user_prompt = f"Question: {question}\nTool: {tool_name}\nTool Output:\n{json.dumps(compact_payload, default=str)}"

        t0 = time.perf_counter()
        try:
            try:
                ai_text = llm.generate(sys_prompt, user_prompt, json_mode=False).strip()
            except TypeError:
                ai_text = llm.generate(sys_prompt, user_prompt).strip()
            lat = round((time.perf_counter() - t0) * 1000.0, 1)
            if ai_text:
                lu_prov = getattr(llm, "last_used_provider", None)
                lu_mdl = getattr(llm, "last_used_model", None)
                orig_fail = getattr(llm, "original_failure", None)
                fb_used = getattr(llm, "fallback_used", False)
                fo_hist = getattr(llm, "failover_history", None)
                actual_prov = lu_prov if isinstance(lu_prov, str) else getattr(llm, "provider_name", "llm")
                actual_mdl = lu_mdl if isinstance(lu_mdl, str) else getattr(llm, "model", None)
                return {
                    "answer": ai_text,
                    "table": table,
                    "ai_available": True,
                    "ai_provider": actual_prov,
                    "ai_model": actual_mdl,
                    "fallback_used": fb_used if isinstance(fb_used, bool) else False,
                    "original_failure": orig_fail if isinstance(orig_fail, str) else None,
                    "failover_history": list(fo_hist) if isinstance(fo_hist, list) else [],
                    "llm_latency_ms": lat,
                    "llm_error": None,
                    "llm_error_category": None,
                }
            raise ValueError("Empty LLM response")
        except Exception as exc:
            lat = round((time.perf_counter() - t0) * 1000.0, 1)
            return {
                "answer": deterministic_text,
                "table": table,
                "ai_available": False,
                "ai_provider": getattr(llm, "provider_name", None),
                "ai_model": getattr(llm, "model", None),
                "llm_latency_ms": lat,
                "llm_error": sanitize_llm_error(exc),
                "llm_error_category": categorize_llm_error(exc),
            }

    @staticmethod
    def _build_table(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not rows or not isinstance(rows[0], dict):
            return None
        columns = list(rows[0].keys())
        return {"columns": columns, "rows": rows[:100], "total_rows": len(rows)}

    @staticmethod
    def _deterministic_answer(tool_name: str, res: Dict[str, Any]) -> str:
        if tool_name == "cucm_list_cti_route_points":
            return f"Your CUCM cluster contains {res.get('count', 0)} configured CTI Route Point(s)."
        if tool_name == "cucm_get_phone_registration":
            return (
                f"RISPort70 reported {res.get('total_found', 0)} phone(s): "
                f"{res.get('registered_count', 0)} Registered, {res.get('unregistered_count', 0)} Unregistered/Other."
            )
        if tool_name == "cucm_list_unregistered_phones":
            return (
                f"Found {res.get('unregistered_count', 0)} unregistered/non-registered phone(s) "
                f"out of {res.get('total_phones_seen', 0)} phone(s) returned by RISPort70."
            )
        if tool_name == "cucm_list_phones_by_node":
            return f"Node {res.get('node_name')} currently has {res.get('registered_count', 0)} registered phone(s)."
        if tool_name == "cucm_list_sip_trunks":
            return f"Found {res.get('count', 0)} configured SIP Trunk(s) in CUCM."
        if tool_name == "cucm_get_cluster_nodes":
            return f"CUCM {res.get('cucm_version', '')} cluster has {res.get('count', 0)} configured node(s)."
        if tool_name == "cucm_get_phone":
            if not res.get("found"):
                return f"Phone '{res.get('device_name')}' was not found on CUCM."
            p = res.get("phone", {})
            return (
                f"Phone {p.get('name')} ({p.get('model') or 'Unknown model'}): "
                f"Status={p.get('registration_status', 'Unknown')}, Device Pool={p.get('device_pool') or 'N/A'}, "
                f"IP={p.get('ip_address') or 'N/A'}."
            )
        if tool_name == "cucm_find_phone_by_dn":
            return f"Found {res.get('count', 0)} phone(s) associated with directory number '{res.get('pattern')}'."
        if tool_name == "cucm_list_device_pools":
            return f"Found {res.get('count', 0)} configured Device Pool(s) in CUCM."
        if tool_name == "cucm_get_line":
            return f"Found {res.get('count', 0)} line record(s) matching '{res.get('pattern')}'."
        return f"Executed {tool_name} ({len(res.get('rows', []))} record(s) returned)."
