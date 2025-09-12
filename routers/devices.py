# routers/devices.py
from fastapi import APIRouter, Request, HTTPException

router = APIRouter()

@router.get("/devices")
async def list_devices(request: Request):
    """
    Jeden widok urządzeń:
    - stałe (z phones.json): host, port, serial, weight, max_concurrency, default_model
    - runtime: healthy, reason, inflight, open_until
    - ostatnio wykryte modele + timestampe: models, last_ok_at, last_error_at
    """
    app = request.app
    gw = getattr(app.state, "gateway", None)
    store = getattr(app.state, "store", None)
    if gw is None or store is None:
        raise HTTPException(status_code=503, detail="Gateway not ready")

    unique = {id(x): x for x in gw.rr}.values()
    out = []
    for st in unique:
        cfg = st.cfg
        key = cfg.serial or f"{cfg.host}:{cfg.port}"
        saved = store.get_entry_by_key(key) or {}
        out.append({
            "id": key,
            "host": cfg.host,
            "port": cfg.port,
            "serial": cfg.serial,
            "weight": cfg.weight,
            "max_concurrency": cfg.max_concurrency,
            "default_model": cfg.model,
            "healthy": st.healthy,
            "reason": st.reason,
            "inflight": st.inflight,
            "open_until": st.open_until,
            "models": saved.get("models", []),
            "last_ok_at": saved.get("last_ok_at"),
            "last_error_at": saved.get("last_error_at"),
        })
    return {"object": "list", "data": out}
