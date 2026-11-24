import asyncio
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
import os
from types import SimpleNamespace
import pytest
from nicegui import core, events, ui
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
import hub.db as db
import hub.heatmap as heatmap
import hub.migrate_db as migrate_db

day = DateTime(2026, 11, 1, tzinfo=TimeZone.utc)


@pytest.fixture
def database(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, 'engine', engine)
    with Session(engine) as session:
        session.add_all([db.Base(id=1, subd='heat1'), db.Base(id=2, subd='heat2')])
        session.commit()
    yield engine
    engine.dispose()


def add_pings(times):
    with Session(db.engine) as session:
        session.add_all(db.Telemetry(base_id=1, created_at=time) for time in times)
        session.commit()


def test_clipped_samples_share_history(database, monkeypatch):
    middle = day + TimeDelta(days=1)
    now = middle + TimeDelta(days=1, minutes=25)
    add_pings([middle - TimeDelta(minutes=19), middle - TimeDelta(minutes=8)])
    add_pings([middle + TimeDelta(minutes=10 * n) for n in range(72)])
    add_pings([now.replace(minute=3), now.replace(minute=23)])
    history = db.UptimeHistory(1, now)
    calls = []
    get_slots = db.get_ping_slots

    def load(*args):
        calls.append(args)
        return get_slots(*args)

    monkeypatch.setattr(db, 'get_ping_slots', load)
    assert history.sample(day - TimeDelta(days=1), day) is None
    assert history.sample(day, middle) == (1000, 1)
    assert history.sample(middle, middle + TimeDelta(days=1)) == (500, 144)
    assert history.sample(middle + TimeDelta(days=1), now) == (500, 2)
    assert history.sample(now, now + TimeDelta(days=1)) is None
    assert len(calls) == 1
    assert db.UptimeHistory(2, now).sample(day, now) is None


def test_partial_pairs_and_unaligned_intervals(database):
    now = day + TimeDelta(hours=12, minutes=35)
    add_pings(
        [day + TimeDelta(hours=h, minutes=m) for h, m in [(10, 3), (10, 13), (12, 4), (12, 20)]]
    )
    history = db.UptimeHistory(1, now)
    assert history.sample(day + TimeDelta(hours=10), day + TimeDelta(hours=10, minutes=20)) == (
        1000,
        1,
    )
    assert history.sample(day + TimeDelta(hours=12), day + TimeDelta(hours=12, minutes=20)) == (
        500,
        2,
    )
    assert history.sample(day + TimeDelta(hours=12, minutes=20), now) == (1000, 1)
    assert history.sample(day + TimeDelta(hours=11), day + TimeDelta(hours=12)) == (0, 6)
    start = day + TimeDelta(hours=12, minutes=5)
    assert history.sample(start, start + TimeDelta(minutes=20)) == (0, 1)
    assert history.sample(start, start + TimeDelta(minutes=4)) is None


def test_snapshot_expands_history_only_when_needed(database, monkeypatch):
    add_pings([day, day + TimeDelta(days=1)])
    history = db.UptimeHistory(1, day + TimeDelta(days=2))
    calls = []
    get_slots = db.get_ping_slots

    def load(*args):
        calls.append(args)
        return get_slots(*args)

    monkeypatch.setattr(db, 'get_ping_slots', load)
    assert history.sample(day + TimeDelta(days=1), day + TimeDelta(days=2)) == (6, 144)
    assert history.sample(day, day + TimeDelta(days=1)) == (6, 144)
    assert history.sample(day, day + TimeDelta(minutes=10)) == (1000, 1)
    assert len(calls) == 2


def grids(container):
    return [node for node in container.descendants() if isinstance(node, ui.grid)]


def cells(grid):
    return [node for node in grid.default_slot.children if 'title' in node.props]


def trigger(element, event_type, args=None):
    args = events.GenericEventArguments(sender=element, client=element.client, args=args)
    for listener in element._event_listeners.values():
        if listener.type.split('.')[0] == event_type:
            events.handle_event(listener.handler, args)


async def resize(container, width, rem=16):
    observer = next(node for node in container.descendants() if node.tag == 'q-resize-observer')
    handler = next(iter(observer._event_listeners.values())).handler
    await handler(SimpleNamespace(args={'width': width, 'rem': rem}))


def test_grid_supports_other_metrics_and_arbitrary_dimensions():
    intervals = []
    selected = []

    def sample(start, end):
        intervals.append((start, end))
        index = len(intervals) - 1
        return None if index == 1 else (index, 'Load: "busy" & available')

    colors = [(10, '#ff0000', "High"), (3, '#ffff00', "Medium"), (-1, '#00ff00', "Low")]
    with ui.column() as container:
        heatmap.render_grid(
            day,
            5,
            TimeDelta(seconds=75),
            ['A', 'B', 'C'],
            lambda _: '',
            sample,
            colors,
            selected.append,
        )
    try:
        grid = grids(container)[0]
        squares = cells(grid)
        assert len(intervals) == 15 and len(squares) == 14
        assert intervals[0] == (day, day + TimeDelta(seconds=75))
        assert intervals[-1][1] == day + TimeDelta(minutes=18, seconds=45)
        assert all(left[1] == right[0] for left, right in zip(intervals, intervals[1:]))
        assert squares[0].style['grid-column'] == '2'
        assert squares[-1].style['grid-column'] == '6'
        assert squares[-1].style['grid-row'] == '4'
        assert {square.style['background-color'] for square in squares} == {
            color for _, color, _ in colors
        }
        trigger(squares[0], 'focus')
        assert not selected  # Inspecting a calendar cell does not activate its day dialog.
        for square in (squares[0], squares[-1]):
            trigger(square, 'click')
        assert selected == [day, day + TimeDelta(minutes=17, seconds=30)]
        assert squares[0].props['title'].endswith('Load: "busy" & available')
        assert squares[0].props['aria-label'] == squares[0].props['title']
        assert not any(node.tag == 'q-tooltip' for node in container.descendants())
    finally:
        container.delete()


@pytest.mark.parametrize('event_type', ['click', 'focus'])
def test_cell_details_are_visible_on_touch_or_keyboard_focus(event_type):
    with ui.column() as container:
        for description in ['100% uptime', 'Load: "busy" & available']:
            heatmap.render_grid(
                day,
                1,
                TimeDelta(minutes=20),
                ['A', 'B', 'C'],
                lambda _: '',
                lambda start, _, text=description: None if start == day else (1000, text),
                heatmap.uptime_colors,
            )
    try:
        readouts = [node for node in container.descendants() if node.props.get('role') == 'status']
        assert len(readouts) == 2
        for grid, readout in zip(grids(container), readouts):
            squares = cells(grid)
            assert len(squares) == 2  # Blank slots stay blank and do not become controls.
            assert readout.props['aria-live'] == 'polite'
            assert readout.props['aria-atomic'] == 'true'
            assert list(container.descendants()).index(grid) < list(container.descendants()).index(
                readout
            )
            assert all(square.props['aria-pressed'] == 'false' for square in squares)
            for square in squares:
                assert square.tag == 'button' and square.props['type'] == 'button'
                assert square.props['aria-controls'] == readout.html_id
                assert square.props['aria-label'] == square.props['title']
                # A touch click must work without a preceding focus event (including Safari).
                trigger(square, event_type)
                assert readout.text == square.props['aria-label']
                assert square.props['aria-pressed'] == 'true'
                assert square.style.get('box-shadow')
                assert square.style['background-color'] == heatmap.uptime_colors[0][1]
                for other in squares:
                    if other is not square:
                        assert other.props['aria-pressed'] == 'false'
                        assert 'box-shadow' not in other.style
                trigger(square, 'blur')
                assert square.props['aria-pressed'] == 'true'
                assert readout.text == square.props['aria-label']
        assert readouts[0].text.endswith('100% uptime')
        assert readouts[1].text.endswith('Load: "busy" & available')
        assert all(cells(grid)[-1].props['aria-pressed'] == 'true' for grid in grids(container))
    finally:
        container.delete()


def test_uptime_color_boundaries():
    values = iter([1000, 990, 970, 950, 930])
    with ui.column() as container:
        heatmap.render_grid(
            day,
            1,
            TimeDelta(minutes=10),
            [''] * 5,
            lambda _: '',
            lambda *_: (next(values), ''),
            heatmap.uptime_colors,
        )
    try:
        assert [square.style['background-color'] for square in cells(grids(container)[0])] == [
            color for _, color, _ in heatmap.uptime_colors
        ]
    finally:
        container.delete()


def test_card_click_clears_details_and_reselection_stays_scoped_to_its_card():
    cards = list()
    with ui.column() as container:
        for _ in range(2):
            with ui.card() as card:
                details = heatmap.CellDetails(card)
                ui.label("Card heading")
                heatmap.render_grid(
                    day,
                    1,
                    TimeDelta(minutes=10),
                    ['A', 'B'],
                    lambda _: '',
                    lambda *_: (1000, '100% uptime'),
                    heatmap.uptime_colors,
                    show_legend=True,
                    details=details,
                )
                ui.button("Other action")
            cards.append(card)
    try:
        readouts = [
            next(node for node in card.descendants() if node.props.get('role') == 'status')
            for card in cards
        ]
        buttons = [cells(grids(card)[0]) for card in cards]
        for card, squares in zip(cards, buttons):
            listener = next(iter(card._event_listeners.values()))
            assert listener.to_dict()['specials'] == ['capture']
            trigger(squares[0], 'focus')
            trigger(card, 'click', squares[0].props['data-heatmap-cell'])
            assert squares[0].props['aria-pressed'] == 'true'
        # the card sees a click before its target; a different cell must still be selected
        trigger(cards[0], 'click', buttons[0][1].props['data-heatmap-cell'])
        trigger(buttons[0][1], 'click')
        assert readouts[0].text == buttons[0][1].props['aria-label']
        assert buttons[0][0].props['aria-pressed'] == 'false'
        assert buttons[0][1].props['aria-pressed'] == 'true'
        trigger(cards[0], 'click')
        assert readouts[0].text == ''
        for square in buttons[0]:
            assert square.props['aria-pressed'] == 'false'
            assert 'box-shadow' not in square.style and 'z-index' not in square.style
        assert buttons[1][0].props['aria-pressed'] == 'true'
        assert readouts[1].text == buttons[1][0].props['aria-label']
        trigger(cards[0], 'click')  # Repeated dismissal is harmless.
        trigger(buttons[0][0], 'click')
        trigger(cards[0], 'click', buttons[0][0].props['data-heatmap-cell'])
        assert buttons[0][0].props['aria-pressed'] == 'true'
        assert readouts[0].text == buttons[0][0].props['aria-label']
    finally:
        container.delete()


def test_calendar_and_day_layouts(monkeypatch):
    container = ui.card()

    async def check():
        monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
        with container:
            heatmap.render_calendar(
                "Load",
                lambda *_: (1, "One unit"),
                [(0, '#123456', "Present")],
                now=day + TimeDelta(days=22),
            )
        try:
            assert not grids(container)
            assert len(container._event_listeners) == 1
            await resize(container, 156.8)  # Exactly ten week columns at a 16px root font.
            grid = grids(container)[0]
            assert [
                node.text
                for node in grid.default_slot.children
                if node.style.get('grid-column') == '1'
            ] == ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
            headers = [
                (node.text, node.style['grid-column'])
                for node in grid.default_slot.children
                if node.style.get('grid-row') == '1'
            ]
            assert headers == [
                ('Oct', '4'),
                ('Nov', '8'),
            ]  # The first partial September has no label.
            readout = next(
                node for node in container.descendants() if node.props.get('role') == 'status'
            )
            order = list(container.descendants())
            legend = next(
                node for node in order if isinstance(node, ui.label) and node.text == 'Present'
            )
            assert order.index(grid) < order.index(legend) < order.index(readout)
            square = next(
                node
                for node in cells(grid)
                if node.props['title'].startswith('beginning 2026-11-01 ')
            )
            trigger(square, 'focus')
            assert square.props['aria-pressed'] == 'true'
            assert readout.text == square.props['aria-label']
            assert not any(
                node.value for node in container.descendants() if isinstance(node, ui.dialog)
            )
            trigger(container, 'click')
            assert readout.text == '' and square.props['aria-pressed'] == 'false'
            trigger(square, 'click')
            dialog = next(
                node
                for node in container.client.layout.descendants()
                if isinstance(node, ui.dialog)
            )
            for width, rem, count in [
                (344.96, 16, 24),
                (344, 16, 12),
                (183.68, 16, 12),
                (112, 16, 6),
                (431.2, 20, 24),
            ]:
                await resize(dialog, width, rem)
                detail = grids(dialog)[0]
                assert len(cells(detail)) == count * 6
                labels = [
                    node.text
                    for node in detail.default_slot.children
                    if node.style.get('grid-row') == '1'
                ]
                rows = [
                    node.text
                    for node in detail.default_slot.children
                    if node.style.get('grid-column') == '1'
                ]
                if count == 24:
                    assert labels == [f'{hour:02d}' for hour in range(0, 24, 2)]
                    assert rows == [':00', ':10', ':20', ':30', ':40', ':50']
                else:
                    assert labels == (
                        ['00', '04', '08', '12', '16', '20'] if count == 12 else ['12', '16', '20']
                    )
                    assert rows == [':00', ':20', ':40', ':00', ':20', ':40']
                # No re-render if the resulting column count is unchanged.
                await resize(dialog, width, rem)
                assert grids(dialog)[0] is detail
            await resize(container, 544)
            assert dialog.value and not dialog.is_deleted
            assert readout.is_deleted
            assert all(
                square.props['aria-pressed'] == 'false' for square in cells(grids(container)[0])
            )
            assert len(container._event_listeners) == 1
            current = cells(grids(container)[0])[0]
            trigger(current, 'focus')
            current_readout = next(
                node for node in container.descendants() if node.props.get('role') == 'status'
            )
            trigger(container, 'click')
            assert current.props['aria-pressed'] == 'false' and current_readout.text == ''
        finally:
            container.delete()

    asyncio.run(check())


def test_uptime_dialog_uses_real_samples_and_keeps_empty_slots_blank(database, monkeypatch):
    now = day + TimeDelta(hours=12, minutes=35)
    add_pings(
        [day + TimeDelta(hours=h, minutes=m) for h, m in [(10, 3), (10, 13), (12, 4), (12, 20)]]
    )

    class Clock(DateTime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(heatmap, 'DateTime', Clock)
    container = ui.column()

    async def check():
        monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
        with container:
            heatmap.render_uptime(1)
        try:
            await resize(container, 544)
            squares = cells(grids(container)[0])
            assert len(squares) == 1
            trigger(squares[0], 'click')
            dialog = next(
                node
                for node in container.client.layout.descendants()
                if isinstance(node, ui.dialog)
            )
            await resize(dialog, 256)
            assert any(
                isinstance(node, ui.label) and node.text == '21.4% uptime'
                for node in dialog.descendants()
            )
            titles = [square.props['title'] for square in cells(grids(dialog)[0])]
            assert len(titles) == 8
            readout = next(
                node for node in dialog.descendants() if node.props.get('role') == 'status'
            )
            for square in cells(grids(dialog)[0]):
                trigger(square, 'click')
                assert readout.text == square.props['aria-label']
                assert square.props['aria-pressed'] == 'true'
                assert (
                    sum(cell.props['aria-pressed'] == 'true' for cell in cells(grids(dialog)[0]))
                    == 1
                )
            card = next(node for node in readout.ancestors() if isinstance(node, ui.card))
            assert len(card._event_listeners) == 1
            trigger(card, 'click', square.props['data-heatmap-cell'])
            assert square.props['aria-pressed'] == 'true'
            trigger(card, 'click')
            assert readout.text == ''
            assert all(cell.props['aria-pressed'] == 'false' for cell in cells(grids(dialog)[0]))
        finally:
            container.delete()

    asyncio.run(check())


def test_migration_replaces_index_and_preserves_telemetry(tmp_path, monkeypatch):
    path = tmp_path / 'data.sqlite'
    engine = create_engine(f'sqlite:///{path}')
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(db.Base(id=1, subd='heat1'))
        session.commit()
        session.add(db.Telemetry(base_id=1, created_at=day, net_rx=12345))
        session.commit()
    with engine.begin() as conn:
        conn.exec_driver_sql('DROP INDEX ix_telemetry_base_created_at')
        conn.exec_driver_sql('CREATE INDEX ix_telemetry_base_id ON telemetry(base_id)')
        conn.exec_driver_sql('PRAGMA user_version=55')
    engine.dispose()
    monkeypatch.setattr(migrate_db.util, 'rotate_backups', lambda old, new: os.replace(new, old))
    migrate_db.migrate(str(path))
    with migrate_db.sqlite_conn(str(path)) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 56
        assert conn.execute('SELECT base_id, net_rx FROM telemetry').fetchall() == [(1, 12345)]
        indexes = {row[1] for row in conn.execute('PRAGMA index_list(telemetry)')}
        assert indexes == {'ix_telemetry_base_created_at'}
        for query in [
            'SELECT min(created_at) FROM telemetry WHERE base_id=1',
            "SELECT created_at FROM telemetry WHERE base_id=1 "
            "AND created_at >= '2026-11-01' AND created_at < '2026-12-01'",
            'DELETE FROM telemetry WHERE base_id=1',
        ]:
            plan = conn.execute('EXPLAIN QUERY PLAN ' + query).fetchall()
            assert any('ix_telemetry_base_created_at' in row[-1] for row in plan)
