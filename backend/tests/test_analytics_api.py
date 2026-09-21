import csv
import io
import json
import zipfile
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.analytics import excel_cell
from app.errors import DomainError


def create_started(client, code="P001", kind="experiment"):
    response = client.post("/api/sessions", json={"participant_code": code, "kind": kind})
    assert response.status_code == 200
    session = response.json()
    trial = session["trials"][0]
    assert client.post(f'/api/trials/{trial["id"]}/start').status_code == 200
    return session, trial


def test_api_opens_and_exposes_typed_contracts(client):
    assert client.get("/health").json() == {"status": "ok"}
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert "Query" in schema.json()["components"]["schemas"]
    assert len(client.get("/api/records").json()) == 120
    assert len(client.get("/api/tasks").json()) == 16


def test_analytics_exports_preserve_actual_and_penalty_and_exclude_training(client, clock):
    _, trial = create_started(client)
    response = client.post(f'/api/trials/{trial["id"]}/attempts', json={"request_id": str(uuid4()), "query": {}})
    assert response.status_code == 200
    clock.advance(300000)
    create_started(client, "TRAINING", "practice")
    snapshot = client.get("/api/analytics?completed_only=true").json()
    assert len(snapshot["trials"]) == 1
    row = snapshot["trials"][0]
    assert row["Nretry_actual"] == 0 and row["Nretry_analysis"] == 4
    assert row["Tcorrect_actual_ms"] is None and row["Tcorrect_analysis_ms"] == 300000
    assert snapshot["summary"][0]["Tcorrect_actual_mean_ms"] is None
    assert snapshot["summary"][0]["Tcorrect_analysis_mean_ms"] == 300000
    assert snapshot["protocol"]["training_excluded"]

    raw = client.get("/api/analytics/trials.csv?completed_only=true")
    assert raw.status_code == 200
    rows = list(csv.DictReader(io.StringIO(raw.content.decode("utf-8-sig"))))
    assert rows[0]["Nretry_actual"] == "0" and rows[0]["Nretry_analysis"] == "4"

    archive_response = client.get("/api/analytics/export.zip?completed_only=true")
    assert archive_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
        assert set(archive.namelist()) == {"trials.csv", "attempts.csv", "events.csv", "summary.csv", "protocol.json"}
        assert json.loads(archive.read("protocol.json"))["manifest"]["dataset_sha256"]

    workbook_response = client.get("/api/analytics/export.xlsx?completed_only=true")
    assert workbook_response.status_code == 200
    workbook = load_workbook(io.BytesIO(workbook_response.content), read_only=True)
    assert set(workbook.sheetnames) == {"protocol", "trials", "attempts", "events", "summary"}
    values = list(workbook["trials"].values)
    excel_row = dict(zip(values[0], values[1]))
    assert excel_row["Nretry_actual"] == 0
    assert excel_row["Nretry_analysis"] == 4
    assert excel_row["Tcorrect_actual_ms"] is None
    workbook.close()


def test_analytics_filters_and_empty_exports(client):
    create_started(client)
    create_started(client, "T001", "practice")
    response = client.get("/api/analytics?participant_code=P001&mode=M1&level=1")
    assert response.status_code == 200, response.json()
    snapshot = response.json()
    assert len(snapshot["trials"]) == 1
    assert snapshot["trials"][0]["participant_code"] == "P001"
    assert snapshot["summary"][0]["completed"] == 0
    assert snapshot["summary"][0]["success_rate"] is None
    all_rows = client.get("/api/analytics?include_practice=true").json()["trials"]
    assert len(all_rows) == 20
    empty = client.get("/api/analytics/trials.csv?participant_code=ABSENT")
    assert empty.status_code == 200
    assert len(list(csv.reader(io.StringIO(empty.content.decode("utf-8-sig"))))) == 1


def test_successful_api_flow_idempotency_and_session_export(client, clock):
    session, trial = create_started(client)
    query = {"filters": [{"field": "status", "operator": "eq", "value": "В работе"},
                          {"field": "priority", "operator": "eq", "value": "Высокий"}]}
    preview = client.post(f'/api/trials/{trial["id"]}/preview', json=query)
    assert preview.status_code == 200
    clock.advance(1000)
    payload = {"request_id": str(uuid4()), "query": query}
    first = client.post(f'/api/trials/{trial["id"]}/attempts', json=payload)
    repeated = client.post(f'/api/trials/{trial["id"]}/attempts', json=payload)
    assert first.status_code == 200 and first.json()["correct"]
    assert first.json() == repeated.json()
    saved = client.get(f'/api/sessions/{session["id"]}/export')
    assert len(saved.json()["attempt_log"]) == 1
    assert saved.json()["trials"][0]["metrics"]["actual"]["Tcorrect_ms"] == 1000


def test_validation_does_not_echo_untrusted_request(client):
    response = client.post("/api/sessions", json={"participant_code": "<invalid-value>"})
    assert response.status_code == 422
    assert "<invalid-value>" not in response.text


@pytest.mark.parametrize("level", ["0", "4", "invalid"])
def test_analytics_rejects_invalid_level(client, level):
    assert client.get(f"/api/analytics?level={level}").status_code == 422


def test_excel_never_silently_truncates_or_executes_text():
    assert excel_cell("=1+1") == "'=1+1"
    assert excel_cell("x\x00y") == "x\\u0000y"
    with pytest.raises(DomainError, match="лимит"):
        excel_cell("x" * 32768)
