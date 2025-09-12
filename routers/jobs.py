# routers/jobs.py
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional

router = APIRouter()

class EnqueueRequest(BaseModel):
    prompt: str
    system: Optional[str] = None
    model: Optional[str] = None
    options: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 5  # 0=wysoki

@router.post("/jobs")
async def enqueue_job(request: Request, body: EnqueueRequest):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Jobs engine not ready")
    job_id = await jobs.enqueue(body.model_dump(), priority=body.priority)
    return {"job_id": job_id, "queued": True}

@router.get("/jobs/{job_id}")
async def job_status(request: Request, job_id: str):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Jobs engine not ready")
    job = await jobs.get_status(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "status": job.status,
        "priority": job.priority,
        "enqueued_at": job.enqueued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "device": job.device,
        "error": job.error
    }

@router.get("/jobs/{job_id}/result")
async def job_result(request: Request, job_id: str):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Jobs engine not ready")
    job = await jobs.get_status(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(status_code=202, detail=f"Job status is {job.status}")
    return job.result

# NEW: strumień wprost po enqueue (tokeny na żywo)
@router.post("/jobs/stream")
async def enqueue_job_stream(request: Request, body: EnqueueRequest):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Jobs engine not ready")
    job_id = await jobs.enqueue_stream(body.model_dump(), priority=body.priority)

    async def gen():
        async for chunk in jobs.stream_job(job_id):
            if chunk:
                yield chunk
    return StreamingResponse(gen(), media_type="application/octet-stream")
