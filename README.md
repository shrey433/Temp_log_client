# Temp_log_client

8-channel RTD temperature logger: an ESP32-S3 reads 8 MAX31865 modules (7 PT100, 1 PT1000, 3-wire)
every 10 s and uploads the readings to a cloud server over HTTPS. Rows are queued in PSRAM while
Wi-Fi is down and caught up one row per cycle after it returns.

## Repository layout

| Path | What it is |
| --- | --- |
| `rtd-logger-spec.html` | Draft 3 project spec: architecture, firmware flow, BOM, upload payload, pin map, requirements |
| `firmware/` | PlatformIO/Arduino firmware for the ESP32-S3-WROOM-1-N8R2 (see `firmware/README.md`) |
| `server/` | FastAPI + SQLite server that receives the uploads, and the dashboard it serves at `/` (see `server/README.md`) |
| `infra/` | Terraform for an EC2 deployment in AWS Mumbai (see `infra/README.md`) |

The server also serves a dashboard (current values and a 8-channel chart) at `/`.

## Quick start

Server (set the same token on both sides):

```bash
cd server && pip install -r requirements.txt
RTD_API_TOKEN=<token> uvicorn app.asgi:app --host 0.0.0.0 --port 8000
```

Firmware:

```bash
cd firmware
cp include/secrets.example.h include/secrets.h   # fill in Wi-Fi, SERVER_URL, DEVICE_TOKEN
pio run -t upload
pio device monitor
```

`firmware/include/secrets.h` is git-ignored. Never commit it.

Host-side tests for the offline queue: `pio test -e native` (needs a C++ compiler).

## Upload API

`POST <SERVER_URL>` with `Authorization: Bearer <DEVICE_TOKEN>` and a JSON body:

```json
{
  "device_id": "rtd-logger-01",
  "fw_version": "0.1.0",
  "readings": [
    {
      "ts": "2026-09-09T14:32:10Z",
      "channels": [
        { "ch": 1, "type": "PT100", "temp_c": 24.31 },
        { "ch": 8, "type": "PT1000", "temp_c": null }
      ]
    }
  ]
}
```

(Example abbreviated: real requests always carry all 8 channels.) `temp_c` is `null` when the
MAX31865 reports a fault. `readings` has 1 entry normally and 2 while a backlog row rides along.

The firmware discards the rows on 2xx, 400, 413 and 422, and keeps them for retry on 401, 5xx and
timeouts. The server should de-duplicate by device and timestamp.

## Before first power-up

- Set each MAX31865 module's solder jumpers to 3-wire.
- Make `R_REF_PT100` / `R_REF_PT1000` in `firmware/include/config.h` match the resistors actually fitted.
- Set `MAINS_HZ` (50 or 60) for your supply.
- Set `SERVER_CA_CERT` in `secrets.h` for https, otherwise the server certificate is not verified.

## Known limitations

- The offline queue lives in RAM, so a power loss empties it (about 116 hours of backlog capacity).
- Without a DS3231 and with no Wi-Fi at boot, sampling waits for a valid clock.
- OTA is not implemented; the partition table already reserves two app slots.
