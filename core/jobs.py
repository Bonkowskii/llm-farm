# core/jobs.py
from __future__ import annotations
import asyncio, uuid
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple, List, AsyncIterator
from datetime import datetime, timezone
from contextlib import suppress

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

@dataclass
class Job:
    id: str
    req: Dict[str, Any]           # AskRequest as dict
    priority: int = 5             # 0 = najwyższy
    status: str = "queued"        # queued | running | done | error
    enqueued_at: str = field(default_factory=_iso_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    device: Optional[Dict[str, Any]] = None   # {"host","port","serial"}
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    # streaming
    stream: bool = False
    events: Optional[asyncio.Queue] = None    # Queue[Optional[bytes]]; None = sentinel

class JobsEngine:
    """
    Kolejka zadań:
    - enqueue(): non-stream (worker woła _post_chat)
    - enqueue_stream(): stream (worker woła _stream_chat i publikuje bajty na kolejkę)
    """
    def __init__(self, gateway):
        self.gateway = gateway
        self.q: "asyncio.PriorityQueue[Tuple[int,int,str]]" = asyncio.PriorityQueue()
        self.jobs: Dict[str, Job] = {}
        self._seq = 0
        self._workers: List[asyncio.Task] = []
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    async def start(self, worker_count: int):
        worker_count = max(1, int(worker_count))
        for i in range(worker_count):
            self._workers.append(asyncio.create_task(self._worker(i)))

    async def stop(self):
        self._stop_event.set()
        for w in self._workers:
            w.cancel()
            with suppress(asyncio.CancelledError):
                await w

    async def enqueue(self, req: Dict[str, Any], priority: int = 5) -> str:
        job_id = uuid.uuid4().hex
        job = Job(id=job_id, req=req, priority=int(priority))
        async with self._lock:
            self.jobs[job_id] = job
            self._seq += 1
            await self.q.put((job.priority, self._seq, job_id))
        return job_id

    async def enqueue_stream(self, req: Dict[str, Any], priority: int = 5) -> str:
        job_id = uuid.uuid4().hex
        job = Job(id=job_id, req=req, priority=int(priority), stream=True, events=asyncio.Queue())
        async with self._lock:
            self.jobs[job_id] = job
            self._seq += 1
            await self.q.put((job.priority, self._seq, job_id))
        return job_id

    async def get_status(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    async def stream_job(self, job_id: str) -> AsyncIterator[bytes]:
        job = self.jobs.get(job_id)
        if not job or not job.stream or job.events is None:
            # brak streamu dla tego joba – nic do wysłania
            return
        while True:
            item = await job.events.get()
            if item is None:
                break
            yield item

    async def _worker(self, worker_idx: int):
        while not self._stop_event.is_set():
            try:
                priority, seq, job_id = await self.q.get()
            except asyncio.CancelledError:
                break
            job = self.jobs.get(job_id)
            if job is None:
                self.q.task_done()
                continue
            job.status = "running"
            job.started_at = _iso_now()

            try:
                phone = await self.gateway._next_phone()
                payload = self.gateway._build_payload(_DictToAsk(job.req), fallback=phone.cfg.model)
                job.device = {"host": phone.cfg.host, "port": phone.cfg.port, "serial": phone.cfg.serial}

                if job.stream and job.events is not None:
                    # lekki nagłówek dla czytelności (opcjonalny)
                    await job.events.put(f"# picked {phone.cfg.host}:{phone.cfg.port} model={payload.get('model')}\n".encode())
                    await job.events.put(b"# posting (streaming)...\n")
                    # strumień 1:1 z telefonu
                    async for chunk in self.gateway._stream_chat(phone, payload):
                        if chunk:
                            await job.events.put(chunk)
                    await job.events.put(b"\n# done\n")
                    job.status = "done"
                else:
                    # non-stream
                    result = await self.gateway._post_chat(phone, payload)
                    job.result = result
                    job.status = "done"
            except Exception as e:
                job.error = str(e)
                job.status = "error"
                if job.stream and job.events is not None:
                    await job.events.put(f'# error: {e}\n'.encode())
            finally:
                job.finished_at = _iso_now()
                if job.stream and job.events is not None:
                    # zamknij strumień
                    await job.events.put(None)  # sentinel
                self.q.task_done()

class _DictToAsk:
    def __init__(self, d: Dict[str, Any]):
        self.prompt = d.get("prompt") or ""
        self.system = d.get("system")
        self.model = d.get("model")
        self.options = d.get("options") or {}
