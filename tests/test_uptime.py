from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
import hub.db as db


@pytest.fixture
def uptime_database(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, 'engine', engine)
    with Session(engine) as session:
        session.add_all([db.Base(id=1, subd='up01'), db.Base(id=2, subd='up02')])
        session.commit()
    yield engine
    engine.dispose()


def add_pings(base_id, times):
    with Session(db.engine) as session:
        session.add_all(db.Telemetry(base_id=base_id, created_at=stamp) for stamp in times)
        session.commit()


def test_uptime_permille_counts_slots_not_pings(uptime_database):
    start = DateTime(2026, 11, 1, tzinfo=TimeZone.utc)
    end = start + TimeDelta(days=1)
    add_pings(1, [start + TimeDelta(minutes=10 * n) for n in range(140)])
    add_pings(1, [start, start + TimeDelta(minutes=1), end])
    add_pings(2, [start + TimeDelta(minutes=10 * n) for n in range(144)])
    assert db.uptime_permille(1, start, end) == 972
    assert db.uptime_permille(2, start, end) == 1000
    assert db.uptime_permille(3, start, end) == 0


def test_uptime_permille_uses_start_as_slot_origin(uptime_database):
    start = DateTime(2026, 11, 1, 8, 5, 0, 500000, tzinfo=TimeZone.utc)
    end = start + TimeDelta(minutes=25)
    add_pings(
        1,
        [
            start - TimeDelta(microseconds=1),
            start,
            start + TimeDelta(minutes=10) - TimeDelta(microseconds=1),
            start + TimeDelta(minutes=20),  # The unfinished third slot is excluded.
            end,
        ],
    )
    assert db.uptime_permille(1, start, end) == 500
    assert db.uptime_permille(1, start, start + TimeDelta(minutes=9)) == 0
    assert db.uptime_permille(1, end, start) == 0


def test_uptime_permille_normalizes_timezones(uptime_database):
    start = DateTime(2026, 11, 1, tzinfo=TimeZone.utc)
    add_pings(1, [start])
    local = start.astimezone(TimeZone(TimeDelta(hours=2)))
    assert db.uptime_permille(1, local, local + TimeDelta(minutes=20)) == 500
    naive = start.replace(tzinfo=None)
    assert db.uptime_permille(1, naive, naive + TimeDelta(minutes=20)) == 500


def test_daily_uptime_can_reuse_one_history_query(uptime_database):
    start = DateTime(2026, 11, 1, tzinfo=TimeZone.utc)
    middle = start + TimeDelta(days=1)
    end = middle + TimeDelta(days=1)
    add_pings(1, [start + TimeDelta(minutes=10 * n) for n in range(144)])
    add_pings(1, [middle + TimeDelta(minutes=10 * n) for n in range(72)])
    statements = []

    def record_statement(*args):
        statements.append(args[2])

    event.listen(uptime_database, 'before_cursor_execute', record_statement)
    try:
        slots = db.get_ping_slots(1, start, end)
        assert db.uptime_permille(1, start, middle, slots=slots) == 1000
        assert db.uptime_permille(1, middle, end, slots=slots) == 500
        assert len(statements) == 1
    finally:
        event.remove(uptime_database, 'before_cursor_execute', record_statement)


@pytest.mark.parametrize(
    'first, expected_begin',
    [
        ('2026-11-01T00:00:00', '2026-11-01T00:00:00'),
        ('2026-11-01T00:00:00.000001', '2026-11-01T00:10:00'),
        ('2026-11-01T23:59:59.999999', '2026-11-02T00:00:00'),
    ],
)
def test_ping_data_range_uses_first_ping_and_clock_boundaries(
    uptime_database, first, expected_begin
):
    now = DateTime(2026, 11, 2, 12, 37, 42, 123456, tzinfo=TimeZone.utc)
    first = DateTime.fromisoformat(first).replace(tzinfo=TimeZone.utc)
    expected_begin = DateTime.fromisoformat(expected_begin).replace(tzinfo=TimeZone.utc)
    # Insert out of order and include an older ping from another base.
    add_pings(1, [now - TimeDelta(minutes=15), first])
    add_pings(2, [first - TimeDelta(days=30)])
    assert db.get_ping_data_range(1, now) == (
        expected_begin,
        DateTime(2026, 11, 2, 12, 30, tzinfo=TimeZone.utc),
    )


def test_ping_data_range_without_telemetry_is_empty(uptime_database):
    now = DateTime(2026, 11, 2, 12, 37, tzinfo=TimeZone.utc)
    expected_end = now.replace(minute=30)
    add_pings(2, [now - TimeDelta(days=30)])
    for value in [now, now.replace(tzinfo=None), now.astimezone(TimeZone(TimeDelta(hours=2)))]:
        assert db.get_ping_data_range(1, value) == (expected_end, expected_end)


def test_first_ping_in_current_slot_has_no_complete_data_yet(uptime_database):
    now = DateTime(2026, 11, 2, 12, 37, tzinfo=TimeZone.utc)
    add_pings(1, [now - TimeDelta(minutes=3)])
    begin, end = db.get_ping_data_range(1, now)
    assert begin == now.replace(minute=40)
    assert end == now.replace(minute=30)
    assert db.get_ping_slots(1, begin, end) == []


def test_uptime_uses_only_observable_slots_of_partial_days(uptime_database):
    first = DateTime(2026, 11, 1, 23, 41, tzinfo=TimeZone.utc)
    now = DateTime(2026, 11, 3, 0, 25, tzinfo=TimeZone.utc)
    middle = DateTime(2026, 11, 2, tzinfo=TimeZone.utc)
    add_pings(1, [first, first.replace(minute=52)])
    add_pings(1, [middle + TimeDelta(minutes=10 * n) for n in range(72)])
    add_pings(1, [now.replace(minute=3), now.replace(minute=23)])
    data_range_begin, data_range_end = db.get_ping_data_range(1, now)
    slots = db.get_ping_slots(1, data_range_begin, data_range_end)
    for offset, expected_points, expected_uptime in [
        (-2, 0, None),
        (-1, 1, 1000),
        (0, 144, 500),
        (1, 2, 500),
        (2, 0, None),
    ]:
        day_start = middle + TimeDelta(days=offset)
        start = max(day_start, data_range_begin)
        end = min(day_start + TimeDelta(days=1), data_range_end)
        data_points = max(0, (end - start) // TimeDelta(minutes=10))
        assert data_points == expected_points
        if data_points:
            assert db.uptime_permille(1, start, end, slots=slots) == expected_uptime
