"""Supervisão dos pais: busy/paridade/override/turno (motor + store + coerção)."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.scheduling import (
    _effective_availability,
    _parity_ok,
    detect_shift,
    suggest_slots,
)

TZ = ZoneInfo("America/Sao_Paulo")
TUE = datetime(2026, 9, 22, 10, 0, tzinfo=TZ)  # terça
DUE = datetime(2026, 9, 25, 23, 59, tzinfo=TZ)
PAR = date(2026, 9, 22).isocalendar()[1] % 2


def _hw(**over):
    hw = {"due_at": DUE, "estimated_minutes": 30, "priority": 1, "subject": "Matemática"}
    hw.update(over)
    return hw


def test_busy_penalizes_but_does_not_block():
    prefs = {"max_slots_per_day": 100}
    avail = [{"weekday": 1, "start_time": "14:00", "end_time": "21:00", "kind": "busy"}]
    free = {s["start_at"]: s for s in suggest_slots(_hw(), now=TUE, limit=200, preferences=prefs)}
    busy = {s["start_at"]: s for s in suggest_slots(_hw(), now=TUE, limit=200, preferences=prefs,
                                                    availability=avail)}
    tue = [k for k in free if k.weekday() == 1]
    assert tue, "sem slots de terça no pool"
    # mesmos slots existem nos dois mundos (penalidade, não bloqueio)...
    assert all(k in busy for k in tue)
    # ...mas pontuam menos e carregam o aviso
    assert all(busy[k]["score"] < free[k]["score"] for k in tue)
    assert all("trabalhando" in busy[k]["reason"] for k in tue)
    assert all(v["score"] >= 0 for v in busy.values())


def test_week_parity_gates_availability():
    prefs = {"max_slots_per_day": 30}
    match = suggest_slots(_hw(), now=TUE, limit=40, preferences=prefs, availability=[
        {"weekday": 1, "start_time": "19:00", "end_time": "20:00",
         "kind": "available", "week_parity": PAR}])
    mismatch = suggest_slots(_hw(), now=TUE, limit=40, preferences=prefs, availability=[
        {"weekday": 1, "start_time": "19:00", "end_time": "20:00",
         "kind": "available", "week_parity": 1 - PAR}])
    assert _parity_ok({"week_parity": PAR}, TUE.date())
    assert not _parity_ok({"week_parity": 1 - PAR}, TUE.date())
    evening = [s for s in match if s["start_at"].hour == 19]
    assert evening and "responsável disponível" in evening[0]["reason"]
    evening_off = [s for s in mismatch if s["start_at"].hour == 19]
    assert evening_off and "responsável disponível" not in evening_off[0]["reason"]


def test_dated_override_beats_weekly_rule():
    # regra semanal diz ocupado, mas hoje (exceção) está livre → vale a exceção
    avail = [{"weekday": 1, "start_time": "14:00", "end_time": "21:00", "kind": "busy"},
             {"weekday": 1, "start_time": "14:00", "end_time": "21:00",
              "kind": "available", "date": "2026-09-22"}]
    eff = _effective_availability(TUE.date(), avail)
    assert len(eff) == 1 and eff[0]["kind"] == "available"
    slots = suggest_slots(_hw(), now=TUE, limit=5, availability=avail)
    assert "trabalhando" not in slots[0]["reason"]


def test_shift_detection_and_morning_preference():
    vespertino = [{"weekday": d, "start_time": "13:00", "end_time": "17:20", "subject": "Aula"}
                  for d in range(5)]
    assert detect_shift(vespertino) == "vespertino"
    matutino = [{"weekday": d, "start_time": "07:30", "end_time": "12:00", "subject": "Aula"}
                for d in range(5)]
    assert detect_shift(matutino) == "matutino"
    assert detect_shift([]) is None
    slots = suggest_slots(_hw(), schedules=vespertino, now=TUE, limit=5)
    assert slots and slots[0]["start_at"].hour < 12  # manhã livre priorizada


def test_biweekly_activity_blocks_only_matching_week():
    act = {"title": "X", "weekday": 1, "start_time": "16:30", "end_time": "17:30",
           "recurrence": "biweekly", "travel_before_min": 0, "is_blocking": True}

    def tue_slots(parity):
        a = dict(act)
        a["week_parity"] = parity
        all_slots = suggest_slots(_hw(), activities=[a], now=TUE, limit=60,
                                  preferences={"max_slots_per_day": 30})
        return {(s["start_at"].hour, s["start_at"].minute)
                for s in all_slots if s["start_at"].date() == TUE.date()}

    assert (16, 30) in tue_slots(1 - PAR)  # semana alternada: livre
    assert (16, 30) not in tue_slots(PAR)  # semana da atividade: bloqueado


def test_store_validates_kinds():
    from app.tasks import routine as R

    child = R.create_child("Ana")
    cid = child["id"]
    try:
        R.save_availability(cid, [{"weekday": 0, "start_time": "18:00", "end_time": "20:00",
                                   "kind": "trabalhando"}])
        raise SystemExit("deveria rejeitar kind")
    except R.Validation:
        pass
    try:
        R.save_availability(cid, [{"weekday": 0, "start_time": "18:00", "end_time": "20:00",
                                   "week_parity": 2}])
        raise SystemExit("deveria rejeitar parity")
    except R.Validation:
        pass
    try:
        R.save_availability(cid, [{"weekday": 0, "start_time": "18:00", "end_time": "20:00",
                                   "date": "ontem"}])
        raise SystemExit("deveria rejeitar date")
    except R.Validation:
        pass
    out = R.save_availability(cid, [{"weekday": 0, "start_time": "18:00", "end_time": "20:00",
                                     "kind": "busy", "week_parity": PAR,
                                     "date": "2026-09-22"}], replace=True)
    assert out["created"] == 1
    got = R.list_availability(cid)[0]
    assert got["kind"] == "busy" and got["week_parity"] == PAR and got["date"] == "2026-09-22"
    R.clear_routine()


def test_coercion_normalizes_new_fields():
    import json
    from unittest.mock import MagicMock, patch

    from app.services.vision_openrouter import extract_routine

    payload = json.dumps({
        "schedules": [], "activities": [],
        "availability": [{"weekday": 1, "start_time": "18:00", "end_time": "20:00",
                          "kind": "TRABALHO", "week_parity": 5, "date": "amanhã"}],
        "confidence": 0.8, "needs_review": False, "warnings": [],
    })
    fake = MagicMock(choices=[MagicMock(message=MagicMock(content=payload))],
                     usage=MagicMock(prompt_tokens=1, completion_tokens=1))
    with patch("app.services.vision_openrouter._chat_json", return_value=fake):
        r = extract_routine(text="trabalho")
    assert len(r.availability) == 1
    w = r.availability[0]
    assert w.kind == "available" and w.week_parity is None and w.date is None
    assert len(r.warnings) == 3
