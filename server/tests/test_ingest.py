import copy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def ts(offset_s: int = 0) -> str:
    t = datetime.now(timezone.utc) - timedelta(seconds=offset_s)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def row(t: str, temp: float | None = 24.31) -> dict:
    return {
        "ts": t,
        "channels": [
            {"ch": i, "type": "PT1000" if i == 8 else "PT100", "temp_c": temp} for i in range(1, 9)
        ],
    }


def body(*rows, device="rtd-logger-01") -> dict:
    return {"device_id": device, "fw_version": "0.1.0", "readings": list(rows)}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(token=TOKEN, db_path=str(tmp_path / "t.db")))


def test_accepts_live_plus_backlog_row(client):
    r = client.post("/ingest", json=body(row(ts(3600)), row(ts())), headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"accepted": 2, "duplicates": 0, "rejected": 0}


def test_resent_rows_are_duplicates(client):
    payload = body(row(ts(20)), row(ts(10)))
    client.post("/ingest", json=payload, headers=AUTH)
    r = client.post("/ingest", json=payload, headers=AUTH)
    assert r.json() == {"accepted": 0, "duplicates": 2, "rejected": 0}


def test_same_ts_on_different_devices_is_not_a_duplicate(client):
    t = ts()
    client.post("/ingest", json=body(row(t), device="a"), headers=AUTH)
    r = client.post("/ingest", json=body(row(t), device="b"), headers=AUTH)
    assert r.json()["accepted"] == 1


def test_null_temp_is_stored_as_fault(client):
    t = ts()
    client.post("/ingest", json=body(row(t, None)), headers=AUTH)
    got = client.get("/api/devices/rtd-logger-01/readings", headers=AUTH).json()
    assert got[0]["ts"] == t
    assert all(c["temp_c"] is None for c in got[0]["channels"])


def test_bad_row_is_rejected_but_good_row_kept(client):
    bad = row(ts(20))
    bad["channels"][0]["temp_c"] = 5000
    r = client.post("/ingest", json=body(bad, row(ts())), headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"accepted": 1, "duplicates": 0, "rejected": 1}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["readings"][0]["channels"].pop(),            # only 7 channels
        lambda b: b["readings"][0]["channels"].reverse(),         # out of order
        lambda b: b["readings"][0]["channels"][0].update(type="PT500"),
        lambda b: b["readings"][0].update(ts="2026-09-09 14:32:10"),
        lambda b: b["readings"][0].update(ts=(datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    ],
)
def test_malformed_rows_are_rejected(client, mutate):
    b = body(row(ts()))
    mutate(b)
    r = client.post("/ingest", json=b, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["rejected"] == 1 and r.json()["accepted"] == 0


@pytest.mark.parametrize(
    "payload",
    [{}, {"device_id": "x"}, {"device_id": "bad id!", "fw_version": "1", "readings": [{}]},
     {"device_id": "x", "fw_version": "1", "readings": []}],
)
def test_bad_envelope_is_422(client, payload):
    assert client.post("/ingest", json=payload, headers=AUTH).status_code == 422


def test_invalid_json_is_422(client):
    r = client.post("/ingest", content=b"{nope", headers={**AUTH, "Content-Type": "application/json"})
    assert r.status_code == 422


def test_oversized_body_is_413(client):
    r = client.post("/ingest", content=b" " * (64 * 1024 + 1), headers={**AUTH, "Content-Type": "application/json"})
    assert r.status_code == 413


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
def test_bad_auth_is_401_and_stores_nothing(client, headers):
    r = client.post("/ingest", json=body(row(ts())), headers=headers)
    assert r.status_code == 401
    assert client.get("/api/devices", headers=AUTH).json() == []


def test_read_endpoints_need_auth(client):
    assert client.get("/api/devices").status_code == 401
    assert client.get("/api/devices/x/readings").status_code == 401


def test_devices_and_readings_listing(client):
    client.post("/ingest", json=body(row(ts(20)), row(ts(10)), row(ts())), headers=AUTH)
    devs = client.get("/api/devices", headers=AUTH).json()
    assert devs[0]["device_id"] == "rtd-logger-01" and devs[0]["fw_version"] == "0.1.0"
    got = client.get("/api/devices/rtd-logger-01/readings?limit=2", headers=AUTH).json()
    assert len(got) == 2 and got[0]["ts"] > got[1]["ts"]
    assert [c["ch"] for c in got[0]["channels"]] == list(range(1, 9))


def test_health_needs_no_auth(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_refuses_to_start_without_token(monkeypatch, tmp_path):
    monkeypatch.delenv("RTD_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        create_app(db_path=str(tmp_path / "t.db"))


def test_dashboard_page_is_served_without_auth(client):
    r = client.get("/")
    assert r.status_code == 200 and "RTD Logger" in r.text


def test_since_filter_and_many_rows(client):
    rows = [row(ts(i * 10)) for i in range(60, -1, -1)]  # 61 rows, oldest first
    client.post("/ingest", json=body(*rows[:30]), headers=AUTH)
    client.post("/ingest", json=body(*rows[30:]), headers=AUTH)
    got = client.get("/api/devices/rtd-logger-01/readings?limit=20000", headers=AUTH).json()
    assert len(got) == 61 and all(len(g["channels"]) == 8 for g in got)
    cut = rows[40]["ts"]
    newer = client.get(f"/api/devices/rtd-logger-01/readings?since={cut}", headers=AUTH).json()
    assert {g["ts"] for g in newer} == {r["ts"] for r in rows[40:]}
