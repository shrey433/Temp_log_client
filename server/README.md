# RTD logger server

Receives the readings posted by the firmware (`../firmware`) and stores them in SQLite.
FastAPI, Python 3.11+.

## Run

```bash
cd server
pip install -r requirements.txt
export RTD_API_TOKEN=<long random string>    # must equal DEVICE_TOKEN in the firmware's secrets.h
uvicorn app.asgi:app --host 0.0.0.0 --port 8000
```

The server refuses to start without `RTD_API_TOKEN`. `RTD_DB_PATH` sets the database file
(default `rtd.db`). Or with Docker: `docker build -t rtd-server server && docker run -e RTD_API_TOKEN=... -v rtd-data:/data -p 8000:8000 rtd-server`.

**TLS:** the server speaks plain HTTP. For anything on the internet, put a reverse proxy (Caddy, nginx)
in front for https and set `SERVER_URL` / `SERVER_CA_CERT` in the firmware to match.

## Endpoints

All except `/health` need `Authorization: Bearer <RTD_API_TOKEN>`; otherwise 401.

| Method and path | Purpose |
| --- | --- |
| `POST /ingest` | Firmware upload, payload as in spec section 04 |
| `GET /api/devices` | Known devices with firmware version and last-seen time |
| `GET /api/devices/{id}/readings?since=<ts>&limit=<n>` | Newest-first rows with their 8 channels (limit up to 20000, default 100) |
| `GET /` | Dashboard page (no data in it; it asks for the token) |
| `GET /health` | Liveness check, no auth |

### `POST /ingest` behaviour

The reply is `{"accepted": n, "duplicates": n, "rejected": n}` with status 200 when the payload was
well formed. Rows are de-duplicated by `device_id` + `ts`, so a resend after a lost response is harmless.

| Status | Meaning | Firmware reaction |
| --- | --- | --- |
| 200 | Processed; bad individual rows are counted in `rejected` and skipped | discards rows |
| 413 | Body over 64 KB | discards rows |
| 422 | Body is not JSON or the envelope is invalid (`device_id`, `fw_version`, `readings`) | discards rows |
| 401 | Wrong or missing token | keeps rows, retries |

A row is rejected if `ts` is not `YYYY-MM-DDTHH:MM:SSZ` or is more than 5 minutes in the future, if it
does not have channels 1 to 8 in order, if `type` is not `PT100`/`PT1000`, or if `temp_c` is outside
-200 to 850 C. `temp_c: null` (a MAX31865 fault) is stored as NULL.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Not included

No data retention or cleanup, no per-device tokens (one shared token), and no rate limiting.
