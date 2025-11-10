import os
import re
import time
import string
import secrets
import hashlib
import collections
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
from typing import Dict, Deque, List, Optional
import urllib.parse
from nicegui import ui, app, Client
from fastapi import Request, Response, Body, APIRouter
from fastapi.responses import PlainTextResponse, RedirectResponse
import sqlalchemy.exc
from sqlmodel import SQLModel, Field, Session, select, create_engine, Column, String, Relationship
import argon2
import uvicorn.middleware.proxy_headers
import fastapi.middleware.trustedhost
import starlette.middleware.httpsredirect

###
### config
###


RATE_LIMIT_REQUESTS = 100
RATE_LIMIT_WINDOW_SECONDS = 60

LOGIN_ATTEMPTS = 10
LOGIN_WINDOW = 60  # seconds

USERNAME_PATTERN = re.compile(r'^[a-zA-Z_.-]{4,12}$')
ALLOWED_CHARS_PATTERN = re.compile(r'^[a-zA-Z_.-]*$')

DB_PATH = os.environ.get('DB_PATH', 'sqlite:///./users.db')

COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

# CSRF
CSRF_HEADER = 'x-csrf-token'

# Session timeouts
IDLE_TIMEOUT = TimeDelta(hours=8)
ABSOLUTE_LIFETIME = TimeDelta(days=7)

# Pending confirm TTL and capacity
PENDING_TTL = 300  # seconds
PENDING_CAP = 200

# 1 in prod (HTTPS), 0 in local dev (HTTP)
SECURE_COOKIES = bool(int(os.environ.get('SECURE_COOKIES', '0')))
SESSION_COOKIE_NAME = '__Host-session' if SECURE_COOKIES else 'session'
CSRF_COOKIE_NAME = '__Host-csrf' if SECURE_COOKIES else 'csrf'

# Environment
PROD = os.getenv('ENV', 'dev') == 'prod'

# Rate limiter minimal eviction cap
_BUCKET_CAP = int(os.getenv('BUCKET_CAP', '10000'))

# Allowed hosts, e.g. 'example.com,www.example.com,localhost'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]').split(',')

# Trusted IPs, e.g. '127.0.0.1,::1,172.17.0.1,10.8.0.0/16'
TRUSTED_IPS = {h.strip() for h in os.getenv('TRUSTED_IPS', '127.0.0.1,::1').split(',') if h.strip()}


###
### FastAPI app
###


# Enforce HTTPS redirects in prod (expects TLS termination at proxy)
if PROD and SECURE_COOKIES:
    app.add_middleware(starlette.middleware.httpsredirect.HTTPSRedirectMiddleware)

app.add_middleware(
    fastapi.middleware.trustedhost.TrustedHostMiddleware,
    allowed_hosts=list(ALLOWED_HOSTS),
)
app.add_middleware(
    uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware,
    trusted_hosts=list(TRUSTED_IPS),
)


###
### database
###


class EpochUTCDateTime(sqlalchemy.types.TypeDecorator):
    impl = sqlalchemy.types.Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=TimeZone.utc)
        return int(value.timestamp())

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return DateTime.fromtimestamp(value, tz=TimeZone.utc)


class Account(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(sa_column=Column(String(64), unique=True, index=True))
    normalized_username: str = Field(sa_column=Column(String(64), unique=True, index=True))
    key_hash: str
    login_sessions: List['LoginSession'] = Relationship(back_populates='account')


class LoginSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key='account.id', index=True)
    token_hash: str = Field(index=True, unique=True)
    user_agent: str
    ip: str
    created_at: DateTime = Field(
        sa_column=Column(EpochUTCDateTime()),
        default_factory=lambda: DateTime.now(TimeZone.utc),
    )
    last_activity: DateTime = Field(
        sa_column=Column(EpochUTCDateTime()),
        default_factory=lambda: DateTime.now(TimeZone.utc),
    )
    valid: bool = Field(default=True)
    account: Optional[Account] = Relationship(back_populates="login_sessions")


engine = create_engine(DB_PATH, echo=False)
SQLModel.metadata.create_all(engine)


def db_session() -> Session:
    return Session(engine)


###
### security
###


hasher = argon2.PasswordHasher()


def ip_from_request(request: Request) -> str:
    # With ProxyHeadersMiddleware, request.client.host is the real client IP (from trusted proxy)
    return request.client.host if request.client else '0.0.0.0'


def generate_password(length: int = 5) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def hash_token(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def user_agent_brief(ua: str) -> str:
    if not ua:
        return 'Unknown device'
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
    browser = 'Browser'
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
### CSRF middleware
###


# NiceGUI hits several internal endpoints (polling/events/socket.io).
# Exempt them fully, or the UI won't bootstrap and pages can appear blank.
NICEGUI_CSRF_EXEMPT_PREFIXES = (
    '/socket.io',  # Socket.IO transport
    '/_nicegui',  # NiceGUI JSON/event endpoints
    '/_event',  # some versions use this path for events
    '/_static',  # static assets served by NiceGUI
)


def hostport(netloc: str) -> str:
    # normalize 'example.com:80' vs 'example.com'
    return netloc.lower()


def same_origin(request: Request) -> bool:
    # Accept same-origin if Origin/Referer host:port equals our host:port after proxy headers
    req_scheme = request.url.scheme
    req_host = hostport(request.url.netloc)
    origin = request.headers.get('origin')
    referer = request.headers.get('referer')

    def ok(h: str) -> bool:
        if not h:
            return False
        parts = urllib.parse.urlsplit(h)
        if parts.scheme != req_scheme:
            return False
        return hostport(parts.netloc) == req_host

    return ok(origin) or ok(referer)


@app.middleware('http')
async def csrf_middleware(request: Request, call_next):
    path = request.url.path
    if any(path.startswith(p) for p in NICEGUI_CSRF_EXEMPT_PREFIXES):
        return await call_next(request)
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        c = request.cookies.get(CSRF_COOKIE_NAME)
        h = request.headers.get(CSRF_HEADER)
        ok = False
        if c and h and secrets.compare_digest(c, h):
            ok = True
        elif same_origin(request):
            ok = True
        if not ok:
            return PlainTextResponse('CSRF check failed', status_code=403)
    response = await call_next(request)
    if not request.cookies.get(CSRF_COOKIE_NAME):
        response.set_cookie(
            CSRF_COOKIE_NAME,
            secrets.token_urlsafe(32),
            httponly=False,
            secure=SECURE_COOKIES,
            samesite='lax',
            path='/',
            max_age=COOKIE_MAX_AGE,
        )
    return response


###
### security headers middleware
###


@app.middleware('http')
async def headers_middleware(request: Request, call_next):
    resp = await call_next(request)

    # NiceGUI (Vue/Quasar) may use inline scripts and eval in dev; allow websocket connects.
    # If you later harden for prod, try removing 'unsafe-eval' after verifying the app still loads.
    if PROD:
        resp.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        resp.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
        resp.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    else:
        resp.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none'",
        )
    if SECURE_COOKIES:
        resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    return resp


###
### rate limiting
###


_rate_limit_buckets: Dict[str, Deque[DateTime]] = collections.defaultdict(collections.deque)
_login_buckets: Dict[tuple, Deque[DateTime]] = collections.defaultdict(collections.deque)


def bucket_ok(bucket: Deque[DateTime], limit: int, window_s: int) -> bool:
    now = DateTime.now(TimeZone.utc)
    while bucket and (now - bucket[0]).total_seconds() > window_s:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


def get_bucket(store: Dict, key):
    if key not in store and len(store) >= _BUCKET_CAP:
        try:
            store.pop(next(iter(store)))
        except StopIteration:
            pass
    return store.setdefault(key, collections.deque())


@app.middleware('http')
async def rate_limit_and_session_middleware(request: Request, call_next):
    ip = ip_from_request(request)
    if not bucket_ok(
        get_bucket(_rate_limit_buckets, ip), RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS
    ):
        return PlainTextResponse('Too Many Requests', status_code=429)
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        with db_session() as s:
            h = hash_token(token)
            ls = s.exec(select(LoginSession).where(LoginSession.token_hash == h)).first()
            if ls and ls.valid:
                now = DateTime.now(TimeZone.utc)
                if (now - ls.created_at) > ABSOLUTE_LIFETIME or (
                    now - ls.last_activity
                ) > IDLE_TIMEOUT:
                    ls.valid = False
                    s.add(ls)
                    s.commit()
                    resp = (
                        RedirectResponse('/login', status_code=303)
                        if 'text/html' in (request.headers.get('accept', ''))
                        else PlainTextResponse('Session expired', status_code=401)
                    )
                    resp.delete_cookie(SESSION_COOKIE_NAME, path='/')
                    return resp
    response = await call_next(request)
    try:
        if token and response.status_code < 400:
            with db_session() as s:
                ls = s.exec(
                    select(LoginSession).where(LoginSession.token_hash == hash_token(token))
                ).first()
                if ls and ls.valid:
                    ls.last_activity = DateTime.now(TimeZone.utc)
                    s.add(ls)
                    s.commit()
    except Exception:
        pass
    return response


###
### authentication helpers and endpoints
###


def set_session_cookie(response: Response, token: str):
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite='strict',
        secure=SECURE_COOKIES,
        path='/',
    )


def create_login_session_record(account: Account, request: Request) -> str:
    with db_session() as s:
        token = secrets.token_urlsafe(32)
        ls = LoginSession(
            account_id=account.id,
            token_hash=hash_token(token),
            user_agent=request.headers.get('User-Agent', ''),
            ip=ip_from_request(request),
            created_at=DateTime.now(TimeZone.utc),
            last_activity=DateTime.now(TimeZone.utc),
            valid=True,
        )
        s.add(ls)
        s.commit()
        return token


def get_current_session_and_account(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None, None
    with db_session() as s:
        row = s.exec(
            select(LoginSession, Account)
            .join(Account)
            .where(LoginSession.token_hash == hash_token(token))
        ).first()
        if not row:
            return None, None
        session_obj, account_obj = row
        if not session_obj.valid:
            return None, None
        return session_obj, account_obj


def require_auth_or_redirect(client: Client) -> Optional[Account]:
    _, acc = get_current_session_and_account(client.request)
    if not acc:
        ui.notify('Please log in first.', color='warning')
        ui.navigate.to('/login')
        return None
    return acc


def sanitize_redirect(url: str) -> str:
    return url if url.startswith('/') and '://' not in url else '/home'


router = APIRouter()


@router.post('/_set_session')
def post_set_session(token: str = Body(..., embed=True), redirect: str = Body('/home', embed=True)):
    response = RedirectResponse(sanitize_redirect(redirect), status_code=303)
    set_session_cookie(response, token)
    return response


@router.post('/_logout')
def logout(request: Request, redirect: str = Body('/login', embed=True)):
    safe_redirect = sanitize_redirect(redirect)
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        with db_session() as s:
            h = hash_token(token)
            ls = s.exec(select(LoginSession).where(LoginSession.token_hash == h)).first()
            if ls:
                ls.valid = False
                s.add(ls)
                s.commit()
    resp = RedirectResponse(safe_redirect, status_code=303)
    resp.delete_cookie(SESSION_COOKIE_NAME, path='/')
    return resp


app.include_router(router)

###
### ephemeral state for confirm page
###


# token -> (timestamp, {'username': ..., 'password': ...})
pending_confirm: 'collections.OrderedDict[str, tuple[float, Dict[str, str]]]' = (
    collections.OrderedDict()
)


def pending_put(nonce: str, data: Dict[str, str]):
    now = time.time()
    pending_confirm[nonce] = (now, data)
    # purge by size
    while len(pending_confirm) > PENDING_CAP:
        pending_confirm.popitem(last=False)
    # purge by TTL
    # remove oldest while expired
    while pending_confirm:
        ts, _ = next(iter(pending_confirm.values()))
        if now - ts <= PENDING_TTL:
            break
        pending_confirm.popitem(last=False)


def pending_take(nonce: str) -> Optional[Dict[str, str]]:
    item = pending_confirm.pop(nonce, None)
    if not item:
        return None
    ts, data = item
    return data if (time.time() - ts) <= PENDING_TTL else None


###
### pages
###


def js_fetch_post(path: str, payload: dict) -> str:
    return f'''
(async () => {{
  function getCookie(name) {{
    const m = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/([.$?*|{{}}()\\[\\]\\\\\\/\\+^])/g, '\\\\$1') + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : null;
  }}

  async function ensureCsrf() {{
    if (!getCookie('{CSRF_COOKIE_NAME}')) {{
      await fetch('/', {{ credentials: 'same-origin' }}); // prime CSRF cookie
    }}
  }}

  async function postOnce() {{
    const csrf = getCookie('{CSRF_COOKIE_NAME}') || '';
    const res = await fetch('{path}', {{
      method: 'POST',
      headers: {{
        'Content-Type': 'application/json',
        '{CSRF_HEADER}': csrf
      }},
      credentials: 'same-origin',
      body: JSON.stringify({payload})
    }});
    return res;
  }}

  await ensureCsrf();
  let r = await postOnce();
  if (r.status === 403) {{
    await ensureCsrf();
    r = await postOnce(); // retry once after priming
  }}
  if (r.redirected) {{
    window.location.href = r.url;
  }} else {{
    // Optional: surface errors
    const t = await r.text();
    console.log('POST {path} ->', r.status, t);
  }}
}})();
'''


@ui.page('/')
def welcome_page():
    with ui.column().style('max-width: 420px; margin: 80px auto; gap: 16px;'):
        ui.label('Welcome!').style('font-size: 2rem; font-weight: 600;')
        ui.label('Please create a new account or log in to continue.')
        with ui.row().style('gap: 12px;'):
            ui.button('Create account', on_click=lambda: ui.navigate.to('/create'))
            ui.button('Log in', on_click=lambda: ui.navigate.to('/login'))


@ui.page('/create')
def create_account_page():
    error_duplicate = ui.label().style('color: red;')
    info_invalid_chars = ui.label('Use only letters, underscores, dashes, and periods.').style(
        'color: #d33; display: none;'
    )
    length_hint = ui.label('Username must be 4–12 characters.').style('color: #666;')

    next_button = ui.button('Next')
    next_button.disable()

    def validate_live(value: str):
        show_invalid = not ALLOWED_CHARS_PATTERN.fullmatch(value or '')
        info_invalid_chars.style(
            replace='color: #d33; display: block;' if show_invalid else 'display: none;'
        )
        ok = bool(value) and USERNAME_PATTERN.fullmatch(value or '') is not None
        if ok:
            next_button.enable()
        else:
            next_button.disable()

    username_input = ui.input(
        label='Username',
        placeholder='letters, _, -, .',
        on_change=lambda e: validate_live(e.value),
    ).style('width: 100%;')

    async def proceed():
        error_duplicate.text = ''
        username = (username_input.value or '').strip()
        if not USERNAME_PATTERN.fullmatch(username):
            ui.notify('Please fix the username.', color='negative')
            return
        with db_session() as s:
            existing = s.exec(
                select(Account).where(Account.normalized_username == username.lower())
            ).first()
            if existing:
                error_duplicate.text = 'That username is already taken. Please choose another.'
                return
        pwd = generate_password(5)  # intentionally short per demo
        nonce = secrets.token_urlsafe(16)
        pending_put(nonce, {'username': username, 'password': pwd})
        ui.navigate.to(f'/confirm?token={nonce}')

    next_button.on('click', proceed)

    with ui.row().style('margin-top: 8px;'):
        ui.button('Back', on_click=lambda: ui.navigate.to('/'))

    ui.separator()
    info_invalid_chars
    length_hint
    error_duplicate


@ui.page('/confirm')
def confirm_password_page(client: Client):
    token = client.request.query_params.get('token')
    data = pending_take(token or '')
    if not token or not data:
        ui.notify('No pending account creation found.', color='warning')
        ui.navigate.to('/create')
        return
    username = data['username']
    password = data['password']
    with ui.column().style('max-width: 520px; margin: 60px auto; gap: 14px;'):
        ui.label('Confirm password').style('font-size: 1.6rem; font-weight: 600;')
        ui.label(f'Username: {username}')
        with ui.row():
            ui.label('Your password (shown only once):').style('font-weight: 600;')
            ui.label(password).style(
                'font-family: monospace; padding: 2px 6px; border: 1px solid #ccc; border-radius: 4px;'
            )
        acknowledged = ui.checkbox('I have stored this password in a safe place.')

        async def finalize():
            next_button.disable()  # prevent double submit
            try:
                # Create (or load) account safely
                with db_session() as s:
                    account = s.exec(
                        select(Account).where(Account.normalized_username == username.lower())
                    ).first()
                    if not account:
                        account = Account(
                            username=username,
                            normalized_username=username.lower(),
                            key_hash=hasher.hash(password),
                        )
                        s.add(account)
                        try:
                            s.commit()
                            s.refresh(account)
                        except sqlalchemy.exc.IntegrityError:
                            s.rollback()
                            account = s.exec(
                                select(Account).where(
                                    Account.normalized_username == username.lower()
                                )
                            ).first()
                            if not account:
                                ui.notify(
                                    'Could not create account. Please try again.', color='negative'
                                )
                                return
                # Create session and set cookie via CSRF-safe POST
                login_token = create_login_session_record(account, client.request)
                ui.run_javascript(
                    js_fetch_post('/_set_session', {'token': login_token, 'redirect': '/home'})
                )
            finally:
                # Don't re-enable; navigation will occur. If it doesn’t, user can refresh.
                pass

        next_button = ui.button('Next', on_click=finalize)
        next_button.disable()

        def sync_next_state():
            next_button.enable() if acknowledged.value else next_button.disable()

        acknowledged.on('update:model-value', lambda _: sync_next_state())
        acknowledged.on('click', lambda _: sync_next_state())
        ui.timer(0.05, sync_next_state, once=True)

        def cancel_and_discard():
            ui.navigate.to('/create')

        with ui.row().style('margin-top: 8px;'):
            ui.button('Cancel', on_click=cancel_and_discard)


@ui.page('/login')
def login_page(client: Client):
    with ui.column().style('max-width: 420px; margin: 80px auto; gap: 12px;'):
        ui.label('Log in').style('font-size: 1.8rem; font-weight: 600;')
        username = ui.input('Username')
        password = ui.input('Password', password=True, password_toggle_button=True)
        error = ui.label().style('color: red;')

        async def do_login():
            error.text = ''
            u = (username.value or '').strip()
            p = password.value or ''
            ip = ip_from_request(client.request)

            # login-specific rate limit
            if not bucket_ok(
                get_bucket(_login_buckets, (ip, u.lower())), LOGIN_ATTEMPTS, LOGIN_WINDOW
            ):
                error.text = 'Too many attempts. Please try again later.'
                return
            with db_session() as s:
                account = s.exec(
                    select(Account).where(Account.normalized_username == u.lower())
                ).first()
                if not account:
                    error.text = 'Invalid username or password.'
                    return
                try:
                    hasher.verify(account.key_hash, p)
                    # optional rehash if parameters have changed
                    if hasher.check_needs_rehash(account.key_hash):
                        account.key_hash = hasher.hash(p)
                        s.add(account)
                        s.commit()
                except Exception:
                    error.text = 'Invalid username or password.'
                    return
            login_token = create_login_session_record(account, client.request)
            ui.run_javascript(
                js_fetch_post('/_set_session', {'token': login_token, 'redirect': '/home'})
            )

        ui.button('Log in', on_click=do_login)
        ui.button('Back', on_click=lambda: ui.navigate.to('/'))
        ui.separator()
        error


@ui.page('/home')
def home_page(client: Client):
    account = require_auth_or_redirect(client)
    if not account:
        return
    with db_session() as s:
        these = s.exec(select(LoginSession).where(LoginSession.account_id == account.id)).all()
    current_token = client.request.cookies.get(SESSION_COOKIE_NAME, '')
    current_token_hash = hash_token(current_token) if current_token else ''
    ui.label('You are on the home page').style(
        'font-size: 1.6rem; font-weight: 600; margin-bottom: 8px;'
    )
    ui.label(f'Logged in as: {account.username}').style('color: #555; margin-bottom: 16px;')
    columns = [
        {'name': 'id', 'label': 'ID', 'field': 'id'},
        {'name': 'device', 'label': 'Device', 'field': 'device'},
        {'name': 'ip', 'label': 'IP', 'field': 'ip'},
        {'name': 'last', 'label': 'Last activity', 'field': 'last'},
        {'name': 'status', 'label': 'Status', 'field': 'status'},
        {'name': 'current', 'label': 'Current', 'field': 'current'},
    ]
    rows = list()
    for sess in these:
        rows.append(
            {
                'id': sess.id,
                'device': user_agent_brief(sess.user_agent),
                'ip': sess.ip,
                'last': (
                    sess.last_activity.astimezone()
                    if sess.last_activity.tzinfo
                    else sess.last_activity
                ).strftime('%Y-%m-%d %H:%M:%S'),
                'status': 'valid' if sess.valid else 'invalid',
                'current': 'Yes' if sess.token_hash == current_token_hash else '',
            }
        )
    table = ui.table(columns=columns, rows=rows, row_key='id', selection='multiple')
    with ui.row().style('gap: 8px; margin-top: 10px;'):

        def select_all():
            table._props['selected'] = [r['id'] for r in rows]  # NiceGUI hacky selection set
            table.update()

        async def invalidate_selected():
            selected_ids = table.selected
            if not selected_ids:
                ui.notify('No devices selected.', color='warning')
                return
            current_invalidated = False
            with db_session() as s:
                for sid in selected_ids:
                    sess = s.get(LoginSession, sid)
                    if not sess:
                        continue
                    if sess.token_hash == current_token_hash:
                        current_invalidated = True
                    sess.valid = False
                    s.add(sess)
                s.commit()
            ui.notify('Selected devices invalidated.', color='positive')
            if current_invalidated:
                ui.run_javascript(js_fetch_post('/_logout', {'redirect': '/login'}))
            else:
                ui.navigate.reload()

        ui.button('Select all', on_click=select_all)
        ui.button('Invalidate selected', on_click=invalidate_selected)


###
### run
###


# Remove permissive proxy env; rely on ProxyHeadersMiddleware allowlist above
os.environ.pop('FORWARDED_ALLOW_IPS', None)
os.environ.pop('PROXY_HEADERS', None)
