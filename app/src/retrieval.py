import json, os
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple
from sentence_transformers import SentenceTransformer

ARTIFACTS = Path("artifacts")
ARTIFACTS.mkdir(exist_ok=True)

def _load_env():
    from dotenv import load_dotenv
    load_dotenv()
    min_score = float(os.getenv("MIN_SCORE", "0.40"))
    embedder_id = os.getenv("EMBEDDER", "intfloat/multilingual-e5-small")
    return embedder_id, min_score

def build_embeddings(chunks_jsonl: Path) -> Tuple[Path, Path, Path]:
    embedder_id, _ = _load_env()
    model = SentenceTransformer(embedder_id)

    texts, meta = [], []
    with chunks_jsonl.open("r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            texts.append(rec["text"])
            meta.append({"doc": rec["doc"], "i": rec["i"]})

    if len(texts) == 0:
        # zapisz „puste” artefakty w spójny sposób
        emb = np.zeros((0, model.get_sentence_embedding_dimension()), dtype=np.float32)
    else:
        emb = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=True)

    emb_path = ARTIFACTS / "embeddings.npy"
    info_path = ARTIFACTS / "embeddings.info.json"
    meta_path = ARTIFACTS / "meta.jsonl"

    np.save(emb_path, emb)
    info_path.write_text(json.dumps({
        "embedder": embedder_id,
        "count": len(texts),
        "dim": int(emb.shape[1] if emb.ndim == 2 else 0),
    }, indent=2), encoding="utf-8")

    with meta_path.open("w", encoding="utf-8") as mf:
        for m, t in zip(meta, texts):
            m2 = {**m, "text": t}
            mf.write(json.dumps(m2, ensure_ascii=False) + "\n")

    return emb_path, info_path, meta_path

def _load_npy_and_meta() -> Tuple[np.ndarray, List[Dict]]:
    emb_path = ARTIFACTS / "embeddings.npy"
    meta_path = ARTIFACTS / "meta.jsonl"
    if not emb_path.exists():
        raise FileNotFoundError("Brak embeddings.npy – zrób /api/reindex")

    emb = np.load(emb_path, allow_pickle=False)
    meta = []
    if meta_path.exists():
        with meta_path.open("r", encoding="utf-8") as f:
            for line in f:
                meta.append(json.loads(line))
    return emb, meta

def topk_for_query(query: str, k: int = 8) -> List[Dict]:
    embedder_id, min_score = _load_env()
    model = SentenceTransformer(embedder_id)
    q = model.encode([query], convert_to_numpy=True, normalize_embeddings=True)[0]

    M, meta = _load_npy_and_meta()

    # Pusty indeks – zwróć po prostu pustą listę
    if M.ndim != 2 or M.shape[0] == 0 or M.shape[1] == 0:
        return []

    # Zmieniłeś embedder i nie zrobiłeś reindex? Zgłoś czytelny błąd.
    if M.shape[1] != q.shape[0]:
        raise RuntimeError(
            f"Embeddings dim mismatch: dokumenty={M.shape[1]} vs zapytanie={q.shape[0]}. "
            "Usuń artifacts/* i zrób reindex po zmianie EMBEDDER."
        )

    sims = (M @ q)  # kosinus przy znormalizowanych wektorach
    idx = np.argsort(-sims)[: max(k, 1)]
    hits = []
    for i in idx:
        if sims[i] < min_score:
            continue
        hits.append({"score": float(sims[i]), **meta[int(i)]})
    return hits
