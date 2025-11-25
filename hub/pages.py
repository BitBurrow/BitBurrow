import asyncio
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
from nicegui import app, ui, Client
import os
import hub.uif as uif
import hub.db as db
import hub.login_key as lk
import hub.auth as auth

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
    idelem = uif.render_page(sections, is_logged_in=False)
    if qparam_coupon:
        idelem['coupon_code'].set_value(qparam_coupon)

    async def on_continue():
        coupon = lk.strip_login_key(idelem['coupon_code'].value or '')
        try:
            aid = db.Account.validate_login_key(coupon, allowed_kinds=db.coupon)
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
        aid = db.Account.validate_login_key(qparam_coupon, allowed_kinds=db.coupon)
    except db.CredentialsError as e:  # send them back to /welcome
        ui.navigate.to(f'/welcome?coupon={qparam_coupon}')
    md_path = os.path.join(ui_path, 'confirm.md')
    with open(md_path, 'r', encoding='utf-8') as f:
        sections = uif.parse_markdown_sections(f.read())
    ui.run_javascript(f"document.title = '{sections[0]}'")
    idelem = uif.render_page(sections, is_logged_in=False)
    login_key = db.Account.new(
        kind=db.Account_kind.NONE,  # in DB but disabled until confirmed
        valid_for=TimeDelta(days=1),  # expire in 1 day if not confirmed
    )
    idelem['login_key'].set_value(lk.dress_login_key(login_key))

    async def on_continue():
        if not idelem['have_written_down'].value:
            ui.notify("Required input is missing")
            return
        idelem['continue'].disable()
        idelem['login_key'].set_value(lk.dress_login_key('*' * lk.login_key_len))
        await asyncio.sleep(1)  # let user see the importantce of keeping the login key secret
        aid = db.Account.update_account(  # validate the new account
            login=login_key,  # find account by 'login' portion only
            kind=db.Account_kind.MANAGER,  # it's now a full login key
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
    idelem = uif.render_page(sections, is_logged_in=False)

    async def on_continue():
        login_key = lk.strip_login_key(idelem['login_key'].value or '')
        try:
            aid = db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
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
### page: /logout
###


@ui.page('/logout')
def logout(client: Client):
    try:
        lsid, aid, kind = auth.require_login(client)
    except db.CredentialsError:
        return
    db.LoginSession.log_out(lsid)  # invalidate login session in DB
    auth.clear_login_cookie()
    ui.navigate.to('/login')


###
### page: /home
###


@ui.page('/home')
def home(client: Client):
    try:
        auth.require_login(client)
    except db.CredentialsError:
        return
    md_path = os.path.join(ui_path, 'home.md')
    with open(md_path, 'r', encoding='utf-8') as f:
        sections = uif.parse_markdown_sections(f.read())
    ui.run_javascript(f"document.title = '{sections[0]}'")
    idelem = uif.render_page(sections, is_logged_in=True)


###
### page: /login_session
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
    idelem = uif.render_page(sections, is_logged_in=True)
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
    if kind != db.Account_kind.ADMIN:
        # hide id, login
        columns[0]['classes'] = 'hidden'
        columns[0]['headerClasses'] = 'hidden'
        columns[1]['classes'] = 'hidden'
        columns[1]['headerClasses'] = 'hidden'

    def build_rows(lsid):
        rows = list()
        now = DateTime.now(TimeZone.utc)
        id_to_query = None if kind == db.Account_kind.ADMIN else aid
        for s in db.LoginSession.iter_get_by_account_id(aid=id_to_query):
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
        db.LoginSession.log_out(e.args['id'])
        table.rows = build_rows(lsid)
        table.update()
        if e.args['current']:  # keep this in case we decide to allow 'log out' for current device
            ui.notify("You have been logged out.", color='warning')
            ui.timer(3, lambda: ui.navigate.to('/login'), once=True)

    table.on('log_out', on_log_out)


def register_pages():
    app.add_static_files('/ui', ui_path)
    ui.add_css(os.path.join(ui_path, 'theme.css'), shared=True)
