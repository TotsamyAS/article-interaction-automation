import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.contracts import AttemptInput, Event, Query, SessionCreate
from app.database import Database
from app.errors import DomainError
from app.service import ExperimentService


def first_trial(service, code="P001", kind="experiment"):
    session = service.create_session(SessionCreate(participant_code=code, kind=kind))
    trial = service.start_trial(session["trials"][0]["id"])
    return session, trial


def submit(service, trial, query=None, request_id=None):
    return service.submit(trial["id"], AttemptInput(request_id=request_id or uuid4(), query=query if query is not None else Query()))


def test_schedule_balanced_without_variant_repeats(service):
    schedules = [service.create_session(SessionCreate(participant_code=f"P{i}"))["trials"] for i in range(5)]
    for level in (1, 2, 3):
        pairs = [(t["mode"], t["task_id"][-1]) for rows in schedules for t in rows if t["level"] == level]
        assert len(pairs) == len(set(pairs)) == 25
    for rows in schedules:
        assert len(rows) == 15
        assert [t["level"] for t in rows] == [1, 2, 3] * 5
        assert len({t["task_id"] for t in rows}) == 15


def test_attempt_limit_keeps_facts_and_penalty_separate(service, clock):
    _, trial = first_trial(service)
    for _ in range(5):
        clock.advance(8000)
        result = submit(service, trial)
        assert not result["correct"]
    view = service.trial(trial["id"])
    assert view["end_reason"] == "attempt_limit"
    assert view["metrics"]["actual"]["elapsed_ms"] == 40000
    assert view["metrics"]["actual"]["attempts"] == 5
    assert view["metrics"]["actual"]["Nretry"] == 4
    assert view["metrics"]["actual"]["Tcorrect_ms"] is None
    assert view["metrics"]["analysis"] == {"Tcorrect_ms": 300000, "A1": 0, "Nretry": 4}


def test_timeout_after_one_attempt_has_no_actual_retries(service, clock):
    _, trial = first_trial(service)
    submit(service, trial)
    clock.advance(300000)
    view = service.trial(trial["id"])
    assert view["end_reason"] == "time_limit"
    assert view["metrics"]["actual"]["Nretry"] == 0
    assert view["metrics"]["analysis"]["Nretry"] == 4
    with pytest.raises(DomainError):
        submit(service, trial, service.catalog[trial["task_id"]].query)


def test_success_on_fifth_attempt_is_success(service, clock):
    _, trial = first_trial(service)
    for _ in range(4):
        submit(service, trial)
    clock.advance(5000)
    assert submit(service, trial, service.catalog[trial["task_id"]].query)["correct"]
    view = service.trial(trial["id"])
    assert view["metrics"]["actual"]["Tcorrect_ms"] == 5000
    assert not view["metrics"]["incomplete"]
    assert view["metrics"]["analysis"]["Tcorrect_ms"] == 5000


def test_idempotent_concurrent_submission_and_conflict(service):
    _, trial = first_trial(service)
    request = AttemptInput(request_id=uuid4(), query=Query())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.submit(trial["id"], request), range(4)))
    assert all(result == results[0] for result in results)
    assert len(service.trial(trial["id"])["attempts"]) == 1
    request.query = service.catalog[trial["task_id"]].query
    with pytest.raises(DomainError, match="request_id"):
        service.submit(trial["id"], request)


def test_idempotency_survives_restart(service, settings, clock):
    session, trial = first_trial(service)
    request = AttemptInput(request_id=uuid4(), query=service.catalog[trial["task_id"]].query)
    response = service.submit(trial["id"], request)
    database = Database(settings)
    database.initialize()
    restarted = ExperimentService(database, clock)
    assert restarted.submit(trial["id"], request) == response
    assert restarted.create_session(SessionCreate(participant_code="P001"))["id"] == session["id"]


def test_order_without_mandatory_break_and_required_csv(service, clock):
    session = service.create_session(SessionCreate(participant_code="P001"))
    with pytest.raises(DomainError, match="предыдущую"):
        service.start_trial(session["trials"][1]["id"])
    for trial in session["trials"][:3]:
        service.start_trial(trial["id"])
        query = service.catalog[trial["task_id"]].query.model_copy(deep=True)
        if trial["level"] == 3:
            query.output.format = "table"
            assert not submit(service, trial, query)["correct"]
            query.output.format = "csv"
        response = submit(service, trial, query)
        assert response["correct"]
        if trial["level"] == 3:
            assert service.export_attempt(response["id"]).startswith("\ufeffid,")
    assert service.start_trial(session["trials"][3]["id"])["status"] == "active"


@pytest.mark.parametrize(("sequence", "offset", "code", "message"), [
    (0, 1000, "event_sequence", "другой вкладке"),
    (1, 500, "event_time_order", "в неправильном порядке"),
    (1, 3000, "event_outside_trial", "за пределами времени"),
])
def test_event_errors_explain_the_specific_problem(service, clock, sequence, offset, code, message):
    _, trial = first_trial(service)
    clock.advance(2000)
    service.ingest_events(trial["id"], [Event(event_id=uuid4(), sequence=0, offset_ms=1000, kind="input")])
    with pytest.raises(DomainError) as error:
        service.ingest_events(trial["id"], [Event(event_id=uuid4(), sequence=sequence, offset_ms=offset, kind="input")])
    assert error.value.code == code
    assert message in error.value.message
    assert service.trial(trial["id"])["next_event_sequence"] == 1


def test_unavailable_mode_not_silently_replaced(service):
    for i in range(3):
        session = service.create_session(SessionCreate(participant_code=f"P{i}"))
    assert session["trials"][0]["mode"] == "M3"
    with pytest.raises(DomainError, match="подключения"):
        service.start_trial(session["trials"][0]["id"])


def test_event_retries_do_not_duplicate_active_time(service, clock):
    _, trial = first_trial(service)
    clock.advance(5000)
    events = [Event(event_id=uuid4(), sequence=i, offset_ms=i * 1000, kind="input") for i in range(4)]
    assert service.ingest_events(trial["id"], events)["accepted"] == 4
    assert service.ingest_events(trial["id"], events)["duplicates"] == 4
    view = service.trial(trial["id"])
    assert view["metrics"]["actual"]["Tuser_active_ms"] == 3000
    assert view["next_event_sequence"] == 4
    assert view["last_event_offset_ms"] == 3000
    assert view["elapsed_since_start_ms"] == 5000
    with pytest.raises(DomainError):
        service.ingest_events(trial["id"], [Event(event_id=uuid4(), sequence=5, offset_ms=6000, kind="input")])


def test_pre_m3_volume_remains_compatible(service, settings):
    database = Database(settings)
    connection = database.connect()
    try:
        row = connection.execute("SELECT manifest FROM dataset WHERE singleton = 1").fetchone()
        manifest = json.loads(row["manifest"])
        for key in ("llm_base_url", "llm_model", "llm_temperature", "llm_timeout_seconds"):
            manifest["protocol"].pop(key, None)
        manifest.pop("m3_prompt_sha256", None)
        connection.execute(
            "UPDATE dataset SET manifest = ? WHERE singleton = 1",
            (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
        )
    finally:
        connection.close()

    database.initialize()


def test_changed_protocol_cannot_rewrite_existing_experiment(service, settings):
    with pytest.raises(RuntimeError, match="протоколом"):
        Database(settings.model_copy(update={"seed": settings.seed + 1})).initialize()


def test_break_migration_preserves_progress_and_records_previous_policy(service, settings, clock):
    session, trial = first_trial(service)
    attempt = submit(service, trial, service.catalog[trial["task_id"]].query)
    # Recreate a pre-patch database only in the disposable test fixture.
    with service.database.transaction() as connection:
        connection.execute("DROP TABLE protocol_changes")
        connection.execute("DELETE FROM schema_migrations WHERE version = 5")
        connection.execute("UPDATE dataset SET manifest = json_set(manifest, '$.protocol.break_seconds', 120)")
    database = Database(settings)
    database.initialize()
    database.initialize()
    restarted = ExperimentService(database, clock)
    restored = restarted.session(session["id"])
    assert restored["trials"][0]["status"] == "correct"
    assert restored["trials"][0]["attempts"][0]["id"] == attempt["id"]
    assert restored["manifest"]["protocol"]["break_seconds"] == 0
    changes = restored["manifest"]["protocol_changes"]
    assert len(changes) == 1
    assert changes[0]["previous_value"] == "120"
    assert changes[0]["new_value"] == "0"
    assert changes[0]["changed_at_ms"] > 0
