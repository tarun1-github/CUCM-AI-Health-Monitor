"""Grounded natural-language, General AI, Hybrid, and tabular response formatter."""

import json
import time
from typing import Any, Dict, List, Optional

from backend.ai.llm_factory import BaseLLMClient
from backend.ai.provider_manager import categorize_llm_error, get_llm_provider_manager, sanitize_llm_error


class ResponseFormatter:
    """Formats General AI, CUCM Operations, and Hybrid responses with full provenance."""

    def __init__(self, llm_client: Optional[BaseLLMClient] = None):
        self._llm_override = llm_client

    def _resolve_llm(self, allow_llm: bool) -> Optional[BaseLLMClient]:
        if not allow_llm:
            return None
        if self._llm_override is not None:
            return self._llm_override
        return get_llm_provider_manager().get_client()

    @staticmethod
    def _extract_llm_meta(llm: BaseLLMClient) -> Dict[str, Any]:
        lu_prov = getattr(llm, "last_used_provider", None)
        lu_mdl = getattr(llm, "last_used_model", None)
        orig_fail = getattr(llm, "original_failure", None)
        fb_used = getattr(llm, "fallback_used", False)
        fo_hist = getattr(llm, "failover_history", None)
        return {
            "ai_provider": lu_prov if isinstance(lu_prov, str) else getattr(llm, "provider_name", "llm"),
            "ai_model": lu_mdl if isinstance(lu_mdl, str) else getattr(llm, "model", None),
            "fallback_used": fb_used if isinstance(fb_used, bool) else False,
            "original_failure": orig_fail if isinstance(orig_fail, str) else None,
            "failover_history": list(fo_hist) if isinstance(fo_hist, list) else [],
        }

    def format_general_ai(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
        allow_llm: bool = True,
    ) -> Dict[str, Any]:
        llm = self._resolve_llm(allow_llm)
        if llm is None:
            return {
                "answer": "General AI mode requires an active LLM provider, which is currently disabled or unavailable.",
                "ai_available": False,
                "ai_provider": None,
                "ai_model": None,
                "fallback_used": False,
                "original_failure": None,
                "failover_history": [],
                "llm_latency_ms": 0.0,
                "llm_error": "No LLM provider configured/available",
                "llm_error_category": "Not configured",
            }

        sys_prompt = (
            "You are a helpful, accurate enterprise AI assistant (covering software engineering, "
            "networking, Linux, PowerShell, Python, Cisco technologies, and general knowledge). "
            "Do not claim to have queried live CUCM devices unless tool data is explicitly provided."
        )
        recent = (history or [])[-6:]
        conv_str = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in recent)
        user_prompt = f"Conversation history:\n{conv_str}\n\nUser: {question}" if conv_str else question

        t0 = time.perf_counter()
        try:
            try:
                ai_text = llm.generate(sys_prompt, user_prompt, json_mode=False).strip()
            except TypeError:
                ai_text = llm.generate(sys_prompt, user_prompt).strip()
            lat = round((time.perf_counter() - t0) * 1000.0, 1)
            if not ai_text:
                raise ValueError("Empty LLM response")
            meta = self._extract_llm_meta(llm)
            return {
                "answer": ai_text,
                "ai_available": True,
                "llm_latency_ms": lat,
                "llm_error": None,
                "llm_error_category": None,
                **meta,
            }
        except Exception as exc:
            lat = round((time.perf_counter() - t0) * 1000.0, 1)
            return {
                "answer": f"Unable to generate General AI response: {categorize_llm_error(exc)}.",
                "ai_available": False,
                "ai_provider": getattr(llm, "provider_name", None),
                "ai_model": getattr(llm, "model", None),
                "fallback_used": False,
                "original_failure": None,
                "failover_history": [],
                "llm_latency_ms": lat,
                "llm_error": sanitize_llm_error(exc),
                "llm_error_category": categorize_llm_error(exc),
            }

    def format(
        self,
        question: str,
        tool_name: str,
        tool_result: Dict[str, Any],
        allow_llm: bool = True,
        intent_type: str = "CUCM_OPERATION",
        history: Optional[List[Dict[str, str]]] = None,
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
        llm = self._resolve_llm(allow_llm)
        if llm is None:
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

        if intent_type == "HYBRID":
            sys_prompt = (
                "You are a Senior Cisco CUCM & Enterprise AI Assistant. "
                "The live CUCM tool has already connected and retrieved the real-time JSON data below. "
                "Never claim you lack network access. "
                "Combine the actual retrieved CUCM JSON data with clear technical explanation, "
                "clearly separating 'Retrieved CUCM Facts' from 'Technical Explanation / Assessment'. "
                "Never invent device names, counts, or metrics not present in the JSON."
            )
        else:
            sys_prompt = (
                "You are a Cisco CUCM assistant. The live CUCM tool has already retrieved the real-time JSON data below. "
                "Never claim you lack network access. Answer using ONLY the provided JSON tool result. "
                "Never invent device names, counts, or metrics not present in the JSON. Keep it concise and factual."
            )

        compact_payload = {k: v for k, v in tool_result.items() if k != "rows"}
        compact_payload["rows_preview"] = rows[:25]
        compact_payload["total_rows"] = len(rows)
        recent = (history or [])[-4:]
        user_prompt = (
            f"Recent history: {json.dumps(recent)}\n"
            f"Question: {question}\nTool: {tool_name}\nTool Output:\n{json.dumps(compact_payload, default=str)}"
        )

        t0 = time.perf_counter()
        try:
            try:
                ai_text = llm.generate(sys_prompt, user_prompt, json_mode=False).strip()
            except TypeError:
                ai_text = llm.generate(sys_prompt, user_prompt).strip()
            lat = round((time.perf_counter() - t0) * 1000.0, 1)
            if not ai_text:
                raise ValueError("Empty LLM response")
            meta = self._extract_llm_meta(llm)
            return {
                "answer": ai_text,
                "table": table,
                "ai_available": True,
                "llm_latency_ms": lat,
                "llm_error": None,
                "llm_error_category": None,
                **meta,
            }
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
        if tool_name == "cucm_live_cpu_utilization":
            c = res.get("cpu") or {}
            return (
                f"Successfully connected to CUCM {c.get('target_ip', res.get('target', ''))}. "
                f"Live CPU utilization: {c.get('cpu_utilization_pct')}% "
                f"(Idle: {c.get('cpu_idle_pct')}%, Status: {c.get('health_status')}, Source: {c.get('source')})."
            )
        if tool_name == "cucm_live_system_status":
            s = res.get("system") or {}
            return (
                f"CUCM {s.get('target_ip', '')} ({s.get('hostname', '')}, v{s.get('cucm_version', '')}): "
                f"CPU={s.get('cpu_utilization_pct')}%, Memory={s.get('memory_utilization_pct')}%, Disk={s.get('disk_utilization_pct')}%."
            )
        if tool_name == "cucm_live_services":
            return (
                f"CUCM {res.get('target', '')}: {res.get('started_count', 0)} of {res.get('total_services', 0)} services STARTED "
                f"({res.get('non_started_count', 0)} stopped/other)."
            )
        if tool_name == "cucm_live_network_status":
            n = res.get("network") or {}
            return f"CUCM {res.get('target', '')} eth0: IP={n.get('ip_address')}, Gateway={n.get('gateway')}, DNS={n.get('dns_primary')}, Link={n.get('link_status')}."
        if tool_name == "cucm_live_memory_disk":
            r0 = (res.get("rows") or [{}])[0]
            return f"CUCM {res.get('target', '')}: Memory={r0.get('memory_utilization_pct')}%, Disk={r0.get('disk_utilization_pct')}%."
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
