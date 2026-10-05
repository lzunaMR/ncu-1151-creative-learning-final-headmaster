"""/chat 的輸入輸出格式，對應 spec/api-contract.md。不要改欄位名稱。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str
    message: str


class Guardrail(BaseModel):
    triggered: bool = False
    category: str | None = None
    action: str = "pass"


class Source(BaseModel):
    id: str
    text: str


class TraceStep(BaseModel):
    step: str
    detail: str


# ---- 教師 AI 助教 ----
class Critic(BaseModel):
    verdict: Literal["pass", "fail"]
    notes: str = ""


class Question(BaseModel):
    stem: str
    options: list[str]
    answer: int
    source_ids: list[str]
    critic: Critic


# ---- 校長 AI 決策助手 ----
class Evidence(BaseModel):
    query: str
    result: Any


class Recommendation(BaseModel):
    claim: str
    evidence: list[Evidence]


class ChatResponse(BaseModel):
    answer: str
    route: Literal["local", "api"]
    guardrail: Guardrail = Field(default_factory=Guardrail)
    sources: list[Source] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)
    # 只有教師組 / 校長組需要，其他組保持 None
    questions: list[Question] | None = None
    recommendations: list[Recommendation] | None = None
