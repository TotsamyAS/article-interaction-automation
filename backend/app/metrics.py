from .contracts import Event


def active_time_ms(events: list[Event], end_ms: int, idle_ms: int) -> int:
    events = sorted(events, key=lambda event: event.sequence)
    inputs = [e.offset_ms for e in events if e.kind == "input" and e.offset_ms <= end_ms]
    active = [(a, b) for a, b in zip(inputs, inputs[1:]) if 0 <= b - a <= idle_ms]
    blocked = []
    blur = None
    requests = {}
    for event in events:
        t = min(event.offset_ms, end_ms)
        if event.kind == "focus_lost" and blur is None:
            blur = t
        elif event.kind == "focus_gained" and blur is not None:
            blocked.append((blur, t))
            blur = None
        elif event.kind == "request_started":
            requests.setdefault(event.request_id, t)
        elif event.kind == "request_finished" and event.request_id in requests:
            blocked.append((requests.pop(event.request_id), t))
    if blur is not None:
        blocked.append((blur, end_ms))
    blocked.extend((start, end_ms) for start in requests.values())
    merged = []
    for start, end in sorted(blocked):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return sum(b - a - sum(max(0, min(b, end) - max(a, start)) for start, end in merged)
               for a, b in active)


def trial_metrics(trial: dict, attempts: list[dict], active_ms: int, limit_ms: int, attempt_limit: int) -> dict:
    start, end = trial["started_ms"], trial["ended_ms"]
    elapsed = max(0, end - start) if start is not None and end is not None else None
    first = attempts[0] if attempts else None
    correct = trial["status"] == "correct"
    incomplete = trial["status"] == "incomplete"
    actual = {
        "elapsed_ms": elapsed,
        "Tcorrect_ms": elapsed if correct else None,
        "Tfirst_ms": first["finished_ms"] - start if first else None,
        "Tuser_active_ms": active_ms,
        "A1": int(first["correct"]) if first else None,
        "attempts": len(attempts),
        "Nretry": max(0, len(attempts) - 1),
    }
    analysis = {
        "Tcorrect_ms": limit_ms if incomplete else actual["Tcorrect_ms"],
        "A1": 0 if incomplete else actual["A1"],
        "Nretry": attempt_limit - 1 if incomplete else actual["Nretry"],
    }
    return {"actual": actual, "analysis": analysis, "incomplete": incomplete,
            "end_reason": trial["end_reason"]}
