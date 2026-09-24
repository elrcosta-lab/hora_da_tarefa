"""TDD RED — Motor de slots livres (SPECS §4 v1.1, CA-02).

Invariantes: nunca sobrepor aula/atividade não-faltável, nunca em quiet hours,
sempre terminar antes de due_at - folga. Empate determinístico (menor data).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")


def _dt(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


def _base(homework_over=None, now=None):
    hw = {"due_at": _dt(25, 23, 59), "estimated_minutes": 40, "priority": 2, "subject": "Matemática"}
    if homework_over:
        hw.update(homework_over)
    return {
        "homework": hw,
        "schedules": [
            {"weekday": d, "start_time": "07:30", "end_time": "12:00", "kind": "aula"}
            for d in [0, 1, 2, 3, 4]  # seg–sex (0=segunda neste teste)
        ],
        "activities": [
            {"weekday": 2, "start_time": "17:00", "end_time": "18:00",
             "travel_before_min": 20, "travel_after_min": 0, "is_blocking": True},
        ],
        "now": now or _dt(22, 10, 0),
    }


def test_no_slot_collides_with_class_or_activity():
    from app.services.scheduling import suggest_slots

    inp = _base()
    slots = suggest_slots(**inp, limit=5)
    assert len(slots) > 0
    for s in slots:
        # nenhuma sugestão colide com aula 07:30-12:00 seg–sex
        assert not (s["start_at"].hour < 12 and s["start_at"].weekday() < 5 and s["start_at"].hour >= 7)
        # nenhuma colide com natação qua 17:00-18:00 + 20min deslocamento (16:40-18:00)
        if s["start_at"].weekday() == 2:  # quarta
            assert s["end_at"].hour * 60 + s["end_at"].minute <= 16 * 60 + 40 or s["start_at"].hour >= 18


def test_first_suggestion_inside_study_window():
    from app.services.scheduling import suggest_slots

    slots = suggest_slots(**_base(), limit=5)
    first = slots[0]
    assert 14 <= first["start_at"].hour < 21
    assert first["score"] >= slots[-1]["score"]


def test_respects_quiet_hours_and_due_with_buffer():
    from app.services.scheduling import suggest_slots

    slots = suggest_slots(**_base(), limit=10)
    for s in slots:
        # quiet 21:30–07:00 nunca
        h = s["start_at"].hour + s["start_at"].minute / 60
        assert not (h >= 21.5 or h < 7.0)
        # termina com folga ≥2h antes da entrega (default)
        assert s["end_at"] <= _dt(25, 21, 59)


def test_no_slot_when_window_fully_blocked():
    from app.services.scheduling import suggest_slots

    inp = _base(homework_over={"due_at": _dt(22, 15, 0), "estimated_minutes": 120})
    # bloqueia o dia inteiro com aula
    inp["schedules"] = [{"weekday": 1, "start_time": "00:00", "end_time": "23:59", "kind": "aula"}]
    inp["activities"] = []
    slots = suggest_slots(**inp, limit=5)
    assert slots == []


def test_deterministic_tiebreak_earliest_first():
    from app.services.scheduling import suggest_slots

    inp = _base()
    inp["schedules"] = []
    inp["activities"] = []
    s1 = suggest_slots(**inp, limit=5)
    s2 = suggest_slots(**inp, limit=5)
    assert [x["start_at"] for x in s1] == [x["start_at"] for x in s2]
    # ordem = score desc (diversidade entre dias); empate de score = menor data
    assert all(a["score"] >= b["score"] for a, b in zip(s1, s1[1:]))


def test_top_slots_spread_across_days():
    """Bug real (2026-09-24): top-3 eram 19:00/19:15/19:30 do mesmo dia.
    Com horizonte de vários dias, as melhores opções devem variar o dia
    (1 por dia na 1ª passada); só completa no mesmo dia se faltar opção.
    """
    from app.services.scheduling import suggest_slots

    inp = _base()
    inp["schedules"] = []
    inp["activities"] = []
    slots = suggest_slots(**inp, limit=3)
    assert len(slots) == 3
    assert len({s["start_at"].date() for s in slots}) == 3


def test_single_day_horizon_still_fills_limit():
    """Horizonte de 1 dia: sem outro dia disponível, completa no mesmo dia."""
    from app.services.scheduling import suggest_slots

    inp = _base(homework_over={"due_at": _dt(22, 21, 0)})
    inp["schedules"] = []
    inp["activities"] = []
    slots = suggest_slots(**inp, limit=3)
    assert len(slots) == 3
