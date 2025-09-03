from typing import List
from .workers import call_worker, fanout as fanout_call, get_workers
from .utils_cleanup import bullets_from_text

MAP_PROMPT = (
    "Z TEGO FRAGMENTU wypisz 3–6 NAJWAŻNIEJSZYCH FAKTÓW po polsku.\n"
    "- Każdy punkt JEDNO zdanie.\n"
    "- BEZ opinii i BEZ powtarzania instrukcji.\n"
    "FRAGMENT_START\n{chunk}\nFRAGMENT_END\n"
    "Wynik:\n"
)
FINAL_PROMPT = (
    "Masz listę faktów (każdy 1 zdanie). Odpowiedz zwięźle na pytanie, "
    "opierając się WYŁĄCZNIE na tych faktach. Jeśli danych brak, napisz: 'Brak w materiale.'\n\n"
    "Pytanie: {question}\n\nFakty:\n{facts}\n\nOdpowiedź:"
)

def chunk_text(text: str, max_chars: int = 800) -> List[str]:
    s = (text or "").strip()
    if not s: return []
    out, buf, count = [], [], 0
    for w in s.split():
        t = w + " "
        if count + len(t) > max_chars and buf:
            out.append("".join(buf).strip()); buf, count = [], 0
        buf.append(t); count += len(t)
    if buf: out.append("".join(buf).strip())
    return out

def map_extract_single(chunk: str, n_predict: int = 96) -> list[str]:
    w = get_workers()[0] if get_workers() else "http://localhost:9001"
    raw = call_worker(w, MAP_PROMPT.format(chunk=chunk), n_predict=n_predict, temperature=0.1)
    return bullets_from_text(raw)

def map_fanout(text: str, max_chars: int = 800, n_predict: int = 96):
    chs = chunk_text(text, max_chars=max_chars)
    prompts = [MAP_PROMPT.format(chunk=c) for c in chs]
    results = fanout_call(prompts, n_predict=n_predict, temperature=0.1)
    enriched = [{**r, "bullets": bullets_from_text(r["output"])} for r in results]
    return {"num_chunks": len(chs), "results": enriched}

def reduce_to_answer(bullets: List[str], question: str, n_predict: int = 256) -> dict:
    # deduplikacja + limit 12 faktów
    seen, clean = set(), []
    for b in bullets:
        line = b.strip().lstrip("-•* 1234567890.).(").strip()
        if not line or line.lower() == "brak": continue
        key = line.lower()
        if key in seen: continue
        seen.add(key); clean.append(f"- {line}")
        if len(clean) >= 12: break
    facts = "\n".join(clean) if clean else "- Brak"
    w = get_workers()[0] if get_workers() else "http://localhost:9001"
    out = call_worker(w, FINAL_PROMPT.format(question=question, facts=facts), n_predict=n_predict, temperature=0.2)
    return {"worker": w, "facts_used": clean, "output": out}
