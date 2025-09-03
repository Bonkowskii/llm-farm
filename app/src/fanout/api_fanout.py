from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from .workers import get_workers, call_worker
from .fanout import map_extract_single, map_fanout, reduce_to_answer

router = APIRouter()

class AskReq(BaseModel):
    prompt: str
    n_predict: int = 64
    temperature: float = 0.2

class MapReq(BaseModel):
    chunk: str
    n_predict: int = 96

class FanReq(BaseModel):
    text: str
    max_chars: int = 800
    n_predict: int = 96

class ReduceReq(BaseModel):
    bullets: list[str]
    question: str
    n_predict: int = 256

@router.get("/health_fan")
def health_fan():
    return {"ok": True, "workers": get_workers()}

@router.post("/ask_proxy")
def ask_proxy(req: AskReq):
    workers = get_workers()
    if not workers: raise HTTPException(503, "Brak workerów.")
    out = call_worker(workers[0], req.prompt, n_predict=req.n_predict, temperature=req.temperature)
    return {"worker": workers[0], "output": out}

@router.post("/map_extract")
def map_extract(req: MapReq):
    workers = get_workers()
    if not workers: raise HTTPException(503, "Brak workerów.")
    return {"bullets": map_extract_single(req.chunk, n_predict=req.n_predict)}

@router.post("/map_fanout")
def map_fan(req: FanReq):
    workers = get_workers()
    if not workers: raise HTTPException(503, "Brak workerów.")
    return map_fanout(req.text, max_chars=req.max_chars, n_predict=req.n_predict)

@router.post("/reduce")
def reduce(req: ReduceReq):
    if not req.bullets: raise HTTPException(400, "Brak punktów.")
    return reduce_to_answer(req.bullets, req.question, n_predict=req.n_predict)
