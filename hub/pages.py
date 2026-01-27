import asyncio
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
import logging
from nicegui import app, ui, Client
import os
import re
import yaml
import hub.uif as uif
import hub.db as db
import hub.login_key as lk
import hub.auth as auth
import hub.config as conf
import hub.util as util

Berror = util.Berror
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)
ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui')

###
### page: /welcome
###


@ui.page('/welcome')
def welcome(client: Client):
    if auth.is_logged_in(client):
        ui.navigate.to('/home')
        return
    md_path = os.path.join(ui_path, 'welcome.md')
    with open(md_path, 'r', encoding='utf-8') as f:
        sections = uif.parse_markdown_sections(f.read())
    ui.run_javascript(f"document.title = '{sections[0]}'")
    qparam_coupon = client.request.query_params.get('coupon')
    uif.render_header(is_logged_in=False)
    idelem = uif.render_content(sections)
    if qparam_coupon:
        idelem['coupon_code'].set_value(qparam_coupon)

    async def on_continue():
        coupon = lk.plain_login_key(idelem['coupon_code'].value.upper() or '')
        idelem['coupon_code'].value = lk.styled_login_key(coupon)  # add dashes where needed
        try:
            aid = db.validate_login_key(coupon, allowed_kinds=db.coupon)
        except db.CredentialsError as e:
            ui.notify(db.style_login_key_error_message(str(e), db.coupon))
            return
        idelem['continue'].disable()
        ui.navigate.to(f'/confirm?coupon={coupon}')

    async def check_enter(e):
        if e.args.get('key') == 'Enter':
            await on_continue()

    idelem['continue'].on_click(callback=on_continue)
    idelem['coupon_code'].on('keydown', check_enter)  # pressing Enter submits form


###
### page: /confirm
###


@ui.page('/confirm')
def confirm(client: Client):
    if auth.is_logged_in(client):
        ui.navigate.to('/home')
        return
    qparam_coupon = client.request.query_params.get('coupon')
    if not qparam_coupon:  # coupon code is required
        ui.navigate.to(f'/welcome?coupon={qparam_coupon}')
    try:  # validate (no other way to confirm that user came via /welcome)
        aid = db.validate_login_key(qparam_coupon, allowed_kinds=db.coupon)
    except db.CredentialsError as e:  # send them back to /welcome
        ui.navigate.to(f'/welcome?coupon={qparam_coupon}')
    md_path = os.path.join(ui_path, 'confirm.md')
    with open(md_path, 'r', encoding='utf-8') as f:
        sections = uif.parse_markdown_sections(f.read())
    ui.run_javascript(f"document.title = '{sections[0]}'")
    uif.render_header(is_logged_in=False)
    idelem = uif.render_content(sections)
    login_key = db.new_account(
        kind=db.AccountKind.NONE,  # in DB but disabled until confirmed
        valid_for=TimeDelta(days=1),  # expire in 1 day if not confirmed
        parent_account_id=aid,  # coupon code used
    )
    idelem['login_key'].set_value(lk.styled_login_key(login_key))

    async def on_continue():
        if not idelem['have_written_down'].value:
            ui.notify("Required input is missing")
            return
        idelem['continue'].disable()
        idelem['login_key'].set_value(lk.styled_login_key('*' * lk.login_key_len))
        await asyncio.sleep(1)  # let user see the importantce of keeping the login key secret
        aid = db.update_account(  # validate the new account
            login=login_key,  # find account by 'login' portion only
            kind=db.AccountKind.MANAGER,  # it's now a full login key
            valid_for=TimeDelta(days=10950),
        )
        auth.log_in(aid, login_key, idelem['keep_me_logged_in'].value, client.request)

    idelem['continue'].on_click(callback=on_continue)
    # do not store login_key anywhere


###
### page: /login
###


@ui.page('/login')
def login(client: Client):
    if auth.is_logged_in(client):
        ui.navigate.to('/home')
        return
    md_path = os.path.join(ui_path, 'login.md')
    with open(md_path, 'r', encoding='utf-8') as f:
        sections = uif.parse_markdown_sections(f.read())
    ui.run_javascript(f"document.title = '{sections[0]}'")
    uif.render_header(is_logged_in=False)
    idelem = uif.render_content(sections)

    async def on_continue():
        login_key = lk.plain_login_key(idelem['login_key'].value.upper() or '')
        idelem['login_key'].value = lk.styled_login_key(login_key)  # add dashes where needed
        try:
            aid = db.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
        except db.CredentialsError as e:
            ui.notify(db.style_login_key_error_message(str(e), db.admin_or_manager))
            return
        auth.log_in(aid, login_key, idelem['keep_me_logged_in'].value, client.request)

    async def check_enter(e):
        if e.args.get('key') == 'Enter':
            await on_continue()

    idelem['continue'].on_click(callback=on_continue)
    idelem['login_key'].on('keydown', check_enter)  # pressing Enter submits form


###
### page: /home
###


@ui.page('/home')
def home(client: Client):
    try:
        lsid, aid, kind = auth.require_login(client)
    except db.CredentialsError:
        return
    md_path = os.path.join(ui_path, 'home.md')
    with open(md_path, 'r', encoding='utf-8') as f:
        sections = uif.parse_markdown_sections(f.read())
    ui.run_javascript(f"document.title = '{sections[0]}'")
    uif.render_header(is_logged_in=True)
    idelem = uif.render_content(sections)

    def build_rows():
        db.update_wg_show()
        rows = list()
        now = DateTime.now(TimeZone.utc)
        id_to_query = None if kind == db.AccountKind.ADMIN else aid
        for dev, inf in db.iter_get_device_by_account_id(aid=id_to_query):
            new_row = {
                'id': dev.id,
                'login': dev.account.login,
                'name': dev.name,
                'slug': dev.name_slug,
                'subd': f'{dev.subd}.{conf.get('frontend.domain')}' if dev.subd else '-',
            }
            if inf and inf.last_endpoint:
                new_row['last_ip'] = inf.last_endpoint
                last_seen = now - DateTime.fromtimestamp(inf.last_handshake, TimeZone.utc)
                if last_seen < TimeDelta(minutes=10):
                    new_row['last_seen'] = "just now"  # values below 7 minutes are somewhat random
                else:
                    new_row['last_seen'] = uif.human_duration(last_seen, positive_only=True)
            else:
                new_row['last_ip'] = '-'
                new_row['last_seen'] = "never"
            rows.append(new_row)
        return rows

    cards = build_rows()
    cards_container = ui.column().classes('w-full gap-4')

    def kv_row(key, value):
        with ui.row().classes('w-full justify-between no-wrap'):
            ui.label(key).classes('text-caption text-grey-7')
            ui.label(value).classes('text-caption')

    def render_cards():
        css = 'w-full max-w-xl mx-auto'
        cards_container.clear()
        with cards_container:
            with ui.column().classes(css):
                idelem['base_name'] = uif.input(
                    label='Name',
                    font_size='18px',
                    icon='router',
                    max_length=70,
                )
                idelem['new_base'] = uif.button(text="New base router", align='center')
                idelem['new_base'].on_click(callback=on_add_item)
            for card in cards:
                with ui.card().classes(css):
                    with ui.column().classes('min-w-0 gap-0'):
                        ui.label(card['name']).classes('text-subtitle1 font-medium truncate')
                        ui.label(card['subd']).classes('text-caption text-grey-7 truncate')
                    # doesn't really improve anything: ui.separator().classes('my-2')
                    with ui.column().classes('w-full gap-1'):
                        if kind == db.AccountKind.ADMIN:
                            kv_row('ID', card['id'])
                            kv_row('LOGIN', card['login'])
                        kv_row('LAST IP', card['last_ip'])
                        kv_row('LAST SEEN', card['last_seen'])
                    with ui.row().classes('w-full no-wrap items-center justify-end gap-2'):
                        ui.button(
                            'Set up',
                            on_click=lambda c=card: ui.navigate.to(f'/setup/{c['slug']}'),
                        ).props('flat')
                        ui.button(
                            'Delete',
                            on_click=lambda e, c=card: on_delete_card(c, e.sender),
                        ).props('flat color=negative')

    async def on_delete_card(card, button):
        dialog = ui.dialog()
        confirmed = {'value': False}
        with dialog, ui.card():
            if card['last_seen'] != "never":
                warning = f"WARNING: The base router '{card['name']}'"
                if card['last_seen'] != "just now":
                    warning += f" was active {card['last_seen']} ago."
                else:
                    warning += f" is currently active."
                warning += " Are you sure you want to remove the ability to manage this router?"
            else:
                warning = f"Delete the base router '{card['name']}'?"
            ui.label(warning).classes('text-subtitle2')
            ui.label("This cannot be undone.").classes('text-caption text-grey-7')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button(
                    'Delete',
                    on_click=lambda: (confirmed.__setitem__('value', True), dialog.close()),
                ).props('flat color=negative')
        dialog.open()
        await dialog
        if not confirmed['value']:
            return
        button.disable()
        try:
            db.delete_device(card['id'])
            nonlocal cards
            cards = build_rows()
            render_cards()
        finally:
            button.enable()

    async def on_add_item():
        base_name = idelem['base_name'].value[:70] or f'Base {lk.generate_login_key(3)}'
        device_slug = db.new_device(account_id=aid, is_base=True, name=base_name)
        ui.navigate.to(f'/setup/{device_slug}')

    render_cards()


###
### page: /setup
###


class ElemRegistry(dict):  # lazy evaluation of idelem objects
    def __init__(self):
        super().__init__()
        self._wait_once = {}  # key -> [callback(elem)]
        self._watch_always = {}  # key -> [callback(elem)]

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        for cb in self._watch_always.get(key, ()):
            cb(value)
        waiters = self._wait_once.pop(key, None)
        if waiters:
            for cb in waiters:
                cb(value)

    def setdefault(self, key, default=None):
        if key in self:
            return self[key]
        self[key] = default
        return default

    def update(self, *args, **kwargs):
        other = dict(*args, **kwargs)
        for k, v in other.items():
            self[k] = v

    def when_available(self, key, cb):
        """Run once: now if present, otherwise when the element is first registered."""
        if key in self:
            cb(self[key])
        else:
            self._wait_once.setdefault(key, []).append(cb)

    def on_register(self, key, cb):
        """Run every time the key is registered (useful if steps are rebuilt)."""
        self._watch_always.setdefault(key, []).append(cb)
        if key in self:
            cb(self[key])


@ui.page('/setup/{device_slug}')
def setup(client: Client, device_slug: str):
    try:
        lsid, aid, kind = auth.require_login(client)
    except db.CredentialsError:
        return
    if not (device_id := db.get_device_by_slug(device_slug, aid)):
        md_path = os.path.join(ui_path, 'device-not-found.md')
        with open(md_path, 'r', encoding='utf-8') as f:
            md = f.read().replace('{device_slug}', device_slug)
            sections = uif.parse_markdown_sections(md)
        ui.run_javascript(f"document.title = '{sections[0]}'")
        uif.render_header(is_logged_in=True)
        idelem = uif.render_content(sections)
        return
    stage_state = 'adopt'

    def stage_chip(title: str, stage: str, n: int) -> None:
        active = stage_state == stage
        props = 'unelevated' if active else 'outline'
        classes = 'stage-pill px-3 py-2 rounded-full whitespace-nowrap min-w-0 ' + (
            ' stage-pill--active' if active else ' stage-pill--inactive'
        )
        with ui.button(on_click=lambda s=stage: set_stage(s)).props(props).classes(classes):
            ui.label(str(n)).classes('stage-pill__num')
            ui.label(title).classes('stage-pill__label')

    @ui.refreshable
    def render_tab_buttons():
        with ui.row().classes('w-full items-center justify-center gap-2 py-3 flex-nowrap'):
            stage_chip('Connect', 'adopt', 1)
            ui.element('div').classes('stage-connector')
            stage_chip('Enable access', 'enable', 2)
            ui.element('div').classes('stage-connector')
            stage_chip('Add device', 'add', 3)

    def set_stage(stage: str) -> None:
        nonlocal stage_state
        stage_state = stage
        render_tab_buttons.refresh()
        panels.set_value(stage)

    def render_stepper(stage: str):
        yaml_path = os.path.join(ui_path, f'setup-{stage}.yaml')
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        idelem = ElemRegistry()  # map each element ID to its actual object
        uif.render_markdown_with_ctags(data['pre_md'], idelem, None)  # text above list
        all_steps = data['steps']
        path_map = dict()  # 'path' from here forward means the sequence of choices the user made
        id_map = dict()
        for s in all_steps:
            if path_map.setdefault(s['path'], s) != s:
                raise Berror(f"B19389 duplicate path '{s['path']}' in {yaml_path}")
            if id_map.setdefault(s['id'], s) != s:
                raise Berror(f"B33451 duplicate id '{s['id']}' in {yaml_path}")
        for s in all_steps:  # use paths to create a tree structure
            if len(s['path']) == 0:
                continue  # root has no parent
            parent_path = re.sub(r'/[^/]*$', '', s['path'])
            parent = path_map[parent_path]
            if 'children' not in parent:
                parent['children'] = list()
            parent['children'].append(s)
            s['parent'] = parent
        # 'header-nav' makes steps clickable (works, but possibly fragile after stepper.remove())
        with ui.stepper().props('vertical header-nav').classes('w-full max-w-5xl') as stepper:

            def on_click_other(child, from_step_el):

                def delete_steps_after(step_el) -> None:
                    children = list(stepper.default_slot.children)
                    try:
                        i = children.index(step_el)
                    except ValueError:
                        return
                    for ch in children[i + 1 :]:
                        stepper.remove(ch)

                delete_steps_after(from_step_el)
                build_steps_down(child)
                stepper.next()

            def build_steps_down(s):
                while True:
                    nextc = None
                    with stepper:
                        with ui.step(s['title']) as step:
                            uif.render_markdown_with_ctags(s['md'], idelem, None)
                            with ui.stepper_navigation():
                                if 'parent' in s:
                                    ui.button('Back', on_click=stepper.previous).props('outline')
                                for c in s.get('children', list()):
                                    label = c['path'].rsplit('/', 1)[-1]
                                    if label == 'Next':
                                        nextc = c
                                        ui.button(label, on_click=stepper.next)
                                    else:
                                        l = lambda child=c, step=step: on_click_other(child, step)
                                        ui.button(label, on_click=l)
                        if nextc:
                            s = nextc
                        else:
                            break

            build_steps_down(path_map[''])
        return idelem

    def render_add_devices():
        ui.label('Add a device name below:').classes('text-lg font-medium')
        name = ui.input(label='Device name').classes('w-full max-w-xl')

        def add_device() -> None:
            v = (name.value or '').strip()
            if not v:
                return
            with device_list:
                with ui.row().classes('items-center gap-3'):
                    ui.icon('devices')
                    ui.label(v).classes('text-base')
            name.value = ''

        ui.button('Add device', on_click=add_device)
        ui.separator().classes('w-full max-w-5xl my-6')
        device_list = ui.column().classes('w-full max-w-5xl gap-3')

    def add_custom_css():  # style for render_tab_buttons()
        ui.add_head_html(
            '''
                <style>
                .stage-pill {
                  max-width: 32vw;
                }
                .stage-pill .q-btn__content {
                  display: inline-flex;
                  align-items: center;
                  gap: 0.5rem;
                  min-width: 0;
                  font-size: clamp(0.85rem, 2.8vw, 1.1rem);
                  line-height: 1.2;
                }
                .stage-pill__num {
                  flex: 0 0 auto;
                  font-weight: 700;
                  opacity: 0.9;
                }
                .stage-pill__label {
                  min-width: 0;
                  overflow: hidden;
                  text-overflow: ellipsis;
                }
                .stage-connector {
                  height: 2px;
                  width: clamp(10px, 3vw, 18px);
                  opacity: 0.35;
                  background: currentColor;
                }
                </style>
            '''
        )

    md_path = os.path.join(ui_path, 'setup.md')
    with open(md_path, 'r', encoding='utf-8') as f:
        sections = uif.parse_markdown_sections(f.read())
    ui.run_javascript(f"document.title = '{sections[0]}'")
    uif.render_header(is_logged_in=True)
    idelem = uif.render_content(sections)
    add_custom_css()
    render_tab_buttons()
    with ui.tabs().classes('hidden') as tabs:
        ui.tab('adopt')
        ui.tab('enable')
        ui.tab('add')
    with ui.tab_panels(tabs, value='adopt').props(
        'animated transition-prev="slide-right" transition-next="slide-left" keep-alive'
    ).classes('w-full') as panels:
        with ui.tab_panel('adopt'):
            idelem = render_stepper('adopt')
            code_cache = {'value': None, 'done': False}

            def set_local_startup_code(el):
                if not code_cache['done']:
                    conf = db.get_conf(db.hub_peer_id(device_id))
                    code_cache['value'] = db.methodize(conf, 'linux.openwrt.gzb')
                    code_cache['done'] = True
                el.set_content(code_cache['value'])

            # using 'idelem.when_available()' below fails on 2nd 'build_steps_down()' call
            idelem.on_register('code_for_local_startup', set_local_startup_code)
        with ui.tab_panel('enable'):
            render_stepper('enable')
        with ui.tab_panel('add'):
            render_add_devices()


###
### page: /login_sessions
###


@ui.page('/login_sessions')
def login_sessions(client: Client):
    try:
        lsid, aid, kind = auth.require_login(client)
    except db.CredentialsError:
        return
    md_path = os.path.join(ui_path, 'login_sessions.md')
    with open(md_path, 'r', encoding='utf-8') as f:
        sections = uif.parse_markdown_sections(f.read())
    ui.run_javascript(f"document.title = '{sections[0]}'")
    uif.render_header(is_logged_in=True)
    idelem = uif.render_content(sections)

    columns = [
        {'name': 'id', 'sortable': True},  # columns[0]
        {'name': 'login', 'sortable': True},  # columns[1]
        {'name': 'device', 'sortable': True},
        {'name': 'ip', 'sortable': True},
        {'name': 'last_activity'},
        {'name': 'valid_for'},
        {'name': 'valid', 'classes': 'hidden', 'headerClasses': 'hidden'},
        {'name': 'current', 'classes': 'hidden', 'headerClasses': 'hidden'},
        {'name': 'action', 'align': 'center'},
    ]
    for c in columns:
        c['label'] = c['name'].replace('_', ' ').upper()
        c['field'] = c['name']
    if kind != db.AccountKind.ADMIN:
        # hide id, login
        columns[0]['classes'] = 'hidden'
        columns[0]['headerClasses'] = 'hidden'
        columns[1]['classes'] = 'hidden'
        columns[1]['headerClasses'] = 'hidden'

    def build_rows(lsid):
        rows = list()
        now = DateTime.now(TimeZone.utc)
        id_to_query = None if kind == db.AccountKind.ADMIN else aid
        for s in db.iter_get_login_session_by_account_id(aid=id_to_query):
            device = uif.human_user_agent(s.user_agent)
            last_activity = uif.human_duration(now - s.last_activity.replace(tzinfo=TimeZone.utc))
            valid = s.valid_until.replace(tzinfo=TimeZone.utc) - now
            valid_for = uif.human_duration(valid, positive_only=True)
            rows.append(
                {
                    'id': s.id,
                    'login': s.account.login,
                    'device': "Current device" if s.id == lsid else device,
                    'ip': s.ip,
                    'last_activity': last_activity,
                    'valid_for': valid_for,
                    'valid': valid.total_seconds() > 0.0,
                    'current': s.id == lsid,
                }
            )
        return rows

    rows = build_rows(lsid)
    table = ui.table(
        columns=columns,
        rows=rows,
        row_key='id',
        column_defaults={'align': 'left'},
    )
    table.add_slot(  # hide 'log out' for not-valid rows and, to avoid confustion, current device
        'body-cell-action',
        '''
            <q-td :props="props">
                <q-btn
                  v-if="props.row.valid && !props.row.current"
                  label="Log out"
                  color="red"
                  @click="() => $parent.$emit('log_out', props.row)"
                  flat
                />
            </q-td>
        ''',
    )
    table.add_slot(  # make 'device' column bold for current device
        'body-cell-device',
        '''
            <q-td :props="props">
                <span :style="props.row.current ? 'font-weight: bold' : ''">
                    {{ props.row.device }}
                </span>
            </q-td>
        ''',
    )

    def on_log_out(e):
        db.log_out(e.args['id'])
        table.rows = build_rows(lsid)
        table.update()
        if e.args['current']:  # keep this in case we decide to allow 'log out' for current device
            ui.notify("You have been logged out.", color='warning')
            ui.timer(3, lambda: ui.navigate.to('/login'), once=True)

    table.on('log_out', on_log_out)


def register_pages():
    app.add_static_files('/ui', ui_path)
    ui.add_css(os.path.join(ui_path, 'theme.css'), shared=True)
