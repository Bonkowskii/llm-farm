import re
_BAD = ["Zero opinii","lania wody","Wynik","FRAGMENT_START","FRAGMENT_END","fragment start","fragment end"]
_BULLET_RE = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)(.+)$")

def _drop_line(line: str) -> bool:
    low = line.lower(); return any(b.lower() in low for b in _BAD)

def _first_sentence(s: str) -> str:
    s = s.strip().strip("`\"'•-* ")
    m = re.split(r"([.!?])", s, maxsplit=1)
    return (m[0] + m[1]).strip() if len(m) >= 2 else s

def bullets_from_text(raw: str, limit: int = 12) -> list[str]:
    lines = (raw or "").splitlines(); out, seen = [], set()
    for ln in lines:
        if not ln.strip() or _drop_line(ln): continue
        m = _BULLET_RE.match(ln)
        cand = m.group(1).strip() if m else ln.strip()
        sent = _first_sentence(cand); key = sent.lower()
        if not key or key == "brak" or key in seen: continue
        seen.add(key); out.append(f"- {sent}")
        if len(out) >= limit: break
    return out
