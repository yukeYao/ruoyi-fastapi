"""
智能助手路由，路径前缀 /agent。所有接口都要求 agent:chat:use（菜单"智能助手"的权限字符）；
工具层面的权限由 Agent 在执行每个工具前单独校验。
对话本身不加 @log：它是查询，写操作在阶段 2 审批执行时单独记录操作日志。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.agent import model as agent_model
from app.core.constants import AGENT_CHAT_PERM
from app.core.database import get_db
from app.core.deps import has_perm
from app.core.response import ajax_success, table_data_info
from app.schemas.agent import AgentChatRequest
from app.services import agent_service

router = APIRouter(prefix="/agent")


@router.post("/chat")
def chat(body: AgentChatRequest, db: Session = Depends(get_db), login_user: dict = Depends(has_perm(AGENT_CHAT_PERM))):
    # 通过模块属性取工厂，测试才能用 monkeypatch 替换成 ScriptedChatModel
    model = agent_model.get_chat_model()
    return ajax_success(agent_service.chat(db, login_user, body.conversation_id, body.message, model))


@router.get("/conversation/list")
def list_conversations(
    pageNum: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm(AGENT_CHAT_PERM)),
):
    rows, total = agent_service.list_conversations(db, login_user, pageNum, pageSize)
    return table_data_info(rows, total)


@router.get("/conversation/{conversation_id}/messages")
def get_messages(conversation_id: str, db: Session = Depends(get_db), login_user: dict = Depends(has_perm(AGENT_CHAT_PERM))):
    return ajax_success(agent_service.get_messages(db, login_user, conversation_id))
