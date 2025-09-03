from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from .rag_pipeline import ask as rag_ask, reindex as rag_reindex
from .fanout.api_fanout import router as fanout_router

app = FastAPI(title="ChatBot — RAG + Fan-out")

# --- API RAG ---
class AskBody(BaseModel):
    q: str

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/api/reindex", response_class=PlainTextResponse)
def reindex():
    rag_reindex()
    return "OK: reindex done."

@app.post("/api/ask", response_class=PlainTextResponse)
def api_ask(body: AskBody):
    try:
        out = rag_ask(body.q)
        return out
    except Exception as e:
        raise HTTPException(500, str(e))

# --- Fan-out router (map/reduce na wielu lokalnych workerach) ---
app.include_router(fanout_router, prefix="/fan")

# --- Statyki: montujemy POD /static i przekierowujemy root ---
@app.get("/")
def root_redirect():
    return RedirectResponse(url="/static/")

app.mount("/static", StaticFiles(directory="static", html=True), name="static")
