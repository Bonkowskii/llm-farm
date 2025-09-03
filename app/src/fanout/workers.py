from __future__ import annotations
import os, requests, concurrent.futures
from typing import Any, Dict, List

def get_workers() -> List[str]:
    csv = os.getenv("WORKERS_CSV", "http://localhost:9001,http://localhost:9002")
    return [w.strip() for w in csv.split(",") if w.strip()]

TIMEOUT = int(os.getenv("WORKER_TIMEOUT", "60"))

def call_worker(worker_url: str, prompt: str, n_predict: int = 64, temperature: float = 0.2) -> str:
    # 1) lokalny llama_cpp.server (OpenAI-style)
    try:
        url = worker_url.rstrip("/") + "/v1/chat/completions"
        body = {"model": "local","messages":[{"role":"user","content":prompt}],
                "max_tokens": n_predict,"temperature": temperature,"stream": False}
        r = requests.post(url, json=body, timeout=TIMEOUT); r.raise_for_status()
        data = r.json(); ch = data.get("choices", [])
        if ch and isinstance(ch[0], dict) and "message" in ch[0]:
            return ch[0]["message"].get("content", str(data))
    except Exception:
        pass
    # 2) stub /completion
    url = worker_url.rstrip("/") + "/completion"
    r = requests.post(url, json={"prompt":prompt,"n_predict":n_predict,"temperature":temperature,"stream":False}, timeout=TIMEOUT)
    r.raise_for_status(); data = r.json()
    return data.get("content") or data.get("completion") or str(data)

def fanout(prompts: List[str], n_predict: int = 96, temperature: float = 0.1) -> List[Dict[str, Any]]:
    workers = get_workers()
    if not workers: return []
    jobs, meta = [], []
    maxw = min(len(workers), len(prompts)) or 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=maxw) as ex:
        for i, p in enumerate(prompts):
            w = workers[i % len(workers)]
            jobs.append(ex.submit(call_worker, w, p, n_predict, temperature))
            meta.append({"idx": i, "worker": w})
        outs = []
        for m, f in zip(meta, jobs):
            try: out = f.result(timeout=TIMEOUT)
            except Exception as e: out = f"[ERROR] {e}"
            outs.append({**m, "output": out})
    return outs
