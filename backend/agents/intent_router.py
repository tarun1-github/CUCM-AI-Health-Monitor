"""LLM-first hybrid intent classifier and router for General AI, CUCM Operations, Hybrid, and Clarification."""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.ai.llm_factory import BaseLLMClient
from backend.ai.provider_manager import categorize_llm_error, get_llm_provider_manager, sanitize_llm_error
from backend.integrations.cucm_axl.tool_registry import ToolSpec


@dataclass
class RoutedIntent:
    intent_type: str = "CUCM_OPERATION"  # GENERAL_AI | CUCM_OPERATION | HYBRID | CLARIFICATION_REQUIRED
    tool_name: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    clarification_question: Optional[str] = None
    confidence: str = "high"
    used_llm: bool = False
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    fallback_used: bool = False
    original_failure: Optional[str] = None
    failover_history: List[Dict[str, Any]] = field(default_factory=list)
    llm_latency_ms: float = 0.0
    llm_error: Optional[str] = None
    llm_error_category: Optional[str] = None
    reason: str = ""


class IntentRouter:
    """Classifies user questions into GENERAL_AI, CUCM_OPERATION, HYBRID, or CLARIFICATION_REQUIRED."""

    def __init__(self, llm_client: Optional[BaseLLMClient] = None):
        self._llm_override = llm_client

    def route(
        self,
        question: str,
        tools: List[ToolSpec],
        context: Optional[Dict[str, Any]] = None,
        allow_llm: bool = True,
        mode: str = "auto",
        history: Optional[List[Dict[str, str]]] = None,
    ) -> RoutedIntent:
        q = (question or "").strip()
        ctx = context or {}
        selected_mode = (mode or "auto").strip().lower()
        tool_names = {t.name for t in tools}

        # MODE B: GENERAL AI — never execute CUCM tools
        if selected_mode == "general":
            return RoutedIntent(
                intent_type="GENERAL_AI",
                tool_name=None,
                used_llm=True,
                reason="Manual General AI mode selected; CUCM tools disabled.",
            )

        mgr = get_llm_provider_manager()
        llm = self._llm_override if self._llm_override is not None else (mgr.get_client() if allow_llm else None)
        llm_err: Optional[str] = None
        llm_cat: Optional[str] = None
        llm_lat = 0.0

        if llm is not None and allow_llm:
            routed = self._classify_with_llm(q, tools, ctx, history or [], selected_mode, llm)
            llm_lat = routed.llm_latency_ms
            if routed.used_llm:
                # Validate tool name if CUCM_OPERATION or HYBRID
                if routed.intent_type in ("CUCM_OPERATION", "HYBRID") and routed.tool_name not in tool_names:
                    # Try deterministic tool match if LLM picked an unknown tool name
                    det_tool = self._route_deterministic(q, tool_names, ctx, selected_mode)
                    routed.tool_name = det_tool.tool_name
                    routed.params = det_tool.params
                if selected_mode == "cucm" and routed.intent_type == "GENERAL_AI":
                    routed.intent_type = "CUCM_OPERATION"
                return routed
            llm_err = routed.llm_error or "Invalid LLM classification"
            llm_cat = routed.llm_error_category or "Invalid response"

        # Deterministic fallback when LLM is unavailable/disabled/failed
        det = self._route_deterministic(q, tool_names, ctx, selected_mode)
        det.llm_latency_ms = llm_lat
        det.llm_error = llm_err
        det.llm_error_category = llm_cat
        det.llm_provider = getattr(llm, "provider_name", mgr.configured_provider) if llm else mgr.configured_provider
        det.llm_model = getattr(llm, "model", mgr.get_active_model()) if llm else mgr.get_active_model()
        return det

    @staticmethod
    def _classify_with_llm(
        question: str,
        tools: List[ToolSpec],
        context: Dict[str, Any],
        history: List[Dict[str, str]],
        selected_mode: str,
        llm: BaseLLMClient,
    ) -> RoutedIntent:
        catalog = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        sys_prompt = (
            "You are an intelligent intent classifier for a hybrid General-Purpose + Cisco CUCM AI Assistant.\n"
            "Classify the user's question into one of four intent_type values:\n"
            "1. GENERAL_AI: Conceptual/educational questions, programming (Python/FastAPI/PowerShell/Linux), "
            "web/CSS/HTML, general Cisco theory (e.g., 'What is CSS?', 'Explain SIP trunk architecture', "
            "'What is Calling Search Space in CUCM?'), writing, or troubleshooting theory NOT asking for live cluster data.\n"
            "2. CUCM_OPERATION: Requests for live/configured data from the user's CUCM cluster "
            "(e.g., 'Check CPU utilization of 10.197.206.141', 'Show system status', 'Show services', "
            "'Show network configuration', 'How many phones are registered?', 'Show unregistered phones', "
            "'How many CTI Route Points are configured?', 'Show all SIP trunks', 'Give me Publisher and Subscriber details').\n"
            "3. HYBRID: Requests that ask for BOTH live CUCM cluster data AND conceptual explanation or health interpretation "
            "(e.g., 'Check CPU utilization and explain whether it is healthy', 'Show my configured SIP trunks and explain their purpose').\n"
            "4. CLARIFICATION_REQUIRED: Ambiguous short/abbreviated queries where the meaning is unclear "
            "(e.g., 'Tell me about CSS' or 'Check CSS' without specifying Cascading Style Sheets vs CUCM Calling Search Space).\n\n"
            f"User selected mode: {selected_mode}.\n"
            "If intent_type is CUCM_OPERATION or HYBRID, select exactly ONE tool_name from the catalog and provide params.\n"
            "If intent_type is GENERAL_AI or CLARIFICATION_REQUIRED, set tool_name to null.\n"
            "Never invent SQL or CLI commands.\n"
            'Return ONLY valid JSON: {"intent_type": "...", "tool_name": "... or null", "params": {}, "clarification_question": "... or null", "reason": "..."}'
        )
        recent = history[-6:] if history else []
        user_prompt = (
            f"Tool Catalog:\n{json.dumps(catalog, indent=2)}\n\n"
            f"Recent Conversation:\n{json.dumps(recent)}\n\n"
            f"Context: {json.dumps(context)}\n"
            f"Question: {question}"
        )
        t0 = time.perf_counter()
        try:
            raw = llm.generate(sys_prompt, user_prompt).strip()
            lat = round((time.perf_counter() - t0) * 1000.0, 1)
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end > start:
                data = json.loads(raw[start : end + 1])
                tname = data.get("tool_name")
                default_itype = "CUCM_OPERATION" if tname else "GENERAL_AI"
                itype = str(data.get("intent_type") or default_itype).upper()
                if itype not in ("GENERAL_AI", "CUCM_OPERATION", "HYBRID", "CLARIFICATION_REQUIRED"):
                    itype = default_itype
                params = data.get("params") if isinstance(data.get("params"), dict) else {}
                clar = data.get("clarification_question")

                lu_prov = getattr(llm, "last_used_provider", None)
                lu_mdl = getattr(llm, "last_used_model", None)
                orig_fail = getattr(llm, "original_failure", None)
                fb_used = getattr(llm, "fallback_used", False)
                fo_hist = getattr(llm, "failover_history", None)

                return RoutedIntent(
                    intent_type=itype,
                    tool_name=tname,
                    params=params,
                    clarification_question=clar,
                    used_llm=True,
                    llm_provider=lu_prov if isinstance(lu_prov, str) else getattr(llm, "provider_name", "llm"),
                    llm_model=lu_mdl if isinstance(lu_mdl, str) else getattr(llm, "model", None),
                    fallback_used=fb_used if isinstance(fb_used, bool) else False,
                    original_failure=orig_fail if isinstance(orig_fail, str) else None,
                    failover_history=list(fo_hist) if isinstance(fo_hist, list) else [],
                    llm_latency_ms=lat,
                    reason=str(data.get("reason", "LLM intent classification")),
                )
            return RoutedIntent(
                intent_type="GENERAL_AI",
                used_llm=False,
                llm_latency_ms=lat,
                llm_error="LLM did not return valid JSON intent classification.",
                llm_error_category="Invalid response",
            )
        except Exception as exc:
            lat = round((time.perf_counter() - t0) * 1000.0, 1)
            return RoutedIntent(
                intent_type="GENERAL_AI",
                used_llm=False,
                llm_latency_ms=lat,
                llm_error=sanitize_llm_error(exc),
                llm_error_category=categorize_llm_error(exc),
            )

    @staticmethod
    def _route_deterministic(
        question: str,
        tool_names: set[str],
        context: Dict[str, Any],
        selected_mode: str = "auto",
    ) -> RoutedIntent:
        ql = question.lower().strip()

        # Ambiguous abbreviation check in auto mode (e.g., "tell me about css", "check css")
        if selected_mode == "auto" and re.fullmatch(r"(?:what\s+is\s+|tell\s+me\s+about\s+|explain\s+|check\s+)?css\??", ql):
            if ql.startswith("what is css"):
                return RoutedIntent(intent_type="GENERAL_AI", tool_name=None, reason="General knowledge question about CSS")
            return RoutedIntent(
                intent_type="CLARIFICATION_REQUIRED",
                tool_name=None,
                clarification_question="Did you mean **CSS (Cascading Style Sheets)** for web styling, or **CSS (Calling Search Space)** in Cisco CUCM?",
                reason="Ambiguous abbreviation 'CSS'",
            )

        # Conceptual / programming / general questions in auto mode
        if selected_mode == "auto" and re.match(r"^(?:what\s+is|explain|write|create|how\s+does|difference\s+between|prepare)\b", ql):
            if not any(live_cue in ql for live_cue in ("my ", "configured", "registered", "unregistered", "cluster", "publisher", "subscriber", "sep", "10.", "192.168.", "172.")):
                return RoutedIntent(intent_type="GENERAL_AI", tool_name=None, reason="Conceptual/general knowledge question")

        # Live CPU utilization
        if any(k in ql for k in ("cpu", "processor")) and "cucm_live_cpu_utilization" in tool_names:
            itype = "HYBRID" if ("explain" in ql or "healthy" in ql or "why" in ql) else "CUCM_OPERATION"
            return RoutedIntent(itype, "cucm_live_cpu_utilization", {}, reason="Matched live CPU utilization query")

        # Live Memory / Disk
        if any(k in ql for k in ("memory", "ram", "disk", "storage", "diskusage")) and "cucm_live_memory_disk" in tool_names:
            itype = "HYBRID" if ("explain" in ql or "healthy" in ql) else "CUCM_OPERATION"
            return RoutedIntent(itype, "cucm_live_memory_disk", {}, reason="Matched live memory/disk query")

        # Live Services
        if any(k in ql for k in ("service", "services")) and "cucm_live_services" in tool_names:
            only_stopped = "stopped" in ql or "down" in ql
            return RoutedIntent("CUCM_OPERATION", "cucm_live_services", {"only_stopped": only_stopped}, reason="Matched live services query")

        # Live Network
        if any(k in ql for k in ("network", "eth0", "gateway", "dns", "interface")) and "cucm_live_network_status" in tool_names:
            return RoutedIntent("CUCM_OPERATION", "cucm_live_network_status", {}, reason="Matched live network status query")

        # Live System Status / Version / Uptime / Hardware
        if any(k in ql for k in ("system status", "uptime", "hardware", "cucm version", "version")) and "cucm_live_system_status" in tool_names:
            return RoutedIntent("CUCM_OPERATION", "cucm_live_system_status", {}, reason="Matched live system status query")

        # Explicit CUCM operations
        dev_match = re.search(r"\b((?:SEP|CSF|BOT|TCT|TAB|IPC|ATA)[A-Za-z0-9_-]{4,})\b", question, re.IGNORECASE)
        if dev_match and "cucm_get_phone" in tool_names:
            return RoutedIntent("CUCM_OPERATION", "cucm_get_phone", {"device_name": dev_match.group(1).upper()}, reason="Matched phone device name")

        node_match = re.search(r"\b([A-Za-z0-9_-]+(?:PUB|SUB\d*)[A-Za-z0-9_-]*)\b", question, re.IGNORECASE)
        if node_match and ("phone" in ql or "registered" in ql) and "cucm_list_phones_by_node" in tool_names:
            return RoutedIntent("CUCM_OPERATION", "cucm_list_phones_by_node", {"node_name": node_match.group(1)}, reason="Matched CUCM node name")

        if ("unregistered" in ql or "not registered" in ql) and "cucm_list_unregistered_phones" in tool_names:
            itype = "HYBRID" if ("why" in ql or "explain" in ql) else "CUCM_OPERATION"
            return RoutedIntent(itype, "cucm_list_unregistered_phones", {}, reason="Matched unregistered phone query")

        if ("registration" in ql or "registered" in ql) and ("phone" in ql or "phones" in ql) and "cucm_get_phone_registration" in tool_names:
            status = "Registered" if ("how many" in ql and "unregistered" not in ql and "status" not in ql) else "Any"
            return RoutedIntent("CUCM_OPERATION", "cucm_get_phone_registration", {"device_name": "*", "status": status}, reason="Matched phone registration query")

        if ("cti" in ql or "route point" in ql or "routepoint" in ql) and "cucm_list_cti_route_points" in tool_names:
            return RoutedIntent("CUCM_OPERATION", "cucm_list_cti_route_points", {}, reason="Matched CTI Route Point query")

        if ("sip" in ql and "trunk" in ql) or "trunks" in ql:
            if "cucm_list_sip_trunks" in tool_names:
                itype = "HYBRID" if ("explain" in ql or "purpose" in ql) else "CUCM_OPERATION"
                return RoutedIntent(itype, "cucm_list_sip_trunks", {}, reason="Matched SIP Trunk query")

        if any(k in ql for k in ("publisher", "subscriber", "cluster", "nodes")) and "cucm_get_cluster_nodes" in tool_names:
            return RoutedIntent("CUCM_OPERATION", "cucm_get_cluster_nodes", {}, reason="Matched cluster node query")

        if ("device pool" in ql or "devicepool" in ql or "device pools" in ql) and "cucm_list_device_pools" in tool_names:
            return RoutedIntent("CUCM_OPERATION", "cucm_list_device_pools", {}, reason="Matched device pool query")

        dn_match = re.search(r"\b(\d{3,10})\b", question)
        if dn_match and any(k in ql for k in ("dn", "directory number", "extension", "number", "line", "phone")):
            if "line" in ql and "phone" not in ql and "cucm_get_line" in tool_names:
                return RoutedIntent("CUCM_OPERATION", "cucm_get_line", {"pattern": dn_match.group(1)}, reason="Matched directory number line query")
            if "cucm_find_phone_by_dn" in tool_names:
                return RoutedIntent("CUCM_OPERATION", "cucm_find_phone_by_dn", {"pattern": dn_match.group(1)}, reason="Matched phone-by-DN query")

        if any(k in ql for k in ("that phone", "its", "details", "more info")) and context.get("last_device_name") and "cucm_get_phone" in tool_names:
            return RoutedIntent("CUCM_OPERATION", "cucm_get_phone", {"device_name": context["last_device_name"]}, reason="Follow-up on previous phone")

        return RoutedIntent(
            intent_type="CUCM_OPERATION" if selected_mode == "cucm" else "GENERAL_AI",
            tool_name=None,
            confidence="low",
            reason="General AI or unmapped query.",
        )
