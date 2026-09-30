from uuid import uuid4

from app.contracts import Event
from app.metrics import active_time_ms


def test_active_time_excludes_idle_blur_and_overlapping_requests():
    request_a, request_b = uuid4(), uuid4()
    raw = [("input", 0, None), ("request_started", 500, request_a), ("input", 1000, None),
           ("request_started", 1200, request_b), ("request_finished", 2000, request_a),
           ("request_finished", 2500, request_b), ("input", 3000, None),
           ("focus_lost", 3500, None), ("focus_gained", 4500, None), ("input", 5000, None),
           ("input", 11000, None), ("input", 12000, None)]
    events = [Event(event_id=uuid4(), sequence=i, kind=kind, offset_ms=offset, request_id=request)
              for i, (kind, offset, request) in enumerate(raw)]
    # [0,5000] minus request union [500,2500] and blur [3500,4500], plus [11000,12000].
    assert active_time_ms(events, 12000, 5000) == 3000


def test_active_time_counts_speech_interval_even_when_longer_than_idle_threshold():
    events = [
        Event(event_id=uuid4(), sequence=0, kind="speech_started", target="m4-speech", offset_ms=1000),
        Event(event_id=uuid4(), sequence=1, kind="speech_finished", target="m4-speech", offset_ms=9000),
    ]
    assert active_time_ms(events, 10000, 5000) == 8000
