# core/store.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import tempfile
import os

DYNAMIC_KEYS = {
    "healthy", "reason", "inflight", "models",
    "last_ok_at", "last_error_at", "open_until"
}

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

class DeviceStore:
    """
    phones.json store:
    - NIE nadpisuje pól konfiguracyjnych (host, port, model, weight, max_concurrency, serial)
    - Aktualizuje TYLKO dynamiczne (DYNAMIC_KEYS)
    - NIE dodaje nowych rekordów (brak autodiscovery)
    - Deduplikacja po serial, a gdy brak – po "host:port"
    """
    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: List[Dict[str, Any]] = []
        self._index: Dict[str, int] = {}
        self._dirty = False
        self.reload()

    def _key_for(self, entry: Dict[str, Any]) -> Optional[str]:
        serial = entry.get("serial")
        host = entry.get("host"); port = entry.get("port")
        if serial: return serial
        if host and port is not None: return f"{host}:{port}"
        return None

    def reload(self) -> None:
        raw = json.loads(self.path.read_text())
        if not isinstance(raw, list):
            raise ValueError("phones.json must be a JSON array")
        self._data = raw
        self._rebuild_index()
        self._dirty = False

    def _rebuild_index(self) -> None:
        self._index.clear()
        for i, e in enumerate(self._data):
            k = self._key_for(e)
            if k is not None and k not in self._index:
                self._index[k] = i

    def get_entry_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        idx = self._index.get(key)
        return self._data[idx] if idx is not None else None

    def get_snapshot(self) -> List[Dict[str, Any]]:
        return self._data  # tylko do odczytu

    def update_dynamic(self, key: str, fields: Dict[str, Any]) -> None:
        idx = self._index.get(key)
        if idx is None:
            return  # nie dopisujemy nowych urządzeń
        entry = self._data[idx]
        changed = False
        for k, v in fields.items():
            if k not in DYNAMIC_KEYS:
                continue
            if entry.get(k) != v:
                entry[k] = v
                changed = True
        if changed:
            self._dirty = True

    def mark_ok(self, key: str) -> None:
        self.update_dynamic(key, {"last_ok_at": _iso_now(), "last_error_at": None})

    def mark_error(self, key: str) -> None:
        self.update_dynamic(key, {"last_error_at": _iso_now()})

    def flush_if_dirty(self) -> None:
        if not self._dirty:
            return
        tmpfd, tmppath = tempfile.mkstemp(prefix="phones.", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(tmpfd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmppath, self.path)
            self._dirty = False
        finally:
            try:
                if os.path.exists(tmppath):
                    os.remove(tmppath)
            except Exception:
                pass
