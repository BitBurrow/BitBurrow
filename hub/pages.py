import asyncio
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
import logging
from nicegui import app, ui, Client
import os
import hub.uif as uif
import hub.db as db
import hub.login_key as lk
import hub.auth as auth
import hub.config as conf

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
        coupon = lk.strip_login_key(idelem['coupon_code'].value or '')
        try:
            aid = db.validate_login_key(coupon, allowed_kinds=db.coupon)
        except db.CredentialsError as e:
            err_message = str(e).replace(db.lkocc_string, "coupon code")
            ui.notify(err_message)
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
    idelem['login_key'].set_value(lk.dress_login_key(login_key))

    async def on_continue():
        if not idelem['have_written_down'].value:
            ui.notify("Required input is missing")
            return
        idelem['continue'].disable()
        idelem['login_key'].set_value(lk.dress_login_key('*' * lk.login_key_len))
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
        login_key = lk.strip_login_key(idelem['login_key'].value or '')
        try:
            aid = db.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
        except db.CredentialsError as e:
            err_message = str(e).replace(db.lkocc_string, "login key")
            ui.notify(err_message)
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
                    placeholder='Name',
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
                            'Manage',
                            on_click=lambda c=card: ui.navigate.to(f'/manage/{c['slug']}'),
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
        ui.navigate.to(f'/manage/{device_slug}')

    render_cards()


###
### page: /manage
###


class State:
    stage: str = 'register'  # register | enable | add
    register_i: int = 0  # 0..13
    enable_i: int = 0  # 0..4
    devices: list[str] = []


@ui.page('/manage/{device_slug}')
def manage(client: Client, device_slug: str):
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
    text_for_enable_tab = [
        (
            "Initium amet, consectetur esse cillum dolore",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Proin varius, arcu in facilisis luctus, erat nisl vulputate purus, sed commodo mi ipsum sed nulla.",
        ),
        (
            "Praeparatio",
            "Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium doloremque laudantium, totam rem aperiam, eaque ipsa quae ab illo inventore veritatis.",
        ),
        (
            "Consilium placerat, nisi at aliquam",
            "Nemo enim ipsam voluptatem quia voluptas sit aspernatur aut odit aut fugit, sed quia consequuntur magni dolores eos qui ratione voluptatem sequi nesciunt, neque porro quisquam.",
        ),
        (
            "Cura sed arcu sed eros suscipit",
            "Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit laboriosam, nisi ut aliquid ex ea commodi consequatur, quis autem vel eum iure reprehenderit qui in ea voluptate velit esse.",
        ),
        (
            "Forma",
            "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur, excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.",
        ),
        (
            "Mutatio elit libero, a pharetra augue",
            "Integer vitae justo non odio lacinia tincidunt. Pellentesque habitant morbi tristique senectus et netus et malesuada fames ac turpis egestas. Curabitur auctor, nunc vitae porta tristique, nisl nisl dictum sem, sit amet.",
        ),
        (
            "Verificatio perspiciatis unde omnis iste natus",
            "Morbi non nunc id lorem tincidunt tristique. Aenean placerat, nisi at aliquam hendrerit, lacus arcu viverra mauris, in dictum lectus tortor at odio. Quisque volutpat, justo in commodo vulputate, nisi.",
        ),
        (
            "Acceptio ad minima",
            "Aliquam erat volutpat. Etiam sed arcu sed eros suscipit luctus. Maecenas nec elit at nibh porta posuere. Vivamus mollis, nisi a blandit pellentesque, dolor magna dapibus ligula, at.",
        ),
        (
            "Regulae posuere consectetur",
            "Fusce dapibus, tellus ac cursus commodo, tortor mauris condimentum nibh, ut fermentum massa justo sit amet risus. Cras mattis consectetur purus sit amet fermentum, donec ullamcorper.",
        ),
        (
            "Nexus etiam sed arcu sed",
            "Praesent commodo cursus magna, vel scelerisque nisl consectetur et. Sed posuere consectetur est at lobortis. Aenean eu leo quam. Pellentesque ornare sem lacinia quam venenatis vestibulum.",
        ),
        (
            "Confirmatio",
            "Nulla vitae elit libero, a pharetra augue. Vestibulum id ligula porta felis euismod semper. Maecenas faucibus mollis interdum. Donec sed odio dui. Cras justo odio, dapibus ac facilisis.",
        ),
        (
            "Ratio vitae elit libero, a pharetra augue",
            "Curabitur blandit tempus porttitor. Etiam porta sem malesuada magna mollis euismod. Integer posuere erat a ante venenatis dapibus posuere velit aliquet. Donec ullamcorper nulla non metus auctor.",
        ),
        (
            "Finis prope",
            "Aenean lacinia bibendum nulla sed consectetur. Vivamus sagittis lacus vel augue laoreet rutrum faucibus dolor auctor. Vestibulum id ligula porta felis euismod semper. Donec id elit non mi porta gravida.",
        ),
        (
            "Completum",
            "Donec sed odio dui. Cras mattis consectetur purus sit amet fermentum. Nulla vitae elit libero, a pharetra augue. Etiam porta sem malesuada magna mollis euismod. Curabitur blandit tempus porttitor.",
        ),
    ]
    state = State()

    def clamp(v: int, lo: int, hi: int) -> int:
        return lo if v < lo else hi if v > hi else v

    def restore_by_next(stepper: ui.stepper, idx: int) -> None:
        for _ in range(max(0, idx)):
            stepper.next()

    def stage_chip(title: str, stage: str, n: int) -> None:
        active = state.stage == stage
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
            stage_chip('Register', 'register', 1)
            ui.element('div').classes('stage-connector')
            stage_chip('Enable access', 'enable', 2)
            ui.element('div').classes('stage-connector')
            stage_chip('Add device', 'add', 3)

    def set_stage(stage: str) -> None:
        state.stage = stage
        render_tab_buttons.refresh()
        panels.set_value(stage)

    def render_register():
        md_path = os.path.join(ui_path, 'manage.md')
        with open(md_path, 'r', encoding='utf-8') as f:
            sections = uif.parse_markdown_sections(f.read())
        ui.run_javascript(f"document.title = '{sections[0]}'")
        idelem = uif.render_content(sections)
        conf = db.get_conf(db.hub_peer_id(device_id))
        code = db.methodize(conf, 'linux.openwrt.gzb')
        idelem['code_for_local_startup'].set_content(code)

    def render_enable():
        with ui.stepper().props('vertical').classes('w-full max-w-5xl') as stepper:

            def go_next() -> None:
                state.enable_i = clamp(state.enable_i + 1, 0, len(text_for_enable_tab) - 1)
                stepper.next()

            def go_prev() -> None:
                state.enable_i = clamp(state.enable_i - 1, 0, len(text_for_enable_tab) - 1)
                stepper.previous()

            def back_to_register_end() -> None:
                state.register_i = 0  # FIXME: should be the last step on that tab
                set_stage('register')

            def done() -> None:
                state.enable_i = len(text_for_enable_tab) - 1
                set_stage('add')

            for i, (title, text) in enumerate(text_for_enable_tab):
                with ui.step(title):
                    ui.label(text)
                    with ui.stepper_navigation():
                        if i == 0:
                            ui.button('Back', on_click=back_to_register_end).props('outline')
                            ui.button('Next', on_click=go_next)
                        elif i == len(text_for_enable_tab) - 1:
                            ui.button('Back', on_click=go_prev).props('outline')
                            ui.button('Done', on_click=done)
                        else:
                            ui.button('Back', on_click=go_prev).props('outline')
                            ui.button('Next', on_click=go_next)
            ui.timer(0.01, once=True, callback=lambda: restore_by_next(stepper, state.enable_i))
        ui.keyboard(
            on_key=lambda e: (
                go_next()
                if (e.action.keydown and e.key in ('Enter', 'ArrowRight'))
                else (
                    go_prev()
                    if (e.action.keydown and e.key in ('Backspace', 'ArrowLeft'))
                    else None
                )
            )
        )

    def render_add_devices():
        ui.label('Add a device name below:').classes('text-lg font-medium')
        name = ui.input(label='Device name').classes('w-full max-w-xl')

        def add_device() -> None:
            v = (name.value or '').strip()
            if not v:
                return
            state.devices.append(v)
            name.value = ''

        ui.button('Add device', on_click=add_device)
        ui.separator().classes('w-full max-w-5xl my-6')
        if not state.devices:
            ui.label('No devices yet.').classes('text-base opacity-70')
        else:
            with ui.column().classes('w-full max-w-5xl gap-3'):
                for d in state.devices:
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('devices')
                        ui.label(d).classes('text-base')

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

    uif.render_header(is_logged_in=True)
    add_custom_css()
    render_tab_buttons()
    with ui.tabs().classes('hidden') as tabs:
        ui.tab('register')
        ui.tab('enable')
        ui.tab('add')
    with ui.tab_panels(tabs, value='register').props(
        'animated transition-prev="slide-right" transition-next="slide-left" keep-alive'
    ).classes('w-full') as panels:
        with ui.tab_panel('register'):
            render_register()
        with ui.tab_panel('enable'):
            render_enable()
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
