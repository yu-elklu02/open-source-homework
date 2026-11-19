"""会员权益与门店服务智能体的 FastAPI 入口。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.getenv("APP_CONFIG", ROOT / "config.yaml"))
CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", CONFIG["ollama"]["base_url"])
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", CONFIG["ollama"]["model"])
KNOWLEDGE_DIR = ROOT / "data" / "knowledge"
AUDIT_PATH = ROOT / CONFIG["app"]["audit_file"]


class ApiResponse(BaseModel):
    """定义所有接口的统一返回结构。"""

    ok: bool
    data: Any = None
    error: dict[str, str] | None = None


class RightsCheckRequest(BaseModel):
    """定义优惠券权益核验请求。"""

    coupon_code: str = Field(min_length=1, max_length=80)
    member_id: str | None = Field(default=None, max_length=80)
    store_id: str | None = Field(default=None, max_length=80)
    question: str = Field(default="请说明该优惠券是否适用、依据是什么。", max_length=500)


class HandoffRequest(BaseModel):
    """定义人工转交请求。"""

    business_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)
    assignee: str = Field(min_length=1, max_length=100)


def response(data: Any = None, error: dict[str, str] | None = None) -> ApiResponse:
    """构造统一响应；失败时返回稳定的错误码和中文错误信息。"""

    return ApiResponse(ok=error is None, data=data, error=error)


def fail(code: str, message: str) -> ApiResponse:
    """构造失败响应；调用方无需捕获业务异常。"""

    return response(error={"code": code, "msg": message})


def load_documents() -> list[dict[str, str]]:
    """加载本地 Markdown 知识；目录缺失时返回空列表而不阻断服务。"""

    documents: list[dict[str, str]] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        documents.append({"source": path.name, "content": path.read_text(encoding="utf-8")})
    return documents


def retrieve(query: str, limit: int = 4) -> list[dict[str, str]]:
    """按关键词重叠检索知识片段；没有命中时返回空列表触发人工兜底。"""

    terms = {term.lower() for term in query.replace("，", " ").replace("。", " ").split() if len(term) > 1}
    scored: list[tuple[int, dict[str, str]]] = []
    for document in load_documents():
        haystack = document["content"].lower()
        score = sum(1 for term in terms if term in haystack)
        if score:
            scored.append((score, document))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [document for _, document in scored[:limit]]


def call_ollama(question: str, context: str) -> str:
    """调用本机 Ollama 生成有依据的解释；网络或模型失败时抛出可识别异常。"""

    prompt = "你是会员权益核验助手。只能依据给定资料回答，不得编造规则。若资料冲突、缺失或涉及资产变更，必须明确建议转人工。\n资料：\n" + context + "\n问题：" + question
    payload = json.dumps({"model": OLLAMA_MODEL, "stream": False, "messages": [{"role": "user", "content": prompt}]}).encode()
    request = urllib.request.Request(OLLAMA_BASE_URL.rstrip("/") + "/api/chat", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as result:
        body = json.loads(result.read().decode("utf-8"))
    return str(body.get("message", {}).get("content", ""))


def audit(event: dict[str, Any]) -> None:
    """追加审计记录；审计写入失败时不向用户暴露敏感堆栈。"""

    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **event}, ensure_ascii=False) + "\n")


def current_user(authorization: str | None = Header(default=None)) -> dict[str, str] | ApiResponse:
    """校验 Bearer token 并返回角色；失败时拒绝请求而不泄露 token。"""

    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    for role, configured in CONFIG["security"]["tokens"].items():
        # 生产环境优先读取环境变量，避免把真实访问凭据写进配置文件或镜像。
        configured = os.getenv(f"{role.upper()}_TOKEN", configured)
        if token == configured:
            return {"role": role}
    return fail("AUTH_REQUIRED", "请提供有效的 Bearer token")


def require_role(user: dict[str, str] | ApiResponse, allowed: set[str]) -> dict[str, str] | ApiResponse:
    """检查角色是否在允许集合中；越权时返回统一错误。"""

    if isinstance(user, ApiResponse):
        return user
    if user["role"] not in allowed:
        return fail("FORBIDDEN", "当前角色无权执行该操作")
    return user


app = FastAPI(title="会员权益与门店服务智能体 MVP", version="0.1.0")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> ApiResponse:
    """把请求校验失败转换为统一错误结构，避免默认 FastAPI 响应破坏接口约定。"""

    return fail("VALIDATION_ERROR", "请求参数不符合接口要求")


@app.get("/health", response_model=ApiResponse)
def health() -> ApiResponse:
    """返回服务存活状态和当前 Ollama 模型配置。"""

    return response({"service": "up", "ollama_model": OLLAMA_MODEL})


@app.post("/api/v1/rights/check", response_model=ApiResponse)
def check_rights(payload: RightsCheckRequest, user: dict[str, str] | ApiResponse = Depends(current_user)) -> ApiResponse:
    """检索规则并生成权益解释；证据不足或高风险时强制人工兜底。"""

    permitted = require_role(user, {"admin", "operator", "cashier"})
    if isinstance(permitted, ApiResponse):
        return permitted
    query = f"{payload.coupon_code} {payload.question}"
    documents = retrieve(query)
    if not documents:
        # 证据不足时不能让模型猜测权益，必须把决定交给具名人工。
        audit({"event": "rights_check", "role": permitted["role"], "coupon_code": payload.coupon_code, "decision": "handoff", "reason": "NO_EVIDENCE"})
        return response({"decision": "handoff", "reason": "NO_EVIDENCE", "message": "没有检索到可核验规则，请转人工。"})
    context = "\n\n".join(f"来源：{document['source']}\n{document['content']}" for document in documents)
    if any(keyword in payload.question for keyword in ("退款", "积分", "退货", "改价", "发券")):
        # 涉及不可逆资产动作时即使有证据也不能自动执行，防止越权造成损失。
        audit({"event": "rights_check", "role": permitted["role"], "coupon_code": payload.coupon_code, "decision": "handoff", "reason": "HIGH_RISK"})
        return response({"decision": "handoff", "reason": "HIGH_RISK", "message": "该请求涉及资产或退款操作，必须人工审核。", "sources": [document["source"] for document in documents]})
    try:
        answer = call_ollama(payload.question, context)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        audit({"event": "rights_check", "role": permitted["role"], "coupon_code": payload.coupon_code, "decision": "handoff", "reason": "LLM_UNAVAILABLE", "detail": str(exc)})
        return response({"decision": "handoff", "reason": "LLM_UNAVAILABLE", "message": "模型服务暂不可用，请转人工。", "sources": [document["source"] for document in documents]})
    audit({"event": "rights_check", "role": permitted["role"], "coupon_code": payload.coupon_code, "decision": "answered", "sources": [document["source"] for document in documents]})
    return response({"decision": "answered", "answer": answer, "sources": [document["source"] for document in documents], "model": OLLAMA_MODEL})


@app.post("/api/v1/handoffs", response_model=ApiResponse)
def create_handoff(payload: HandoffRequest, user: dict[str, str] | ApiResponse = Depends(current_user)) -> ApiResponse:
    """创建人工接手记录；只有运营或管理员可以指定责任人。"""

    permitted = require_role(user, {"admin", "operator"})
    if isinstance(permitted, ApiResponse):
        return permitted
    audit({"event": "handoff_created", "role": permitted["role"], **payload.model_dump()})
    return response({"business_id": payload.business_id, "status": "pending", "assignee": payload.assignee})


@app.get("/api/v1/audit", response_model=ApiResponse)
def list_audit(user: dict[str, str] | ApiResponse = Depends(current_user)) -> ApiResponse:
    """返回审计记录；仅管理员可查看，文件不存在时返回空数组。"""

    permitted = require_role(user, {"admin"})
    if isinstance(permitted, ApiResponse):
        return permitted
    if not AUDIT_PATH.exists():
        return response([])
    records = [json.loads(line) for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    return response(records[-100:])
