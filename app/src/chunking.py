import json, os
from pathlib import Path
from typing import List, Tuple

ARTIFACTS = Path("artifacts")
DOCS = Path("docs")
ARTIFACTS.mkdir(exist_ok=True)

def _sentences_spacy(text: str) -> List[str]:
    try:
        import spacy
        try:
            nlp = spacy.load("pl_core_news_sm")
        except Exception:
            nlp = spacy.blank("pl"); nlp.add_pipe("sentencizer")
        doc = nlp(text)
        return [s.text.strip() for s in doc.sents if s.text.strip()]
    except Exception:
        # Fallback: „kropkowy”
        import re
        parts = re.split(r"(?<=[.!?])\s+", text)
        return [p.strip() for p in parts if p.strip()]

def chunk_text(text: str, max_sents: int = 6, overlap: int = 2) -> List[str]:
    sents = _sentences_spacy(text)
    chunks = []
    i = 0
    while i < len(sents):
        chunk = " ".join(sents[i:i+max_sents]).strip()
        if chunk:
            chunks.append(chunk)
        i += max_sents - overlap if max_sents > overlap else max_sents
    return chunks

def index_docs(docs_dir: Path = DOCS, out_dir: Path = ARTIFACTS) -> Tuple[Path, Path]:
    """
    Przetwarza pliki .txt/.md -> artifacts/chunks.jsonl + manifest.json
    """
    chunks_path = out_dir / "chunks.jsonl"
    manifest_path = out_dir / "manifest.json"
    with chunks_path.open("w", encoding="utf-8") as chf:
        manifest = []
        for p in sorted(docs_dir.glob("**/*")):
            if not p.is_file() or p.suffix.lower() not in {".txt", ".md"}:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            chunks = chunk_text(text)
            for idx, ch in enumerate(chunks):
                rec = {"doc": str(p), "i": idx, "text": ch}
                chf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            manifest.append({"doc": str(p), "chunks": len(chunks)})
    manifest_path.write_text(json.dumps({"items": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
    return chunks_path, manifest_path
