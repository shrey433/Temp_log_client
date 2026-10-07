import hmac
import os
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import ValidationError

from .db import Database
from .models import Envelope, Row

MAX_BODY_BYTES = 64 * 1024


def create_app(token: str | None = None, db_path: str | None = None) -> FastAPI:
    token = token or os.environ.get("RTD_API_TOKEN", "")
    if not token:
        raise RuntimeError("RTD_API_TOKEN must be set (it must equal DEVICE_TOKEN in the firmware's secrets.h)")
    db = Database(db_path or os.environ.get("RTD_DB_PATH", "rtd.db"))
    app = FastAPI(title="RTD logger server")

    def require_token(authorization: str = Header(default="")) -> None:
        scheme, _, supplied = authorization.partition(" ")
        if scheme != "Bearer" or not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="invalid token")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/ingest", dependencies=[Depends(require_token)])
    async def ingest(request: Request) -> dict:
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="payload too large")
        try:
            env = Envelope.model_validate_json(body)
        except ValidationError as e:
            # 422 tells the firmware the payload itself is bad, so it drops the rows
            raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False, include_input=False))

        valid: list[dict] = []
        rejected = 0
        for raw in env.readings:
            try:
                row = Row.model_validate(raw)
            except ValidationError:
                rejected += 1
                continue
            valid.append({"ts": row.ts, "channels": [c.model_dump() for c in row.channels]})

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        accepted, duplicates = db.store(env.device_id, env.fw_version, now, valid)
        return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}

    @app.get("/api/devices", dependencies=[Depends(require_token)])
    def devices() -> list[dict]:
        return db.devices()

    @app.get("/api/devices/{device_id}/readings", dependencies=[Depends(require_token)])
    def readings(
        device_id: str,
        since: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict]:
        return db.readings(device_id, since, limit)

    return app
