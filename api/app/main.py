from fastapi import FastAPI

from . import ollama_check, pipeline, retrieval, router
from .pipeline import handle
from .schemas import ChatRequest, ChatResponse

app = FastAPI()


@app.on_event("startup")
def startup():
    models = [pipeline.LOCAL_MODEL]
    if router.ROUTER_MODE != "off":
        models.append(router.CHAT_MODEL)
    if router.ROUTER_MODE in ("llm", "fields"):
        models.append(router.ROUTER_MODEL)
    if retrieval.EMBED_BACKEND == "ollama":
        models.append(retrieval.EMBED_MODELS[retrieval.PRESETS["A"]["embed"]][0])
    ollama_check.ensure_models(pipeline.OLLAMA_URL, list(dict.fromkeys(models)))
    retrieval.warmup()


@app.get("/health")
def health():
    # 規格只要求 200；另外附上 Ollama 狀態，方便 demo 時排查
    return {"status": "ok", "ollama": ollama_check.STATUS}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    return handle(req.session_id, req.message)
