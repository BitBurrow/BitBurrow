from datetime import datetime, timedelta, timezone
import os
import re
import secrets
import string
from collections import deque, defaultdict
from typing import Optional, Dict, Deque

from fastapi import Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from nicegui import app, Client, ui
from sqlmodel import (
    Field,
    SQLModel,
    Session,
    create_engine,
    select,
    Column,
    String,
    Relationship,
)
from sqlalchemy import func
import argon2


# ------------------------------ CONFIG ---------------------------------

PORT = 8080
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60

USERNAME_PATTERN = re.compile(r"^[a-zA-Z_.-]{4,12}$")
ALLOWED_CHARS_PATTERN = re.compile(r"^[a-zA-Z_.-]*$")  # for live validation message only

DB_PATH = os.environ.get("DB_PATH", "sqlite:///./users.db")

COOKIE_NAME = "session_token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

# ------------------------------ DATABASE ---------------------------------


class Account(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(sa_column=Column(String(64), unique=True, index=True))
    normalized_username: str = Field(sa_column=Column(String(64), unique=True, index=True))
    key_hash: str

    sessions: list["LoginSession"] = Relationship(back_populates="account")


class LoginSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    token: str = Field(index=True, unique=True)
    user_agent: str
    ip: str
    last_activity: datetime
    valid: bool = Field(default=True)

    account: Optional[Account] = Relationship(back_populates="sessions")


engine = create_engine(DB_PATH, echo=False)
SQLModel.metadata.create_all(engine)


def db_session() -> Session:
    return Session(engine)


# ------------------------------ SECURITY ---------------------------------

hasher = argon2.PasswordHasher()


def ip_from_request(request: Request) -> str:
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    # Fallback: uvicorn client host
    return request.client.host if request.client else "0.0.0.0"


def generate_password(length: int = 5) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def user_agent_brief(ua: str) -> str:
    if not ua:
        return "Unknown device"
    os_name = "Unknown OS"
    if "Windows" in ua:
        os_name = "Windows"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        os_name = "macOS"
    elif "Linux" in ua and "Android" not in ua:
        os_name = "Linux"
    elif "Android" in ua:
        os_name = "Android"
    elif "iPhone" in ua:
        os_name = "iPhone iOS"
    elif "iPad" in ua:
        os_name = "iPad iOS"

    browser = "Browser"
    if "Edg/" in ua or "Edge/" in ua:
        browser = "Edge"
    elif "Chrome/" in ua and "Chromium" not in ua:
        browser = "Chrome"
    elif "Safari/" in ua and "Chrome/" not in ua:
        browser = "Safari"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Chromium" in ua:
        browser = "Chromium"

    return f"{browser} on {os_name}"


# ------------------------------ RATE LIMITING MIDDLEWARE ---------------------------------

_rate_limit_buckets: Dict[str, Deque[datetime]] = defaultdict(deque)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = ip_from_request(request)
    now = datetime.now(timezone.utc)
    bucket = _rate_limit_buckets[ip]
    # purge old
    while bucket and (now - bucket[0]).total_seconds() > RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_REQUESTS:
        return PlainTextResponse("Too Many Requests", status_code=429)
    bucket.append(now)

    # proceed
    response: Response = await call_next(request)

    # Update last_activity for current valid session
    try:
        token = request.cookies.get(COOKIE_NAME)
        if token:
            with db_session() as s:
                ls = s.exec(select(LoginSession).where(LoginSession.token == token)).first()
                if ls and ls.valid:
                    ls.last_activity = datetime.now(timezone.utc)
                    s.add(ls)
                    s.commit()
    except Exception:
        pass

    return response


# ------------------------------ AUTH HELPERS ---------------------------------


def get_current_session_and_account(
    request: Request,
) -> tuple[Optional[LoginSession], Optional[Account]]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None, None
    with db_session() as s:
        q = s.exec(select(LoginSession, Account).join(Account).where(LoginSession.token == token))
        row = q.first()
        if not row:
            return None, None
        session_obj, account_obj = row
        if not session_obj.valid:
            return None, None
        return session_obj, account_obj


def require_auth_or_redirect(client: Client) -> Optional[Account]:
    ls, acc = get_current_session_and_account(client.request)
    if not acc:
        ui.notify("Please log in first.", color="warning")
        ui.navigate.to("/login")
        return None
    return acc


def set_session_cookie(response: Response, token: str):
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=False,  # TLS is terminated by the reverse proxy
        path="/",
    )


def create_login_session(response: Response, account: Account, request: Request):
    with db_session() as s:
        token = generate_token()
        ls = LoginSession(
            account_id=account.id,
            token=token,
            user_agent=request.headers.get("User-Agent", ""),
            ip=ip_from_request(request),
            last_activity=datetime.now(timezone.utc),
            valid=True,
        )
        s.add(ls)
        s.commit()
        set_session_cookie(response, token)


def logout_current_device(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with db_session() as s:
            ls = s.exec(select(LoginSession).where(LoginSession.token == token)).first()
            if ls:
                ls.valid = False
                s.add(ls)
                s.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


# ------------------------------ EPHEMERAL STATE FOR CONFIRM PAGE ---------------------------------

pending_confirm: Dict[str, Dict[str, str]] = {}  # client_id -> {'username': ..., 'password': ...}

# ------------------------------ PAGES ---------------------------------


@ui.page("/")
def welcome_page():
    with ui.column().style("max-width: 420px; margin: 80px auto; gap: 16px;"):
        ui.label("Welcome!").style("font-size: 2rem; font-weight: 600;")
        ui.label("Please create a new account or log in to continue.")
        with ui.row().style("gap: 12px;"):
            ui.button("Create account", on_click=lambda: ui.navigate.to("/create"))
            ui.button("Log in", on_click=lambda: ui.navigate.to("/login"))


@ui.page("/create")
def create_account_page(client: Client):
    error_duplicate = ui.label().style("color: red;")
    info_invalid_chars = ui.label("Use only letters, underscores, dashes, and periods.").style(
        "color: #d33; display: none;"
    )
    length_hint = ui.label("Username must be 4–12 characters.").style("color: #666;")

    next_button = ui.button("Next")
    next_button.disable()

    def validate_live(value: str):
        show_invalid = not ALLOWED_CHARS_PATTERN.fullmatch(value or "")
        info_invalid_chars.style(
            replace="color: #d33; display: block;" if show_invalid else "display: none;"
        )
        ok = bool(value) and USERNAME_PATTERN.fullmatch(value or "") is not None
        if ok:
            next_button.enable()
        else:
            next_button.disable()

    username_input = ui.input(
        label="Username",
        placeholder="letters, _, -, .",
        on_change=lambda e: validate_live(e.value),
    ).style("width: 100%;")

    async def proceed():
        error_duplicate.text = ""
        username = (username_input.value or "").strip()
        if not USERNAME_PATTERN.fullmatch(username):
            ui.notify("Please fix the username.", color="negative")
            return
        with db_session() as s:
            existing = s.exec(
                select(Account).where(func.lower(Account.normalized_username) == username.lower())
            ).first()
            if existing:
                error_duplicate.text = "That username is already taken. Please choose another."
                return
        # prepare confirm page state
        pwd = generate_password(5)
        pending_confirm[client.id] = {"username": username, "password": pwd}
        ui.navigate.to("/confirm")

    next_button.on("click", proceed)

    with ui.row().style("margin-top: 8px;"):
        ui.button("Back", on_click=lambda: ui.navigate.to("/"))

    # place labels at bottom
    ui.separator()
    info_invalid_chars
    length_hint
    error_duplicate


@ui.page("/confirm")
def confirm_password_page(client: Client):
    data = pending_confirm.get(client.id)
    if not data:
        ui.notify("No pending account creation found.", color="warning")
        ui.navigate.to("/create")
        return

    username = data["username"]
    password = data["password"]

    with ui.column().style("max-width: 520px; margin: 60px auto; gap: 14px;"):
        ui.label("Confirm password").style("font-size: 1.6rem; font-weight: 600;")
        ui.label(f"Username: {username}")
        with ui.row():
            ui.label("Your password (shown only once):").style("font-weight: 600;")
            ui.label(password).style(
                "font-family: monospace; padding: 2px 6px; border: 1px solid #ccc; border-radius: 4px;"
            )

        acknowledged = ui.checkbox("I have stored this password in a safe place.")
        next_button = ui.button("Next", on_click=lambda: None).disable()

        def on_ack(e):
            if e.value:
                next_button.enable()
            else:
                next_button.disable()

        acknowledged.on("change", on_ack)

        async def finalize():
            # store user and create session, then clear pending state
            with db_session() as s:
                account = Account(
                    username=username,
                    normalized_username=username.lower(),
                    key_hash=hasher.hash(password),
                )
                s.add(account)
                s.commit()
                s.refresh(account)

                response = RedirectResponse("/home", status_code=303)
                create_login_session(response, account, client.request)

                # clear pending state
                pending_confirm.pop(client.id, None)

                await client.navigate.to("/home", new_tab=False)
                # ensure cookie set (NiceGUI sends after navigate); but to be safe:
                app.add_route("/_set_cookie_redirect", lambda: response)

        next_button.on("click", finalize)

        def cancel_and_discard():
            pending_confirm.pop(client.id, None)
            ui.navigate.to("/create")

        with ui.row().style("margin-top: 8px;"):
            ui.button("Cancel", on_click=cancel_and_discard)


@ui.page("/login")
def login_page(client: Client):
    with ui.column().style("max-width: 420px; margin: 80px auto; gap: 12px;"):
        ui.label("Log in").style("font-size: 1.8rem; font-weight: 600;")
        username = ui.input("Username")
        password = ui.input("Password", password=True, password_toggle_button=True)
        error = ui.label().style("color: red;")

        async def do_login():
            error.text = ""
            u = (username.value or "").strip()
            p = password.value or ""
            with db_session() as s:
                account = s.exec(
                    select(Account).where(Account.normalized_username == u.lower())
                ).first()
                if not account:
                    error.text = "Invalid username or password."
                    return
                try:
                    hasher.verify(account.key_hash, p)
                except Exception:
                    error.text = "Invalid username or password."
                    return
                response = Response()
                create_login_session(response, account, client.request)
                # attach cookie to current response
                for h, v in response.raw_headers:
                    if h.decode().lower() == "set-cookie":
                        ui.add_head_html(f"")  # noop to keep consistent
                        # NiceGUI doesn't expose direct header set on current route; rely on backend cookie
                ui.navigate.to("/home")

        ui.button("Log in", on_click=do_login)
        ui.button("Back", on_click=lambda: ui.navigate.to("/"))
        ui.separator()
        error


@ui.page("/home")
def home_page(client: Client):
    account = require_auth_or_redirect(client)
    if not account:
        return

    # Fetch sessions for this user
    with db_session() as s:
        acc = s.exec(select(Account).where(Account.id == account.id)).first()
        sessions = s.exec(select(LoginSession).where(LoginSession.account_id == account.id)).all()

    current_token = client.request.cookies.get(COOKIE_NAME, "")

    ui.label("You are on the home page").style(
        "font-size: 1.6rem; font-weight: 600; margin-bottom: 8px;"
    )
    ui.label(f"Logged in as: {account.username}").style("color: #555; margin-bottom: 16px;")

    # Controls
    select_all_btn = ui.button("Select all")
    invalidate_btn = ui.button("Invalidate selected")

    # Table of devices
    header = ui.row().style(
        "gap: 12px; font-weight: 600; border-bottom: 1px solid #ddd; padding-bottom: 6px; margin-top: 10px;"
    )
    with header:
        ui.label("Select").style("width: 70px;")
        ui.label("Device").style("flex: 2;")
        ui.label("IP").style("flex: 1;")
        ui.label("Last activity").style("flex: 1;")
        ui.label("Status").style("flex: 0.6;")
        ui.label("Current").style("flex: 0.6;")

    # dynamic list
    rows = []
    checkbox_refs = []

    with db_session() as s:
        these = s.exec(select(LoginSession).where(LoginSession.account_id == account.id)).all()
        for sess in these:
            with ui.row().style(
                "gap: 12px; align-items: center; padding: 8px 0; border-bottom: 1px solid #f0f0f0;"
            ):
                cb = ui.checkbox(value=False)
                checkbox_refs.append((cb, sess.id))
                rows.append(cb)
                ui.label(user_agent_brief(sess.user_agent)).style("flex: 2;")
                ui.label(sess.ip).style("flex: 1;")
                ts = (
                    sess.last_activity.astimezone()
                    if sess.last_activity.tzinfo
                    else sess.last_activity
                )
                ui.label(ts.strftime("%Y-%m-%d %H:%M:%S")).style("flex: 1;")
                ui.label("valid" if sess.valid else "invalid").style(
                    f'flex: 0.6; color: {"#2a8" if sess.valid else "#d33"};'
                )
                ui.label("Yes" if sess.token == current_token else "").style(
                    "flex: 0.6; font-weight: 600;" if sess.token == current_token else "flex: 0.6;"
                )

    def select_all():
        for cb, _ in checkbox_refs:
            cb.value = True

    async def invalidate_selected():
        selected_ids = [sid for cb, sid in checkbox_refs if cb.value]
        if not selected_ids:
            ui.notify("No devices selected.", color="warning")
            return
        current_invalidated = False
        with db_session() as s:
            for sid in selected_ids:
                sess = s.get(LoginSession, sid)
                if not sess:
                    continue
                sess.valid = False
                if sess.token == current_token:
                    current_invalidated = True
                s.add(sess)
            s.commit()
        ui.notify("Selected devices invalidated.", color="positive")
        if current_invalidated:
            response = Response()
            logout_current_device(client.request, response)
            ui.notify("This device has been logged out.", color="warning")
            ui.navigate.to("/login")
        else:
            ui.navigate.reload()

    select_all_btn.on("click", select_all)
    invalidate_btn.on("click", invalidate_selected)


# ------------------------------ RUN ---------------------------------

ui.run(
    host="0.0.0.0",
    port=PORT,
    title="NiceGUI Auth Demo",
    reload=False,
)
