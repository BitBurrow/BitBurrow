import ast
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
from nicegui import ui, Client
import re
from typing import Dict, List, Tuple
import logging
import zoneinfo
import hub.util as util

Berror = util.Berror
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)

###
### HTML page set-up
###


def enable_external_links_new_tab():
    """Add JavaScript to the page to make external links open in a new browser tab."""

    ui.run_javascript(
        r'''
            (function() {
              if (window.__ng_ext_link_handler_installed) return;
              window.__ng_ext_link_handler_installed = true;
              const handler = (e) => {
                if (e.defaultPrevented || e.button !== 0 || e.metaKey ||
                    e.ctrlKey || e.shiftKey || e.altKey) return;
                const path = e.composedPath ? e.composedPath() : [];
                const a = path.find(n => n && n.tagName === 'A' && n.href);
                if (!a) return;
                try {
                  const hrefAttr = a.getAttribute('href') || '';
                  const url = new URL(a.href, location.href);
                  const isHttp = /^https?:$/i.test(url.protocol);
                  const isInternal = isHttp && (
                    url.origin === location.origin ||
                    hrefAttr.startsWith('/') ||
                    !/^\w+:/.test(hrefAttr)
                  );
                  if (isHttp && !isInternal) {
                    e.preventDefault();
                    e.stopPropagation();
                    if (e.stopImmediatePropagation) e.stopImmediatePropagation();
                    window.open(url.href, '_blank', 'noopener,noreferrer');
                  }
                } catch (_) {}
              };
              document.removeEventListener('click', window.__ng_ext_link_handler, true);
              window.__ng_ext_link_handler = handler;
              document.addEventListener('click', handler, true);
            })();
        '''
    )


###
### human display utils
###


async def client_timezone(client: Client) -> str:
    """Detect and cache browser timezone name in client.storage."""
    tz_name = client.storage.get('timezone')
    if tz_name:
        return tz_name
    tz_name = await client.run_javascript('return Intl.DateTimeFormat().resolvedOptions().timeZone')
    client.storage['timezone'] = tz_name
    return tz_name


async def format_time(t: DateTime, client: Client) -> str:
    if t.tzinfo is None or t.tzinfo.utcoffset(t) is None:
        t = t.replace(tzinfo=TimeZone.utc)
    tz_name = await client_timezone(client)
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        local_dt = t
    else:
        local_dt = t.astimezone(tz)
    offset = local_dt.utcoffset() or TimeDelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = '+' if total_minutes >= 0 else '-'
    hours, minutes = divmod(abs(total_minutes), 60)
    if minutes:
        utc_part = f'UTC{sign}{hours:02d}:{minutes:02d}'
    else:
        utc_part = f'UTC{sign}{hours}'
    return f'{local_dt:%Y-%m-%d %H:%M} ({utc_part})'


def human_duration(delta: TimeDelta, positive_only=False) -> str:
    """Converts duration into short, human-readable duration, e.g. '30 hours'."""
    if delta is None:
        return '–'
    seconds = int(delta.total_seconds())
    if positive_only and seconds <= 0:
        return '–'
    if seconds < 0:
        sign = '-'
        seconds = abs(seconds)
    else:
        sign = ''
    if seconds < 100:
        value = seconds
        unit = "second"
    elif seconds < 6000:  # 100 minutes
        value = (seconds + 30) // 60  # round to the nearest minute
        unit = "minute"
    elif seconds < 180_000:  # 50 hours
        value = (seconds + 1800) // 3600
        unit = "hour"
    elif seconds < 2_592_000:  # 30 days
        value = (seconds + 43200) // 86400
        unit = "day"
    elif seconds < 6_048_000:  # 10 weeks
        value = (seconds + 302400) // 604800
        unit = "week"
    elif seconds < 51_840_000:  # 20 months
        value = (seconds + 1_296_000) // 2_592_000
        unit = "month"
    else:
        value = (seconds + 15_768_000) // 31_536_000
        unit = "year"
    if value != 1:
        unit += 's'
    return f'{sign}{value} {unit}'


def test_human_duration():
    assert human_duration(None) == '–'
    assert human_duration(-TimeDelta(seconds=129)) == "-2 minutes"
    assert human_duration(TimeDelta(seconds=129)) == "2 minutes"
    assert human_duration(TimeDelta(seconds=129), positive_only=True) == "2 minutes"
    assert human_duration(TimeDelta(seconds=129), positive_only=False) == "2 minutes"
    assert human_duration(-TimeDelta(seconds=129), positive_only=True) == '–'
    assert human_duration(-TimeDelta(seconds=129), positive_only=False) == "-2 minutes"
    assert human_duration(-TimeDelta(seconds=0)) == "0 seconds"
    assert human_duration(TimeDelta(seconds=0)) == "0 seconds"
    assert human_duration(TimeDelta(seconds=0), positive_only=True) == '–'
    assert human_duration(-TimeDelta(seconds=2)) == "-2 seconds"
    assert human_duration(-TimeDelta(seconds=99)) == "-99 seconds"
    assert human_duration(TimeDelta(seconds=1)) == "1 second"
    assert human_duration(TimeDelta(seconds=5)) == "5 seconds"
    assert human_duration(-TimeDelta(seconds=5)) == "-5 seconds"
    assert human_duration(TimeDelta(seconds=0)) == "0 seconds"
    assert human_duration(TimeDelta(seconds=100)) == "2 minutes"
    assert human_duration(TimeDelta(seconds=149)) == "2 minutes"
    assert human_duration(TimeDelta(seconds=151)) == "3 minutes"  # 150 could go either way
    assert human_duration(TimeDelta(seconds=152)) == "3 minutes"
    assert human_duration(TimeDelta(seconds=160)) == "3 minutes"
    assert human_duration(TimeDelta(minutes=1)) == "60 seconds"
    assert human_duration(TimeDelta(minutes=2)) == "2 minutes"
    assert human_duration(-TimeDelta(minutes=2)) == "-2 minutes"
    assert human_duration(TimeDelta(minutes=99)) == "99 minutes"
    assert human_duration(TimeDelta(seconds=6000)) == "2 hours"
    assert human_duration(TimeDelta(hours=1)) == "60 minutes"
    assert human_duration(-TimeDelta(hours=49)) == "-49 hours"
    assert human_duration(-TimeDelta(hours=50)) == "-2 days"
    assert human_duration(TimeDelta(hours=1)) == "60 minutes"
    assert human_duration(TimeDelta(hours=25)) == "25 hours"
    assert human_duration(TimeDelta(hours=30)) == "30 hours"
    assert human_duration(TimeDelta(days=2)) == "48 hours"
    assert human_duration(TimeDelta(days=29)) == "29 days"
    assert human_duration(TimeDelta(days=30)) == "4 weeks"
    assert human_duration(TimeDelta(days=1)) == "24 hours"
    assert human_duration(TimeDelta(days=29)) == "29 days"
    assert human_duration(TimeDelta(days=28)) == "28 days"
    assert human_duration(TimeDelta(weeks=2)) == "14 days"
    assert human_duration(TimeDelta(weeks=3)) == "21 days"
    assert human_duration(TimeDelta(weeks=10)) == "2 months"
    assert human_duration(TimeDelta(weeks=5)) == "5 weeks"
    assert human_duration(TimeDelta(weeks=3)) == "21 days"
    assert human_duration(TimeDelta(weeks=8)) == "8 weeks"
    assert human_duration(TimeDelta(weeks=9)) == "9 weeks"
    assert human_duration(TimeDelta(days=300)) == "10 months"
    assert human_duration(TimeDelta(days=600)) == "2 years"
    assert human_duration(TimeDelta(days=60)) == "9 weeks"
    assert human_duration(TimeDelta(days=300)) == "10 months"
    assert human_duration(TimeDelta(days=90)) == "3 months"
    assert human_duration(TimeDelta(days=365)) == "12 months"
    assert human_duration(TimeDelta(days=360)) == "12 months"
    assert human_duration(TimeDelta(days=800)) == "2 years"
    assert human_duration(TimeDelta(days=2000)) == "5 years"
    assert human_duration(TimeDelta(days=800)) == "2 years"
    assert human_duration(TimeDelta(days=365 * 20)) == "20 years"


def human_user_agent(ua: str) -> str:
    if not ua:
        return "Unknown device"
    os_name = 'Unknown OS'
    if 'Windows' in ua:
        os_name = 'Windows'
    elif 'Macintosh' in ua or 'Mac OS X' in ua:
        os_name = 'macOS'
    elif 'Linux' in ua and 'Android' not in ua:
        os_name = 'Linux'
    elif 'Android' in ua:
        os_name = 'Android'
    elif 'iPhone' in ua:
        os_name = 'iPhone iOS'
    elif 'iPad' in ua:
        os_name = 'iPad iOS'
    browser = "Browser"
    if 'Edg/' in ua or 'Edge/' in ua:
        browser = 'Edge'
    elif 'Chrome/' in ua and 'Chromium' not in ua:
        browser = 'Chrome'
    elif 'Safari/' in ua and 'Chrome/' not in ua:
        browser = 'Safari'
    elif 'Firefox/' in ua:
        browser = 'Firefox'
    elif 'Chromium' in ua:
        browser = 'Chromium'
    return f'{browser} on {os_name}'


###
### elements that can go in ctags
###


def input(
    placeholder: str = '',
    label: str = '',
    icon=None,
    icon_position: str = 'left',
    readonly: bool = False,
    align: str = 'left',
    font_size=None,  # e.g. '14px', '1rem'
    show_copy: bool = False,
):
    # docs: https://nicegui.io/documentation/input
    # icon names: https://quasar.dev/vue-components/icon/#ionicons
    props = [f'input-class="text-{align}"']
    if readonly:
        props.append('readonly')  # QInput readonly prop
    obj = ui.input(label=label, placeholder=placeholder).props(' '.join(props)).classes('w-full')
    if font_size:
        obj.style(f'font-size: {font_size}')
    if icon:
        with obj.add_slot('prepend' if icon_position == 'left' else 'append'):
            ui.icon(icon).classes('opacity-80')

    def on_copy(o=obj) -> None:
        ui.clipboard.write(o.value or '')
        ui.notify('Copied to clipboard')

    if show_copy:
        with obj.add_slot('append'):
            ui.button('Copy', on_click=on_copy).props('flat dense')
    return obj


def headline(text: str, align='center'):
    return ui.markdown(text).classes(f'w-full text-{align} text-3xl')


def image(source='', align='left', alt='', width=None):
    img = ui.image(source if '/' in source else f'ui/img/{source}').props(f'alt={alt}')
    if width:
        if width.endswith('%'):
            img.style(f'width:{width};')
        elif width.replace('.', '', 1).isdigit():
            img.style(f'width:{width}px;')
        else:
            img.style(f'width:{width};')
    if align == 'center':
        img.classes('mx-auto block')
    elif align == 'right':
        img.classes('ml-auto block')
    elif align == 'left':
        img.classes('mr-auto block')
    return img


def button(text=''):
    return ui.button(text)


def checkbox(label='', value=False, icon=None):
    row = ui.row().classes('items-center gap-2')
    with row:
        if icon:
            ui.icon(icon).classes('opacity-80')
        return ui.checkbox(label, value=value)


def comment(text=''):  # for adding author comments within the .md file
    return None


elements_available = {
    'input': input,
    'headline': headline,
    'image': image,
    'button': button,
    'checkbox': checkbox,
    'comment': comment,
}


###
### process ctags in Markdown files
###


def test_split_at_ctags():
    input = '{{ zero }}{{ one }}{ foo }{{bar}}{{ two }}'
    assert split_at_ctags(input) == ['{{ zero }}', '{{ one }}', '{ foo }{{bar}}', '{{ two }}']
    input = 'foo {{ three }} bar{{ four }} }}'  # unmatched close are ignored
    assert split_at_ctags(input) == ['foo ', '{{ three }}', ' bar', '{{ four }}', ' }}']
    try:
        input = '{{ thir{{ teen }}{{ fourteen }}'
        split_at_ctags(input)
    except:
        pass
    else:
        assert False, "test_split_at_ctags() did not catch nexted ctag"


def parse_kwargs(param_str: str):
    """Convert a string, e.g. 'length=2, prefix="fo"', into kwargs safely."""
    tree = ast.parse(f"f({param_str})", mode="exec")
    call = tree.body[0].value
    kwargs = dict()
    for kw in call.keywords:
        kwargs[kw.arg] = ast.literal_eval(kw.value)
    return kwargs


def split_at_ctags(text: str) -> List[str]:
    """Split text at '{{ ... }}' ctags.

    Returns a list of strings where each piece either does not contain '{{ ', or
    it starts with '{{ ' and ends with ' }}'. Raises an exception on unmatched or nested ctags.
    """
    ctags: List[str] = list()
    i = 0
    len_text = len(text)
    while i < len_text:
        start = text.find("{{ ", i)
        if start == -1:  # no more ctags
            if i < len_text:
                ctags.append(text[i:])
            break
        if start > i:  # plain text before the ctag
            ctags.append(text[i:start])
        end = text.find(" }}", start)
        if end == -1:
            raise Berror(f"B04365 unmatched ctag: {text[start:start + 10]}...")
        nested = text.find("{{ ", start + 3, end)
        if nested != -1:
            raise Berror(f"B68301 nested '{{ ... }}' ctags are not allowed: {text[start:end+3]}")
        ctags.append(text[start : end + 3])
        i = end + 3
    return ctags


def render_markdown_with_ctags(md: str, idelem: Dict[str, object], within=None):
    splits = split_at_ctags(md)
    for split in splits:
        if split.startswith('{{ '):
            # parse ctag, e.g. 'image(source="abc.png", width="50%")'
            m = re.fullmatch(r'\s*([^\d\W]\w*)\(([^\)]*)\)\s*', split[3:-3])
            if not m:
                raise Berror(f"B75468 syntax error in ctag: {split}")
            if m.group(1) not in elements_available:
                raise Berror(f"B50657 unknown element '{m.group(1)}' in: {split}")
            try:
                params = parse_kwargs(m.group(2))
            except (SyntaxError, ValueError, AttributeError, IndexError, TypeError):
                raise Berror(f"B76131 malformed parameters in: {m.group(0)}")
            id = params.get('id', None)
            if id:
                del params['id']  # don't pass to element
            try:
                if within:
                    with within:
                        obj = (elements_available[m.group(1)])(**params)
                else:
                    obj = (elements_available[m.group(1)])(**params)
            except TypeError as e:
                raise Berror(f"B63098 error in '{m.group(0)}': {e}")
            if id:
                idelem[id] = obj
        else:
            split_bare = split.strip(' \t\r\n')
            if split_bare:  # avoid extra vertical space between elements
                if within:
                    with within:
                        ui.markdown(split_bare)
                else:
                    ui.markdown(split_bare)


def render_expansion(title_md: str, within=None):
    if within:
        with within:
            e = ui.expansion(icon='chevron_right').props('dense').classes('rounded-xl shadow-sm')
    else:
        e = ui.expansion(icon='chevron_right').props('dense').classes('rounded-xl shadow-sm')
    with e.add_slot('header'):
        # using ui.row() with ui.icon() and ui.markdown() split bullet when screen was narrow
        content = f'■\u2000 {title_md.lstrip("# \t")}'  # EN QUAD to force more space after bullet
        ui.markdown(content).classes('!m-0 !p-0 text-sm font-base')
    return e


###
### parse Markdown files
###


def parse_markdown_sections(md: str):
    lines = md.splitlines(keepends=True)
    root = None
    stack = list()
    current_section = None
    buffer = ''
    heading_re = re.compile(r'^(#+)\s+(.*)\s*$')
    for line in lines:
        m = heading_re.match(line)
        if m:
            hashes, title_md = m.groups()
            level = len(hashes)
            title = title_md.lstrip('# \t')
            if buffer and current_section is not None:
                current_section.append(buffer)
            buffer = ''
            if root is None:
                root = [title]
                stack = [(level, root)]
                current_section = root
                continue
            if title == '..':  # special end-of-section marker
                if len(stack) > 1:
                    stack.pop()
                current_section = stack[-1][1]
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = root if not stack else stack[-1][1]
            new_node = [title]
            parent.append(new_node)
            stack.append((level, new_node))
            current_section = new_node
        else:
            buffer += line
    if buffer and current_section is not None:
        current_section.append(buffer)
    return root


def test_parse_markdown_sections():
    input = (
        "## one\n\n"
        + "Text within 'one'.\n\n"
        + "### two\n\n"
        + "Text within 'two'.\n\n"
        + "## ..\n\n"
        + "Text within 'one' again.\n"
    )
    expected_output = [
        "one",
        "\nText within 'one'.\n\n",
        ["two", "\nText within 'two'.\n\n"],
        "\nText within 'one' again.\n",
    ]
    assert parse_markdown_sections(input) == expected_output


def render_page(sections, is_logged_in: bool):
    idelem: Dict[str, object] = dict()  # map each element ID to its actual object
    within = list()  # expansion object stack
    within.append(None)  # something for the outer-most level to build on
    enable_external_links_new_tab()
    with ui.header().classes('app-header'):
        with ui.row().classes('w-full items-center'):  # better placement of menu drop-down
            ui.space()  # right-justify the menu
            menu_icon = ui.icon('menu').classes(
                'cursor-pointer text-white'
                ' scale-175'  # scale menu icon
                ' leading-none'  # keep small header bar hieght
            )
            with ui.menu().props('anchor="top right" self="bottom right"') as menu:
                if is_logged_in:
                    ui.menu_item("Home", lambda: ui.navigate.to('/home'))
                    ui.menu_item("View loggin sessions", lambda: ui.navigate.to('/login_sessions'))
                    ui.menu_item("Log out", lambda: ui.navigate.to('/logout'))
                else:
                    ui.menu_item("Log in", lambda: ui.navigate.to('/login'))
            menu_icon.on('click', lambda e: menu.open())
    with ui.column().classes('w-full max-w-screen-sm mx-auto px-3'):
        stack = list()  # the part of the tree that is left to traverse
        stack.append(sections[1:])  # push the list without title
        while len(stack):  # depth-first traversal of sections
            secs = stack.pop()
            for i, sec in enumerate(secs):
                if isinstance(sec, str):
                    w = within[-1]  # current expansion object
                    render_markdown_with_ctags(sec, idelem, w)
                else:  # sec is a list representing a section header and sections
                    assert isinstance(sec[0], str)
                    w = within[-1]  # current expansion object
                    within.append(render_expansion(sec[0], w))
                    stack.append(secs[i + 1 :])  # remainder of current list
                    stack.append(sec[1:])  # sublist without title
                    break
            else:  # done with secs at this level
                within.pop()
    return idelem
