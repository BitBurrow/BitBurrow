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
                idelem['base_name'] = uif.input(placeholder='Name', font_size='18px', icon='router')
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
        base_name = idelem['base_name'].value or f'Base {lk.generate_login_key(3)}'
        device_slug = db.new_device(account_id=aid, is_base=True, name=base_name)
        ui.navigate.to(f'/manage/{device_slug}')

    render_cards()


###
### page: /manage
###


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
    md_path = os.path.join(ui_path, 'manage.md')
    with open(md_path, 'r', encoding='utf-8') as f:
        sections = uif.parse_markdown_sections(f.read())
    ui.run_javascript(f"document.title = '{sections[0]}'")
    uif.render_header(is_logged_in=True)
    idelem = uif.render_content(sections)
    conf = db.get_conf(db.hub_peer_id(device_id))
    code = db.methodize(conf, 'linux.openwrt.gzb')
    idelem['code_for_local_startup'].set_content(code)


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
