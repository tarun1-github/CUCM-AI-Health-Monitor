"""CUCM AI Assistant orchestrator with strict execution modes and response provenance."""

import time
from typing import List, Optional

from backend.agents.conversation_manager import (
    ChatMessage,
    ConversationManager,
    LLMProvenance,
    TimingProvenance,
    ToolProvenance,
    get_conversation_manager,
)
from backend.agents.intent_router import IntentRouter
from backend.agents.response_formatter import ResponseFormatter
from backend.ai.provider_manager import get_llm_provider_manager
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry, get_axl_tool_registry


class CUCMAssistantAgent:
    """Controlled read-only CUCM AI Assistant with full execution provenance."""

    def __init__(
        self,
        registry: Optional[AXLToolRegistry] = None,
        router: Optional[IntentRouter] = None,
        formatter: Optional[ResponseFormatter] = None,
        conversations: Optional[ConversationManager] = None,
    ):
        self.registry = registry or get_axl_tool_registry()
        self.router = router or IntentRouter()
        self.formatter = formatter or ResponseFormatter()
        self.conversations = conversations or get_conversation_manager()

    def ask(
        self,
        question: str,
        session_id: str = "default",
        execution_preference: str = "auto",  # "auto" | "tool_only"
    ) -> ChatMessage:
        t_start = time.perf_counter()
        mgr = get_llm_provider_manager()
        allow_llm = execution_preference != "tool_only"

        user_msg = ChatMessage(session_id=session_id, role="user", content=question)
        self.conversations.add_message(session_id, user_msg)

        stages: List[str] = ["Understanding question..."]
        context = self.conversations.get_context(session_id)
        tools = self.registry.list_tools()

        stages.append("Selecting AXL/RISPort70 tool...")
        intent = self.router.route(question, tools, context=context, allow_llm=allow_llm)

        if not intent.tool_name:
            total_ms = round((time.perf_counter() - t_start) * 1000.0, 1)
            llm_failed = bool(intent.llm_error)
            mode = "LLM_FAILED" if llm_failed else "DETERMINISTIC_FALLBACK"
            notice = (
                f"LLM invocation failed ({intent.llm_error_category}). No matching fallback tool found."
                if llm_failed
                else "No matching read-only CUCM tool found for this question."
            )
            if llm_failed:
                mgr.record_call_result(False, intent.llm_latency_ms, intent.llm_error)
            reply = ChatMessage(
                session_id=session_id,
                role="assistant",
                content=(
                    "I could not map that question to an approved read-only CUCM tool. "
                    "Try asking about phone registration, unregistered phones, CTI Route Points, "
                    "SIP trunks, Publisher/Subscriber nodes, device pools, or a specific phone/DN."
                ),
                execution_mode=mode,
                execution_notice=notice,
                stages=stages,
                ai_available=False,
                ai_provider=intent.llm_provider,
                llm=LLMProvenance(
                    provider=intent.llm_provider or mgr.configured_provider,
                    model=intent.llm_model or mgr.get_active_model(),
                    invocation_success=False,
                    response_generation_success=False,
                    error=intent.llm_error,
                    error_category=intent.llm_error_category,
                ),
                timing=TimingProvenance(llm_latency_ms=intent.llm_latency_ms, total_latency_ms=total_ms),
                error=intent.llm_error,
            )
            self.conversations.add_message(session_id, reply)
            return reply

        spec = self.registry.get_spec(intent.tool_name)
        stages.append(f"Executing {spec.source_api if spec else 'AXL'} ({intent.tool_name})...")
        t_tool = time.perf_counter()
        tool_result = self.registry.execute(intent.tool_name, intent.params)
        tool_ms = round((time.perf_counter() - t_tool) * 1000.0, 1)
        tool_ok = bool(tool_result.get("success", True))

        stages.append("Formatting grounded response...")
        formatted = self.formatter.format(
            question,
            intent.tool_name,
            tool_result,
            allow_llm=allow_llm and intent.used_llm,
        )

        total_llm_ms = round(intent.llm_latency_ms + formatted.get("llm_latency_ms", 0.0), 1)
        total_ms = round((time.perf_counter() - t_start) * 1000.0, 1)

        llm_err = formatted.get("llm_error") or intent.llm_error
        llm_cat = formatted.get("llm_error_category") or intent.llm_error_category
        actual_prov = formatted.get("ai_provider") or intent.llm_provider or mgr.configured_provider
        actual_mdl = formatted.get("ai_model") or intent.llm_model or mgr.get_active_model()
        gw_fallback_used = bool(formatted.get("fallback_used") or intent.fallback_used)
        orig_failure = formatted.get("original_failure") or intent.original_failure
        failover_hist = formatted.get("failover_history") or intent.failover_history or []

        if execution_preference == "tool_only":
            mode = "TOOL_ONLY"
            notice = "Executed registered CUCM tool directly (Tool-Only mode; no LLM inference performed)."
        elif intent.used_llm and formatted["ai_available"] and tool_ok:
            mode = "LLM_GENERATED"
            notice = (
                f"Generated by {actual_prov} ({actual_mdl})"
                + (f" via gateway failover ({orig_failure})." if gw_fallback_used and orig_failure else ".")
            )
            mgr.record_call_result(True, total_llm_ms, actual_provider=actual_prov, actual_model=actual_mdl)
        elif llm_err:
            mode = "DETERMINISTIC_FALLBACK" if tool_ok else "LLM_FAILED"
            notice = (
                f"LLM invocation failed ({llm_cat}). Deterministic fallback was used successfully."
                if tool_ok
                else f"LLM request failed ({llm_cat}) and tool execution failed."
            )
            mgr.record_call_result(False, total_llm_ms, llm_err)
        else:
            mode = "DETERMINISTIC_FALLBACK"
            notice = "This response was generated using deterministic Python logic. No LLM inference was performed."

        if intent.tool_name == "cucm_get_phone" and intent.params.get("device_name"):
            self.conversations.update_context(session_id, {"last_device_name": intent.params["device_name"]})
        elif intent.tool_name == "cucm_list_phones_by_node" and intent.params.get("node_name"):
            self.conversations.update_context(session_id, {"last_node_name": intent.params["node_name"]})

        reply = ChatMessage(
            session_id=session_id,
            role="assistant",
            content=formatted["answer"],
            execution_mode=mode,
            execution_notice=notice,
            tool_used=intent.tool_name,
            source=tool_result.get("source") or (spec.source_api if spec else "AXL"),
            ai_available=bool(mode == "LLM_GENERATED"),
            ai_provider=actual_prov if mode == "LLM_GENERATED" else None,
            llm=LLMProvenance(
                configured_gateway=mgr.configured_provider,
                provider=actual_prov if mode == "LLM_GENERATED" else mgr.configured_provider,
                model=actual_mdl if mode == "LLM_GENERATED" else mgr.get_active_model(),
                fallback_used=gw_fallback_used,
                original_failure=orig_failure,
                failover_history=failover_hist,
                invocation_success=bool(intent.used_llm),
                response_generation_success=bool(formatted["ai_available"]),
                error=llm_err,
                error_category=llm_cat,
            ),
            tool=ToolProvenance(
                name=intent.tool_name,
                success=tool_ok,
                source=tool_result.get("source") or (spec.source_api if spec else "AXL"),
                error=tool_result.get("error"),
            ),
            timing=TimingProvenance(
                llm_latency_ms=total_llm_ms,
                tool_latency_ms=tool_ms,
                total_latency_ms=total_ms,
            ),
            table=formatted.get("table"),
            stages=stages,
            error=tool_result.get("error") if not tool_ok else llm_err,
        )
        self.conversations.add_message(session_id, reply)
        return reply
