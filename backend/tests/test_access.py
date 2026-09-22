from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.access import (AccessService, Principal, Signatures, key_from_runtime,
                        require_principal)
from app.api import create_app
from app.contracts import SessionCreate
from app.errors import DomainError


def test_missing_secret_fails_without_reading_environment():
    with pytest.raises(RuntimeError, match="INVITATION_SIGNING_KEY"):
        key_from_runtime(getenv=lambda _: None)


def test_public_endpoints_do_not_allow_access_to_experiment(settings, clock):
    app = create_app(settings, clock, signatures_factory=lambda: Mock(spec=Signatures), m3_interpreter_factory=lambda *_: None)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        for path in ("/api/me", "/api/records", "/api/protocol", "/api/tasks", "/api/analytics",
                     "/api/analytics/export.xlsx", "/api/analytics/export.zip", "/api/analytics/trials.csv",
                     "/api/sessions/unknown", "/api/trials/unknown", "/api/attempts/unknown/export"):
            assert client.get(path).status_code == 401, path
        assert client.post("/api/sessions", json={"participant_code": "OTHER"}).status_code == 401


def test_participant_isolation_covers_reads_writes_and_exports(client):
    other = client.post("/api/sessions", json={"participant_code": "OTHER"}).json()
    trial_id = other["trials"][0]["id"]
    client.post(f"/api/trials/{trial_id}/start")
    attempt = client.post(f"/api/trials/{trial_id}/attempts", json={"request_id": str(uuid4()), "query": {}}).json()
    client.app.dependency_overrides[require_principal] = lambda: Principal("participant", "SELF", "participant", 1)
    own = client.post("/api/sessions", json={"participant_code": "SELF"})
    assert own.status_code == 200
    assert client.post("/api/sessions", json={"participant_code": "OTHER"}).status_code == 403
    for path in (f'/api/sessions/{other["id"]}', f'/api/sessions/{other["id"]}/export',
                 f'/api/sessions/{other["id"]}/metrics.csv', f"/api/trials/{trial_id}",
                 f'/api/attempts/{attempt["id"]}/export'):
        assert client.get(path).status_code == 404, path
    for endpoint, payload in (("start", None), ("preview", {}),
                              ("attempts", {"request_id": str(uuid4()), "query": {}}),
                              ("events", {"events": [{"event_id": str(uuid4()), "sequence": 0, "offset_ms": 0, "kind": "input"}]})):
        assert client.post(f"/api/trials/{trial_id}/{endpoint}", json=payload).status_code == 404
    for path in ("/api/tasks", "/api/analytics", "/api/analytics/export.xlsx", "/api/analytics/trials.csv"):
        assert client.get(path).status_code == 403
    resumed = client.get("/api/me").json()
    assert [s["id"] for s in resumed["sessions"]] == [own.json()["id"]]


def test_cross_origin_mutations_blocked(client):
    response = client.post("/api/sessions", json={"participant_code": "P"}, headers={"Origin": "https://external.example"})
    assert response.status_code == 403


def test_repeated_identity_does_not_reset_progress_and_revocation_preserves_data(service):
    signatures = Mock(spec=Signatures)
    access = AccessService(service.database, signatures)
    principal = access.provision("P001", "participant")
    session = service.create_session(SessionCreate(participant_code="P001"))
    trial_id = session["trials"][0]["id"]
    service.start_trial(trial_id)
    assert access.provision("P001", "participant") == principal
    assert access._get(principal.id, principal.generation) == principal
    assert service.create_session(SessionCreate(participant_code="P001"))["trials"][0]["status"] == "active"
    access.revoke("P001")
    with pytest.raises(DomainError):
        access._get(principal.id, principal.generation)
    assert service.session(session["id"])["trials"][0]["status"] == "active"
    signatures.sign.assert_not_called()


def test_invitation_exchange_reuses_identity_without_minting_real_credentials(client):
    # Exchange plumbing only: no actual key, signature or usable token is generated.
    access = client.app.state.access
    principal = access.provision("P001", "participant")
    access.validate = Mock(return_value=principal)
    access.cookie = Mock(return_value="")
    for _ in range(2):
        response = client.get("/api/access/enter?invitation=", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == client.app.state.service.settings.login_redirect_path
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=lax" in response.headers["set-cookie"]
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["cache-control"] == "no-store"
    assert access.cookie.call_args.args[0] == principal


@pytest.mark.parametrize("value", ["", "неверно", "x" * 257, "session.invalid", "invite.invalid"])
def test_malformed_credentials_rejected_before_signature_check(service, value):
    signatures = Mock(spec=Signatures)
    with pytest.raises(DomainError):
        AccessService(service.database, signatures).validate(value, "invite")
    signatures.matches.assert_not_called()


def test_reusable_invitation_restores_progress_and_researcher_can_export(settings, clock):
    signatures = Mock(spec=Signatures)
    signatures.sign.return_value = "a" * 64
    signatures.matches.return_value = True
    app = create_app(settings, clock, signatures_factory=lambda: signatures, m3_interpreter_factory=lambda *_: None)
    with TestClient(app) as invited:
        participant = app.state.access.provision("P001", "participant")
        invitation = app.state.access.invitation(participant)

        first_entry = invited.get("/api/access/enter", params={"invitation": invitation}, follow_redirects=False)
        assert first_entry.status_code == 303
        session = invited.post("/api/sessions", json={"participant_code": "P001"}).json()
        trial_id = session["trials"][0]["id"]
        assert invited.post(f"/api/trials/{trial_id}/start").status_code == 200

        invited.cookies.clear()
        second_entry = invited.get("/api/access/enter", params={"invitation": invitation}, follow_redirects=False)
        assert second_entry.status_code == 303
        resumed = invited.get("/api/me").json()
        assert resumed["participant_code"] == "P001"
        assert resumed["sessions"][0]["trials"][0]["status"] == "active"

        invited.cookies.clear()
        researcher = app.state.access.provision("RESEARCHER", "researcher")
        researcher_invitation = app.state.access.invitation(researcher)
        assert invited.get("/api/access/enter", params={"invitation": researcher_invitation},
                           follow_redirects=False).status_code == 303
        export = invited.get("/api/analytics/export.xlsx")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
