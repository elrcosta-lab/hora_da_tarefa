"""Motor de slots livres (SPECS §4 v1.1) — funções puras, sem rede/DB.

Invariantes (nunca violados):
- nunca sobrepor aula ou atividade is_blocking (com deslocamento)
- nunca em quiet hours (21:30–07:00 default)
- sempre terminar ≥ folga (2h default) antes de due_at
"""
import math
from datetime import datetime, time, timedelta


GRID_MINUTES = 15
BUFFER_MINUTES = 10
MAX_SLOTS_PER_DAY = 3
FOLGA_MINUTES = 120
MIN_LEAD_MINUTES = 0


def _parse_hm(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(h, m)


def _ceil_duration(estimated_minutes: int) -> int:
    return math.ceil((estimated_minutes or 30) / GRID_MINUTES) * GRID_MINUTES + BUFFER_MINUTES


def _study_window(day, preferences=None):
    prefs = preferences or {}
    sw = prefs.get("study_window", {})
    is_weekend = day.weekday() >= 5
    if is_weekend:
        w = sw.get("weekend", {"start": "09:00", "end": "20:00"})
    else:
        w = sw.get("weekday", {"start": "14:00", "end": "21:00"})
    return _parse_hm(w["start"]), _parse_hm(w["end"])


def _quiet() -> tuple[time, time]:
    return time(21, 30), time(7, 0)


def _to_interval(day, start_s: str, end_s: str, tz, delta_before=0, delta_after=0):
    s = _parse_hm(start_s)
    e = _parse_hm(end_s)
    start = datetime(day.year, day.month, day.day, s.hour, s.minute, tzinfo=tz)
    end = datetime(day.year, day.month, day.day, e.hour, e.minute, tzinfo=tz)
    return (start - timedelta(minutes=delta_before), end + timedelta(minutes=delta_after))


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _merge(intervals):
    if not intervals:
        return []
    intervals = sorted(intervals)
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        ls, le = merged[-1]
        if s <= le:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    return merged


def _focus_factor(start: datetime) -> float:
    h = start.hour + start.minute / 60
    if 14 <= h < 18:
        return 1.0
    if 18 <= h < 20:
        return 0.6
    if 9 <= h < 14:
        return 0.4
    return 0.2


def _in_parent_window(slot_start, slot_end, availability) -> bool:
    """Slot contido numa janela do responsável (mesmo weekday)."""
    for w in availability or []:
        try:
            if int(w.get("weekday", -1)) != slot_start.weekday():
                continue
            ws, we = _parse_hm(w["start_time"]), _parse_hm(w["end_time"])
            a = datetime(slot_start.year, slot_start.month, slot_start.day,
                         ws.hour, ws.minute, tzinfo=slot_start.tzinfo)
            b = datetime(slot_start.year, slot_start.month, slot_start.day,
                         we.hour, we.minute, tzinfo=slot_start.tzinfo)
            if a <= slot_start and slot_end <= b:
                return True
        except Exception:
            continue
    return False


def _score(slot_start, slot_end, duration, homework, now, due, busy, availability=None) -> float:
    total_sec = max(1.0, (due - now).total_seconds())
    remain_sec = max(0.0, (due - slot_start).total_seconds())
    s = 30.0 * max(0.0, min(1.0, remain_sec / total_sec))
    # earlier is better: invert — quanto mais cedo após now, maior
    elapsed_sec = max(0.0, (slot_start - now).total_seconds())
    s += 0.0  # mantido p/ clareza; o termo acima já premia antecedência relativa
    s = 30.0 * (1.0 - min(1.0, elapsed_sec / total_sec)) + 20.0 * _focus_factor(slot_start)
    # fragmentação: +10 se longe de bloqueios, senão +4
    gap_ok = True
    for bs, be in busy:
        gap = min(abs((slot_start - be).total_seconds()), abs((bs - slot_end).total_seconds())) / 60
        if gap < 30:
            gap_ok = False
            break
    s += 10.0 if gap_ok else 4.0
    s += 15.0 * (float(homework.get("priority", 1)) / 2.0)
    if _in_parent_window(slot_start, slot_end, availability):
        s += 12.0  # responsável disponível para acompanhar
    if slot_start.date() == now.date() and duration > 45:
        s -= 15.0
    return max(0.0, min(100.0, s))


def suggest_slots(homework, schedules=None, activities=None, preferences=None, now=None, limit=5,
                  availability=None):
    """Retorna até `limit` slots [{start_at, end_at, score, reason, rank}]."""
    schedules = schedules or []
    activities = activities or []
    prefs = preferences or {}
    if now is None:
        raise ValueError("now é obrigatório (aware)")
    tz = now.tzinfo
    due = homework.get("due_at") or (now + timedelta(days=7))
    if due.tzinfo is None:
        due = due.replace(tzinfo=tz)
    duration = _ceil_duration(int(homework.get("estimated_minutes") or 30))
    if duration < int(prefs.get("min_slot_minutes", 20)):
        duration = int(prefs.get("min_slot_minutes", 20))
    folga = int(prefs.get("folga_minutes", FOLGA_MINUTES))
    due_limite = due - timedelta(minutes=folga)
    if due_limite <= now:
        return []

    max_per_day = int(prefs.get("max_slots_per_day", MAX_SLOTS_PER_DAY))
    q_start, q_end = _quiet()

    # 1. busy no horizonte (máx 14 dias)
    busy = []
    day = now.date()
    last = min(due_limite.date(), (now + timedelta(days=14)).date())
    d = day
    while d <= last:
        wd = datetime(d.year, d.month, d.day, tzinfo=tz).weekday()
        for sc in schedules:
            if int(sc.get("weekday", -1)) == wd:
                busy.append(_to_interval(d, sc["start_time"], sc["end_time"], tz))
        for ac in activities:
            if not ac.get("is_blocking", True):
                continue
            if int(ac.get("weekday", -1)) == wd:
                busy.append(
                    _to_interval(
                        d, ac["start_time"], ac["end_time"], tz,
                        delta_before=int(ac.get("travel_before_min", 0)),
                        delta_after=int(ac.get("travel_after_min", 0)),
                    )
                )
        d += timedelta(days=1)
    busy = _merge(busy)

    # 2. candidatos
    candidates = []
    d = day
    while d <= last:
        w_start_t, w_end_t = _study_window(datetime(d.year, d.month, d.day, tzinfo=tz), prefs)
        win_start = datetime(d.year, d.month, d.day, w_start_t.hour, w_start_t.minute, tzinfo=tz)
        win_end = datetime(d.year, d.month, d.day, w_end_t.hour, w_end_t.minute, tzinfo=tz)
        # quiet corta o fim; início nunca antes de 07:00
        q_start_dt = datetime(d.year, d.month, d.day, q_start.hour, q_start.minute, tzinfo=tz)
        win_end = min(win_end, q_start_dt, due_limite)
        q_end_dt = datetime(d.year, d.month, d.day, q_end.hour, q_end.minute, tzinfo=tz)
        win_start = max(win_start, q_end_dt)
        if d == now.date():
            win_start = max(win_start, now + timedelta(minutes=MIN_LEAD_MINUTES))
        cur = win_start
        # alinha na grade
        minute = (cur.minute // GRID_MINUTES) * GRID_MINUTES
        cur = cur.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minute)
        if cur < win_start:
            cur += timedelta(minutes=GRID_MINUTES)
        while cur + timedelta(minutes=duration) <= win_end:
            s, e = cur, cur + timedelta(minutes=duration)
            if not any(_overlaps(s, e, bs, be) for bs, be in busy):
                score = _score(s, e, duration, homework, now, due, busy, availability)
                days_left = (due.date() - s.date()).days
                reason = f"Livre; entrega em {days_left}d; foco {'alto' if 14 <= s.hour < 18 else 'normal'}"
                if _in_parent_window(s, e, availability):
                    reason += "; responsável disponível"
                candidates.append({"start_at": s, "end_at": e, "score": round(score, 1), "reason": reason})
            cur += timedelta(minutes=GRID_MINUTES)
        d += timedelta(days=1)

    # 4. rank determinístico: maior score, depois menor data
    candidates.sort(key=lambda c: (-c["score"], c["start_at"]))
    per_day: dict = {}
    ranked = []
    for c in candidates:
        k = c["start_at"].date()
        per_day[k] = per_day.get(k, 0)
        if per_day[k] >= max_per_day:
            continue
        per_day[k] += 1
        ranked.append(c)
        if len(ranked) >= limit:
            break
    for i, c in enumerate(ranked, 1):
        c["rank"] = i
    return ranked
