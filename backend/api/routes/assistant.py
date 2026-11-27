"""CUCM AI Assistant chat, provider status, connectivity/inference tests, and tool routes."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.agents.conversation_manager import ChatMessage, get_conversation_manager
from backend.agents.cucm_agent import CUCMAssistantAgent
from backend.ai.provider_manager import get_llm_provider_manager
from backend.integrations.cucm_axl.tool_registry import ToolSpec, get_axl_tool_registry

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class ChatRequest(BaseModel):
    question: str
    session_id: str = "default"
    execution_preference: str = "auto"  # "auto" | "tool_only"


class ToolExecuteRequest(BaseModel):
    tool_name: str
    params: Dict[str, Any] = Field(default_factory=dict)


class ConfigureProviderRequest(BaseModel):
    provider: str
    model: Optional[str] = None
    confirmed: bool = False


@router.get("/status")
def get_assistant_status() -> Dict[str, Any]:
    return get_llm_provider_manager().get_status()


@router.post("/test-connection")
def test_provider_connection() -> Dict[str, Any]:
    return get_llm_provider_manager().test_connection()


@router.post("/test-inference")
def test_provider_inference() -> Dict[str, Any]:
    return get_llm_provider_manager().test_inference()


@router.post("/test-provider")
def test_specific_provider(provider: str, model: Optional[str] = None) -> Dict[str, Any]:
    mgr = get_llm_provider_manager()
    conn = mgr.test_single_provider_connection(provider, model=model)
    inf = mgr.test_single_provider_inference(provider, model=model)
    return {"provider": provider, "connection": conn, "inference": inf}


@router.post("/refresh")
def refresh_assistant_status() -> Dict[str, Any]:
    mgr = get_llm_provider_manager()
    mgr.test_connection()
    return mgr.get_status()


@router.post("/configure")
def configure_assistant_provider(req: ConfigureProviderRequest) -> Dict[str, Any]:
    if not req.confirmed:
        return {"updated": False, "detail": "Explicit confirmation required before changing provider/model."}
    status = get_llm_provider_manager().configure(req.provider, req.model)
    return {"updated": True, "status": status}


@router.post("/chat", response_model=ChatMessage)
def assistant_chat(req: ChatRequest) -> ChatMessage:
    agent = CUCMAssistantAgent()
    return agent.ask(req.question, session_id=req.session_id, execution_preference=req.execution_preference)


@router.get("/tools", response_model=List[ToolSpec])
def list_assistant_tools() -> List[ToolSpec]:
    return get_axl_tool_registry().list_tools()


@router.post("/tools/execute")
def execute_assistant_tool(req: ToolExecuteRequest) -> Dict[str, Any]:
    return get_axl_tool_registry().execute(req.tool_name, req.params)


@router.get("/history", response_model=List[ChatMessage])
def get_assistant_history(session_id: str = "default") -> List[ChatMessage]:
    return get_conversation_manager().get_history(session_id=session_id)


@router.post("/new-chat")
def new_assistant_chat() -> Dict[str, str]:
    sid = get_conversation_manager().new_session()
    return {"session_id": sid}


@router.delete("/history")
def clear_assistant_history(session_id: str = "default") -> Dict[str, Any]:
    get_conversation_manager().clear(session_id=session_id)
    return {"cleared": True, "session_id": session_id}
