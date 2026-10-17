from fastapi import FastAPI

from . import retrieval
from .pipeline import handle
from .schemas import ChatRequest, ChatResponse

app = FastAPI()


@app.on_event("startup")
def build_index():
    retrieval.warmup()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    return handle(req.session_id, req.message)
