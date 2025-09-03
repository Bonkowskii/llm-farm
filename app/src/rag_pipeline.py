import os, sys, json
from pathlib import Path
from typing import List
from dotenv import load_dotenv

from .chunking import index_docs
from .retrieval import build_embeddings, topk_for_query

def _env():
    load_dotenv()
    return {
        "LLM": os.getenv("LLM", "Qwen/Qwen2.5-1.5B-Instruct"),
        "CTX_CAP_TOKENS": int(os.getenv("CTX_CAP_TOKENS", "4096")),
        "MAX_NEW_TOKENS": int(os.getenv("MAX_NEW_TOKENS", "512")),
        "MIN_ANS_TOKENS": int(os.getenv("MIN_ANS_TOKENS", "192")),
        "PER_HIT_MIN_TOKENS": int(os.getenv("PER_HIT_MIN_TOKENS", "64")),
        "PER_HIT_HARD_CAP": int(os.getenv("PER_HIT_HARD_CAP", "0")),
        "DEVICE_MAP": os.getenv("DEVICE_MAP", "cpu"),
    }

def _format_prompt(question: str, hits: List[dict]) -> str:
    ctx = "\n\n".join([f"[{h['doc']}#{h['i']} | score={h['score']:.3f}]\n{h['text']}" for h in hits])
    return (
        "Jesteś asystentem odpowiadającym WYŁĄCZNIE na podstawie kontekstu.\n"
        "Jeśli brakuje danych w kontekście — napisz: 'Brak w materiale.'\n\n"
        f"Pytanie: {question}\n\nKontekst:\n{ctx}\n\n<final>"
    )

def _generate_llm(prompt: str) -> str:
    # Minimalny generator via transformers (HF), CPU/GPU zależnie od DEVICE_MAP
    cfg = _env()
    from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
    tok = AutoTokenizer.from_pretrained(cfg["LLM"])
    model = AutoModelForCausalLM.from_pretrained(cfg["LLM"], device_map=cfg["DEVICE_MAP"])
    pipe = pipeline("text-generation", model=model, tokenizer=tok)
    out = pipe(prompt, max_new_tokens=cfg["MAX_NEW_TOKENS"], do_sample=False)[0]["generated_text"]
    # Przytnij po znaczniku
    if "</final>" in out:
        out = out.split("<final>", 1)[-1]
        out = out.split("</final>", 1)[0]
    return out.strip()

def reindex():
    ch, man = index_docs()
    build_embeddings(ch)

def ask(question: str) -> str:
    hits = topk_for_query(question, k=12)
    prompt = _format_prompt(question, hits)
    return _generate_llm(prompt)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Użycie: python -m src.rag_pipeline [ask|reindex] ...")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "reindex":
        reindex(); print("OK: reindex done.")
    elif cmd == "ask":
        q = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else input("Pytanie: ")
        print(ask(q))
    else:
        print("Nieznana komenda.")
