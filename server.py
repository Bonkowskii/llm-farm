import asyncio, json, logging, random, time, contextlib, hashlib, sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import StreamingResponse, PlainTextResponse
from pydantic import BaseModel, Field
from collections import OrderedDict

from core.store import DeviceStore
from routers.devices import router as devices_router

# logger
logger = logging.getLogger("gateway")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(h)
logger.propagate = False

# Configuration
API_KEY_REQUIRED = False
API_KEY_VALUE = ""
HEALTH_INTERVAL_S = 10
CB_FAIL_THRESHOLD = 3
CB_OPEN_SECONDS = 30
POST_TIMEOUT_S = None
STREAM_TIMEOUT_S = None
ENABLE_LRU_CACHE = True
LRU_MAX_ITEMS = 128

class AskRequest(BaseModel):
    prompt: str
    system: Optional[str] = None
    model: Optional[str] = None
    options: Dict[str, Any] = Field(default_factory=dict)

class AskBatchRequest(BaseModel):
    requests: List[AskRequest]

class HealthPhone(BaseModel):
    host: str; port: int; model: Optional[str]
    healthy: bool; reason: Optional[str] = None; inflight: int

class HealthResponse(BaseModel):
    phones: List[HealthPhone]

class LRUCache:
    def __init__(self, max_items: int = 128):
        self.max = max_items
        self.store: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.lock = asyncio.Lock()
    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            val = self.store.get(key)
            if val is not None: self.store.move_to_end(key)
            return val
    async def set(self, key: str, val: Dict[str, Any]):
        async with self.lock:
            self.store[key] = val; self.store.move_to_end(key)
            if len(self.store) > self.max: self.store.popitem(last=False)

def cache_key(req: AskRequest, fallback_model: Optional[str]) -> str:
    payload = {"prompt": req.prompt, "system": req.system,
               "model": req.model or fallback_model, "options": req.options}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

@dataclass
class PhoneConfig:
    host: str; port: int = 11434
    model: Optional[str] = None; weight: int = 1
    max_concurrency: int = 1
    serial: Optional[str] = None

@dataclass
class PhoneState:
    cfg: PhoneConfig
    healthy: bool = False; reason: Optional[str] = "unknown"
    inflight: int = 0; failures: int = 0; open_until: float = 0.0
    semaphore: asyncio.Semaphore = field(init=False)
    def __post_init__(self): self.semaphore = asyncio.Semaphore(self.cfg.max_concurrency)

def load_phones_config() -> List[PhoneConfig]:
    p = Path(__file__).parent / "phones.json"
    raw = json.loads(p.read_text())
    return [PhoneConfig(host=item["host"],
                        port=int(item.get("port", 11434)),
                        model=item.get("model"),
                        weight=int(item.get("weight", 1)),
                        max_concurrency=int(item.get("max_concurrency", 1)),
                        serial=item.get("serial"))
            for item in raw]

class Metrics:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.total_requests = 0; self.total_failures = 0
        self.latency_sum = 0.0; self.phone_hits: Dict[str, int] = {}
    async def mark(self, phone: Optional["PhoneState"], ok: bool, latency: float):
        async with self.lock:
            self.total_requests += 1
            if ok: self.latency_sum += latency
            else: self.total_failures += 1
            if phone:
                key = f"{phone.cfg.host}:{phone.cfg.port}"
                self.phone_hits[key] = self.phone_hits.get(key, 0) + 1
    async def render_prom(self) -> str:
        async with self.lock:
            successes = max(1, self.total_requests - self.total_failures)
            avg = self.latency_sum / successes
            lines = [
                "# HELP gw_requests_total Total requests",
                "# TYPE gw_requests_total counter",
                f"gw_requests_total {self.total_requests}",
                "# HELP gw_failures_total Total failed requests",
                "# TYPE gw_failures_total counter",
                f"gw_failures_total {self.total_failures}",
                "# HELP gw_latency_seconds_avg Average success latency",
                "# TYPE gw_latency_seconds_avg gauge",
                f"gw_latency_seconds_avg {avg:.6f}",
            ]
            for k, v in self.phone_hits.items():
                lines.append(f'gw_phone_hits_total{{phone=\"{k}\"}} {v}')
            return "\n".join(lines) + "\n"

class Gateway:
    def __init__(self, cfgs: List[PhoneConfig], store: Optional[DeviceStore] = None):
        weighted: List[PhoneState] = []
        for cfg in cfgs:
            st = PhoneState(cfg=cfg)
            weighted.extend([st] * max(1, cfg.weight))
        self.rr: List[PhoneState] = weighted
        self._rr_idx = 0; self._rr_lock = asyncio.Lock()
        self._hc_task: Optional[asyncio.Task] = None
        self.metrics = Metrics()
        self.cache = LRUCache(LRU_MAX_ITEMS) if ENABLE_LRU_CACHE else None
        self.store = store

    def _devkey(self, cfg: PhoneConfig) -> str:
        return cfg.serial or f"{cfg.host}:{cfg.port}"

    async def start(self):
        self._hc_task = asyncio.create_task(self._health_loop())
    async def stop(self):
        if self._hc_task:
            self._hc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._hc_task

    async def _health_loop(self):
        while True:
            unique = {id(x): x for x in self.rr}.values()
            await asyncio.gather(*(self._health_check(p) for p in unique))
            if self.store:
                self.store.flush_if_dirty()
            await asyncio.sleep(HEALTH_INTERVAL_S)

    async def _health_check(self, phone: PhoneState):
        now = asyncio.get_event_loop().time()
        key = self._devkey(phone.cfg)
        if phone.open_until > now:
            phone.healthy, phone.reason = False, "circuit_open"
            if self.store:
                self.store.update_dynamic(key, {
                    "healthy": False, "reason": "circuit_open",
                    "inflight": phone.inflight, "open_until": phone.open_until
                })
            return
        try:
            url = f"http://{phone.cfg.host}:{phone.cfg.port}/api/tags"
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
            models = [m.get("name") for m in (data.get("models") or []) if m.get("name")]
            phone.healthy, phone.reason, phone.failures = True, None, 0
            if self.store:
                self.store.update_dynamic(key, {
                    "healthy": True, "reason": None,
                    "inflight": phone.inflight, "open_until": phone.open_until,
                    "models": sorted(set(models)),
                })
                self.store.mark_ok(key)
            logger.info("[health] OK %s:%d", phone.cfg.host, phone.cfg.port)
        except Exception as e:
            phone.healthy, phone.reason = False, f"health_fail: {e}"
            phone.failures += 1
            if phone.failures >= CB_FAIL_THRESHOLD:
                phone.open_until = now + CB_OPEN_SECONDS
            if self.store:
                self.store.update_dynamic(key, {
                    "healthy": False, "reason": str(e),
                    "inflight": phone.inflight, "open_until": phone.open_until,
                })
                self.store.mark_error(key)
            logger.warning("[health] FAIL %s:%d -> %s", phone.cfg.host, phone.cfg.port, e)

    async def _next_phone(self) -> PhoneState:
        async with self._rr_lock:
            n = len(self.rr)
            for _ in range(n):
                st = self.rr[self._rr_idx]
                self._rr_idx = (self._rr_idx + 1) % n
                if st.healthy and st.open_until <= asyncio.get_event_loop().time():
                    return st
        return random.choice(self.rr)

    def _build_payload(self, req: AskRequest, fallback: Optional[str]) -> Dict[str, Any]:
        messages = []
        if req.system: messages.append({"role":"system","content":req.system})
        messages.append({"role":"user","content":req.prompt})
        payload: Dict[str, Any] = {"messages": messages, "stream": False}
        if req.options: payload["options"] = req.options
        if req.model: payload["model"] = req.model
        elif fallback: payload["model"] = fallback
        return payload

    async def _post_chat(self, phone: PhoneState, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"http://{phone.cfg.host}:{phone.cfg.port}/api/chat"
        backoff = 0.5; last_exc: Optional[Exception] = None
        t0 = time.perf_counter()
        for _ in range(3):
            try:
                async with phone.semaphore:
                    phone.inflight += 1
                    try:
                        async with httpx.AsyncClient(timeout=POST_TIMEOUT_S) as client:
                            resp = await client.post(url, json=payload); resp.raise_for_status()
                            await self.metrics.mark(phone, True, time.perf_counter()-t0)
                            phone.failures = 0
                            return resp.json()
                    finally:
                        phone.inflight -= 1
            except Exception as e:
                last_exc = e
                await self.metrics.mark(phone, False, time.perf_counter()-t0)
                phone.failures += 1
                if phone.failures >= CB_FAIL_THRESHOLD:
                    phone.open_until = asyncio.get_event_loop().time() + CB_OPEN_SECONDS
                await asyncio.sleep(backoff); backoff *= 2
        raise last_exc or RuntimeError("unknown error")

    async def _stream_chat(self, phone: PhoneState, payload: Dict[str, Any]) -> AsyncIterator[bytes]:
        url = f"http://{phone.cfg.host}:{phone.cfg.port}/api/chat"
        payload_stream = {**payload, "stream": True}
        backoff = 0.5
        for _ in range(3):
            try:
                async with phone.semaphore:
                    phone.inflight += 1
                    try:
                        async with httpx.AsyncClient(timeout=STREAM_TIMEOUT_S) as client:
                            async with client.stream("POST", url, json=payload_stream) as resp:
                                resp.raise_for_status()
                                async for chunk in resp.aiter_bytes():
                                    if chunk: yield chunk
                                phone.failures = 0
                                return
                    finally:
                        phone.inflight -= 1
            except Exception:
                phone.failures += 1
                if phone.failures >= CB_FAIL_THRESHOLD:
                    phone.open_until = asyncio.get_event_loop().time() + CB_OPEN_SECONDS
                await asyncio.sleep(backoff); backoff *= 2
        return

    def health_snapshot(self) -> HealthResponse:
        phones = []
        seen = set()
        for st in self.rr:
            if id(st) in seen: continue
            seen.add(id(st))
            phones.append(HealthPhone(
                host=st.cfg.host, port=st.cfg.port, model=st.cfg.model,
                healthy=st.healthy, reason=st.reason, inflight=st.inflight))
        return HealthResponse(phones=phones)

app = FastAPI(title="Distributed LLM Mobile Gateway", version="2.0.0")
gateway: Optional[Gateway] = None
store: Optional[DeviceStore] = None

# API
app.include_router(devices_router)

def require_api_key(x_api_key: Optional[str]):
    if API_KEY_REQUIRED and x_api_key != API_KEY_VALUE:
        raise HTTPException(status_code=401, detail="Invalid API key")

@app.on_event("startup")
async def startup():
    global gateway, store
    phones_path = Path(__file__).parent / "phones.json"
    store = DeviceStore(phones_path)
    cfgs = load_phones_config()
    gateway = Gateway(cfgs, store=store)
    await gateway.start()
    app.state.gateway = gateway
    app.state.store = store
    logger.info("Gateway ready with %d weighted entries.", len(gateway.rr))

@app.on_event("shutdown")
async def shutdown():
    global gateway
    if gateway: await gateway.stop()

@app.get("/metrics")
async def metrics():
    text = await gateway.metrics.render_prom()
    return PlainTextResponse(text, media_type="text/plain")

@app.get("/ping")
async def ping():
    unique = {id(x): x for x in gateway.rr}.values()
    out = []
    for p in unique:
        url = f"http://{p.cfg.host}:{p.cfg.port}/api/tags"
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(url)
            out.append({
                "host": p.cfg.host, "port": p.cfg.port,
                "ok": r.status_code == 200, "status": r.status_code,
                "ms": int((time.perf_counter() - t0) * 1000)
            })
        except Exception as e:
            out.append({
                "host": p.cfg.host, "port": p.cfg.port,
                "ok": False, "error": str(e),
                "ms": int((time.perf_counter() - t0) * 1000)
            })
    return {"results": out}

@app.post("/ask_trace")
async def ask_trace(req: AskRequest, x_api_key: Optional[str] = Header(default=None)):
    require_api_key(x_api_key)
    async def _gen():
        unique = list({id(x): x for x in gateway.rr}.values())
        yield f"# phones={len(unique)}\n".encode()
        phone = await gateway._next_phone()
        fallback_model = phone.cfg.model
        payload = gateway._build_payload(req, fallback_model)
        yield f"# selected {phone.cfg.host}:{phone.cfg.port} model={payload.get('model')}\n".encode()
        yield b"# posting to phone (streaming)...\n"
        async for chunk in gateway._stream_chat(phone, payload):
            if chunk:
                yield chunk
        yield b"\n# done\n"
    return StreamingResponse(_gen(), media_type="text/plain")

@app.get("/health", response_model=HealthResponse)
async def health():
    return gateway.health_snapshot()

@app.post("/warmup")
async def warmup(x_api_key: Optional[str] = Header(default=None)):
    require_api_key(x_api_key)
    unique = {id(x): x for x in gateway.rr}.values()
    async def _warm(p: PhoneState):
        req = AskRequest(prompt=".", options={"num_predict":16})
        payload = gateway._build_payload(req, p.cfg.model)
        try:
            await gateway._post_chat(p, payload); return True
        except Exception: return False
    results = await asyncio.gather(*(_warm(p) for p in unique))
    return {"warmed": sum(1 for r in results if r), "total": len(list(unique))}

@app.post("/ask")
async def ask(req: AskRequest, x_api_key: Optional[str] = Header(default=None)):
    require_api_key(x_api_key)
    if ENABLE_LRU_CACHE:
        tmp_phone = await gateway._next_phone()
        key = cache_key(req, tmp_phone.cfg.model)
        cached = await gateway.cache.get(key)
        if cached: return cached
        fallback_phone = tmp_phone
    else:
        fallback_phone = None

    unique = {id(x): x for x in gateway.rr}.values()
    last_error: Optional[Exception] = None
    for _ in range(len(list(unique))):
        phone = await gateway._next_phone()
        payload = gateway._build_payload(req, phone.cfg.model)
        logger.info(f"[ask] trying phone={phone.cfg.host}:{phone.cfg.port} "
                    f"model={payload.get('model')} healthy={phone.healthy} inflight={phone.inflight}")
        try:
            result = await gateway._post_chat(phone, payload)
            logger.info(f"[ask] success phone={phone.cfg.host}:{phone.cfg.port}")
            if ENABLE_LRU_CACHE and fallback_phone:
                k2 = cache_key(req, fallback_phone.cfg.model)
                await gateway.cache.set(k2, result)
            return result
        except Exception as e:
            logger.warning(f"[ask] failed phone={phone.cfg.host}:{phone.cfg.port}: {e}")
            last_error = e
            continue

    states = [{"host": p.cfg.host, "port": p.cfg.port, "healthy": p.healthy, "reason": p.reason,
               "inflight": p.inflight, "open_until": p.open_until}
              for p in {id(x): x for x in gateway.rr}.values()]
    raise HTTPException(
        status_code=503,
        detail=f"No phones responded. last_error={last_error!s}; states={states}"
    )

@app.post("/ask_stream")
async def ask_stream(req: AskRequest, x_api_key: Optional[str] = Header(default=None)):
    require_api_key(x_api_key)
    phone = await gateway._next_phone()
    payload = gateway._build_payload(req, phone.cfg.model)
    async def _gen():
        async for chunk in gateway._stream_chat(phone, payload):
            if chunk: yield chunk
    return StreamingResponse(_gen(), media_type="application/octet-stream")

@app.post("/ask_batch")
async def ask_batch(req: AskBatchRequest, x_api_key: Optional[str] = Header(default=None)):
    require_api_key(x_api_key)
    async def _do(single: AskRequest):
        try:
            phone = await gateway._next_phone()
            payload = gateway._build_payload(single, phone.cfg.model)
            result = await gateway._post_chat(phone, payload)
            return True, result
        except Exception as e:
            return False, str(e)
    results = await asyncio.gather(*(_do(r) for r in req.requests))
    return {"results": [{"ok": ok, "data": data} for ok, data in results]}
