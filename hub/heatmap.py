from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
from nicegui import ui
import hub.db as db

cell_size = 0.64  # all dimensions are in rem
gap = 0.2
label_width = 1.4
label_height = 1.0
grid_style = f'gap: {gap}rem; font-size: 0.56rem; line-height: 0.8rem; margin-bottom: 0.4rem'
container_style = 'gap: 0.4rem; font-size: 0.6rem'
uptime_colors = (
    (990, '#16a34a', ">99%"),
    (970, '#84cc16', ">97%"),
    (950, '#eab308', ">95%"),
    (930, '#f97316', ">93%"),
    (-1, '#dc2626', "low"),
)


class CellDetails:
    """Pair cell details with a selection that can be dismissed from its card."""

    def __init__(self, dismiss_on: ui.element | None = None):
        self.square: ui.element | None = None
        self.label: ui.label | None = None
        if dismiss_on is not None:
            # bind once when the card is built; resizing only rebuilds its grid and readout
            dismiss_on.on(
                'click.capture',
                self.on_card_click,
                js_handler='''(event) => {
                    const cell = event.target.closest('[data-heatmap-cell]');
                    const active = document.activeElement;
                    if (active?.matches('[data-heatmap-cell]') && active !== cell &&
                        event.currentTarget.contains(active)) active.blur();
                    emit(cell?.getAttribute('data-heatmap-cell') ?? null);
                }''',
            )

    def render_label(self) -> ui.label:
        self.clear()
        self.label = (
            ui.label("")  # could put instructions here, e.g. "Tap a square for more details"
            .classes('w-full min-w-0 whitespace-normal break-words text-caption')
            .style('min-height: 2.5rem')
            .props('role=status aria-live=polite aria-atomic=true')
        )
        return self.label

    def clear(self) -> None:
        if self.square is not None and not self.square.is_deleted:
            self.square.style.pop('box-shadow', None)
            self.square.style.pop('z-index', None)
            self.square.props['aria-pressed'] = 'false'
        self.square = None
        if self.label is not None and not self.label.is_deleted:
            self.label.set_text('')

    def on_card_click(self, e) -> None:
        if self.label is None or e.args != self.label.html_id:
            self.clear()

    def select(self, e) -> None:
        self.clear()
        self.square = e.sender
        self.square.style('box-shadow: 0 0 0 2px white, 0 0 0 6px #2563eb; z-index: 1')
        self.square.props['aria-pressed'] = 'true'
        if self.label is not None:
            self.label.set_text(self.square.props['aria-label'])


def render_grid(
    start: DateTime,
    columns: int,
    cell_duration: TimeDelta,
    row_labels,
    column_label,
    sample,
    colors,
    on_select=None,
    show_legend: bool = False,
    details: CellDetails | None = None,
) -> None:
    """Fill [start, end) cells down each UTC column; None samples leave the background blank.
    Samples supply a numeric value and description. Colors have descending, exclusive
    lower bounds and legend labels; the last color also covers values below every bound.
    Tapping or focusing a cell reveals its description; activation also calls on_select.
    """
    start = start.replace(tzinfo=start.tzinfo or TimeZone.utc).astimezone(TimeZone.utc)
    rows = len(row_labels)
    if columns < 1 or rows < 1 or cell_duration <= TimeDelta(0) or not colors:
        raise ValueError("Heatmaps need positive dimensions, a positive duration, and colors")
    details = details or CellDetails()
    squares = []
    with ui.grid(
        columns=f'{label_width}rem repeat({columns}, {cell_size}rem)',
        rows=f'{label_height}rem repeat({rows}, {cell_size}rem)',
    ).style(grid_style):
        for row, label in enumerate(row_labels, 2):
            ui.label(label).style(f'grid-column: 1; grid-row: {row}')
        for column in range(columns):
            column_start = start + column * rows * cell_duration
            if label := column_label(column_start):
                ui.label(label).style(f'grid-column: {column + 2}; grid-row: 1')
            for row in range(rows):
                begin = column_start + row * cell_duration
                end = begin + cell_duration
                result = sample(begin, end)
                if result is None:
                    continue
                value, description = result
                color = next((color for bound, color, _ in colors if value > bound), colors[-1][1])
                title = f"{begin:beginning %Y-%m-%d %H:%M} UTC: {description}"
                square = (
                    ui.element('button')
                    .classes(
                        'rounded-sm cursor-pointer focus-visible:outline '
                        'focus-visible:outline-2 focus-visible:outline-offset-2'
                    )
                    .props('type=button')
                    .style(
                        f'grid-column: {column + 2}; grid-row: {row + 2}; '
                        f'background-color: {color}; padding: 0; border: 0'
                    )
                )
                square.props.update({'title': title, 'aria-label': title})
                squares.append((square, begin))
    if show_legend:
        with ui.row().classes('items-center').style('column-gap: 0.6rem; row-gap: 0.2rem'):
            for _, color, label in colors:
                with ui.row().classes('items-center no-wrap').style(f'gap: {gap}rem'):
                    ui.element('div').classes('rounded-sm').style(
                        f'width: {cell_size * 0.75}rem; height: {cell_size * 0.75}rem; '
                        f'background-color: {color}'
                    )
                    ui.label(label)
    readout = details.render_label()
    for square, begin in squares:
        square.props.update(
            {
                'aria-controls': readout.html_id,
                'aria-pressed': 'false',
                'data-heatmap-cell': readout.html_id,
            }
        )
        # handle clicks as well as focus: touch browsers need not focus tapped buttons
        square.on('click', details.select, args=[])
        square.on('focus', details.select, args=[])
        if on_select:
            square.on('click', lambda _, time=begin: on_select(time), args=[])


def responsive(render, choose_columns=lambda capacity: capacity) -> None:
    """Render only after measuring the container, and again when its column count changes."""
    columns = 0

    @ui.refreshable
    def draw() -> None:
        if columns:
            render(columns)

    async def on_resize(e) -> None:
        nonlocal columns
        if e.args['width'] <= 0:
            return
        width = e.args['width'] / e.args['rem']
        capacity = max(1, int((width - label_width) / (cell_size + gap) + 1e-9))
        new_columns = choose_columns(capacity)
        if new_columns != columns:
            columns = new_columns
            await draw.refresh()

    with ui.column().classes('w-full min-w-0 gap-0'):
        ui.element('q-resize-observer').props('debounce=200').on(
            'resize',
            on_resize,
            js_handler='''(size) => emit({
                width: size.width,
                rem: parseFloat(getComputedStyle(document.documentElement).fontSize)
            })''',
        )
        draw()


def show_day(dialog, title: str, start: DateTime, sample, colors) -> None:
    def render(columns: int) -> None:
        hours_per_column = 1 if columns == 24 else 2
        minutes = 10 * hours_per_column
        step = TimeDelta(minutes=minutes)
        first = start + TimeDelta(days=1) - columns * 6 * step
        render_grid(
            first,
            columns,
            step,
            [f':{row * minutes % 60:02d}' for row in range(6)],
            lambda time: f'{time:%H}' if time.hour % (2 * hours_per_column) == 0 else '',
            sample,
            colors,
            details=details,
        )

    dialog.clear()
    dialog.props['aria-label'] = f"{title} for {start:%A, %d %B %Y} (UTC)"
    with dialog, ui.card().classes('w-full max-w-xl').style(container_style) as card:
        details = CellDetails(card)
        ui.label(f"{start:%Y-%m-%d} · UTC").classes('text-subtitle2')
        if (result := sample(start, start + TimeDelta(days=1))) is not None:
            ui.label(result[1]).classes('w-full whitespace-normal break-words text-caption')
        # narrow views keep whole hour pairs, showing the latest pairs when necessary
        responsive(render, lambda capacity: 24 if capacity >= 24 else min(12, capacity))
        ui.button("Close", on_click=dialog.close).props('flat').classes('self-end')
    dialog.open()


def render_calendar(title: str, sample, colors, now: DateTime) -> None:
    """Show a responsive calendar and day dialog for any interval-sampling metric."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def render(weeks: int) -> None:
        heading.set_text(f"{title} · past {weeks} weeks")
        start = today - TimeDelta(days=(today.weekday() + 1) % 7, weeks=weeks - 1)
        render_grid(
            start,
            weeks,
            TimeDelta(days=1),
            ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"),
            # a month's first Sunday starts the column containing its 7th day
            lambda time: time.strftime('%b') if time.day <= 7 else '',
            sample,
            colors,
            on_select=lambda day: show_day(dialog, title, day, sample, colors),
            show_legend=True,
            details=details,
        )

    with ui.column().classes('w-full min-w-0').style(container_style) as container:
        card = next(
            (node for node in container.ancestors() if isinstance(node, ui.card)), container
        )
        details = CellDetails(card)
        dialog = ui.dialog()  # keep the dialog alive when the calendar is refreshed
        with ui.row().classes('w-full items-baseline justify-between').style(f'gap: {gap}rem'):
            heading = ui.label(title)
        responsive(render)


def render_uptime(base_id: int) -> None:
    now = DateTime.now(TimeZone.utc)
    history = db.UptimeHistory(base_id, now)

    def sample(start: DateTime, end: DateTime) -> tuple[float, str] | None:
        if (result := history.sample(start, end)) is None:
            return None
        value, points = result
        return value, f"{value / 10:g}% uptime"

    render_calendar("Uptime", sample, uptime_colors, now=now)
