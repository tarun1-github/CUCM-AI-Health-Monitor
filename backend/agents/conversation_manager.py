"""Thread-safe conversation history, multi-session support, and execution provenance models."""

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LLMProvenance(BaseModel):
    configured_gateway: str = "gateway"
    provider: str = "none"
    model: Optional[str] = None
    fallback_used: bool = False
    original_failure: Optional[str] = None
    failover_history: List[Dict[str, Any]] = Field(default_factory=list)
    invocation_success: bool = False
    response_generation_success: bool = False
    error: Optional[str] = None
    error_category: Optional[str] = None


class ToolProvenance(BaseModel):
    name: Optional[str] = None
    success: bool = False
    source: Optional[str] = None
    error: Optional[str] = None


class TimingProvenance(BaseModel):
    llm_latency_ms: float = 0.0
    tool_latency_ms: float = 0.0
    total_latency_ms: float = 0.0


class ChatMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    session_id: str = "default"
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    selected_mode: str = "auto"  # auto | general | cucm
    intent_type: str = "CUCM_OPERATION"  # GENERAL_AI | CUCM_OPERATION | HYBRID | CLARIFICATION_REQUIRED
    execution_mode: str = "DETERMINISTIC_FALLBACK"  # GENERAL_AI | LLM_GENERATED | HYBRID | CLARIFICATION_REQUIRED | DETERMINISTIC_FALLBACK | TOOL_ONLY | LLM_FAILED
    execution_notice: str = ""
    tool_used: Optional[str] = None
    source: Optional[str] = None
    ai_available: bool = False
    ai_provider: Optional[str] = None
    llm: LLMProvenance = Field(default_factory=LLMProvenance)
    tool: ToolProvenance = Field(default_factory=ToolProvenance)
    timing: TimingProvenance = Field(default_factory=TimingProvenance)
    table: Optional[Dict[str, Any]] = None
    stages: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class ConversationManager:
    """In-memory multi-session conversation store."""

    def __init__(self, max_messages: int = 100):
        self.max_messages = max_messages
        self._lock = threading.RLock()
        self._sessions: Dict[str, List[ChatMessage]] = {}
        self._context: Dict[str, Dict[str, Any]] = {}

    def new_session(self) -> str:
        sid = f"chat-{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._sessions[sid] = []
            self._context[sid] = {}
        return sid

    def add_message(self, session_id: str, msg: ChatMessage) -> None:
        with self._lock:
            msgs = self._sessions.setdefault(session_id, [])
            msgs.append(msg)
            if len(msgs) > self.max_messages:
                self._sessions[session_id] = msgs[-self.max_messages :]

    def get_history(self, session_id: str = "default") -> List[ChatMessage]:
        with self._lock:
            return list(self._sessions.get(session_id, []))

    def clear(self, session_id: str = "default") -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._context.pop(session_id, None)

    def update_context(self, session_id: str, updates: Dict[str, Any]) -> None:
        with self._lock:
            ctx = self._context.setdefault(session_id, {})
            ctx.update({k: v for k, v in updates.items() if v is not None})

    def get_context(self, session_id: str = "default") -> Dict[str, Any]:
        with self._lock:
            return dict(self._context.get(session_id, {}))


_MANAGER: Optional[ConversationManager] = None


def get_conversation_manager() -> ConversationManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ConversationManager()
    return _MANAGER
