from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
from fastapi import Request, Response, Body, APIRouter
from fastapi.responses import RedirectResponse
import json
import logging
from nicegui import ui, app, Client
import hub.config as conf
import hub.db as db
import hub.csrf as csrf

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)
router = APIRouter()
app.include_router(router)  # FastAPI >= 0.137 supports routes added after inclusion
app.add_middleware(csrf.OriginMiddleware)  # only browser routes need this guard, not base API


def secure_cookies() -> bool:
    return conf.get('frontend.web_proto') == 'https'


def cookie_name(name: str) -> str:
    return f'__Host-{name}' if secure_cookies() else name


def is_logged_in(client: Client):
    """If the client is logged in, redirect client browser."""
    token = client.request.cookies.get(cookie_name('session'))
    try:
        lsid, aid, kind = db.get_account_by_token(token, db.LoginSessionKind.COOKIE)
    except db.CredentialsError:
        return False
    return True


def require_login(client: Client, redirect: str = '/login') -> tuple[int, int, db.AccountKind]:
    """If the client is logged in, return tuple about user, else redirect client browser."""
    token = client.request.cookies.get(cookie_name('session'))
    try:
        lsid, aid, kind = db.get_account_by_token(token, db.LoginSessionKind.COOKIE)
    except db.CredentialsError:
        ui.notify('Please log in first.', color='warning')
        ui.timer(3, lambda: ui.navigate.to(redirect), once=True)
        raise
    return lsid, aid, kind  # LoginSession.id, Account.id, kind


def set_session_cookie(response: Response, token: str, max_age: int = 0):
    # docs: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie#attributes
    kwargs = dict(
        key=cookie_name('session'),
        value=token,
        httponly=True,
        samesite='strict',
        secure=secure_cookies(),
        path='/',  # __Host- cookies require this path even when the app is mounted.
    )
    if max_age > 0:
        kwargs['max_age'] = max_age
    response.set_cookie(**kwargs)


allowed_redirects = {
    '/home',
    '/login',
}


def sanitize_redirect(url: str, fallback: str = '/home') -> str:
    if url in allowed_redirects:
        return url
    logger.warning(f"B03177 refusing redirect to {url}")
    return fallback


@router.post('/set_session')
def post_set_session(
    token: str = Body(..., embed=True),
    redirect: str = Body('/home', embed=True),
    max_age: int = Body(0, embed=True),
):
    response = RedirectResponse(
        conf.get('frontend.site_code') + sanitize_redirect(redirect, '/home'), status_code=303
    )
    set_session_cookie(response, token, max_age)
    return response


@router.post('/logout')
def logout(request: Request, redirect: str = Body('/login', embed=True)):
    safe_redirect = sanitize_redirect(redirect, '/login')
    token = request.cookies.get(cookie_name('session'))
    if token:
        try:
            lsid, aid, kind = db.get_account_by_token(token, db.LoginSessionKind.COOKIE)
            db.log_out(lsid)  # invalidate login session in DB
        except db.CredentialsError:
            pass  # expired or invalid token
    resp = RedirectResponse(conf.get('frontend.site_code') + safe_redirect, status_code=303)
    resp.delete_cookie(cookie_name('session'), path='/', secure=secure_cookies())
    return resp


def js_fetch_post(path: str, payload: dict) -> str:
    options = {
        'path': conf.get('frontend.site_code') + path,
        'payload': payload,
    }
    js = '''
        (async () => {
          const {path, payload} = JSON.parse(__OPTIONS__);
          try {
            const response = await fetch(path, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              credentials: 'same-origin',
              body: JSON.stringify(payload)
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            if (response.redirected) {
              window.location.href = response.url;
            } else {
              console.log('POST', path, '->', response.status);
            }
          } catch (error) {
            console.error('POST', path, 'failed:', error);
            Quasar.Notify.create({
              type: 'negative',
              message: 'Could not complete the request. Please reload the page and try again.'
            });
          }
        })();
    '''
    return js.replace('__OPTIONS__', json.dumps(json.dumps(options, allow_nan=False)))


def set_login_cookie(
    login_token, max_age: int = 0
):  # max_age of 0 means cookie removed on browser close
    ui.run_javascript(
        js_fetch_post(
            '/set_session', {'token': login_token, 'redirect': '/home', 'max_age': max_age}
        )
    )


def log_in(account_id, keep_logged_in: bool, request):
    if keep_logged_in:  # keep user logged in for 30 days
        session_ttl = TimeDelta(days=30)
        cookie_seconds = 60 * 60 * 24 * 30
    else:  # keep logged in until client browser is closed (max 24 hours)
        session_ttl = TimeDelta(days=1)
        cookie_seconds = 0
    token, lsid = db.new_login_session(account_id, session_ttl, metadata=request)
    logger.info(f"B06525 new login session {lsid} for account {account_id}")
    set_login_cookie(token, cookie_seconds)


def log_out(redirect_to: str = '/login'):
    ui.run_javascript(js_fetch_post('/logout', {'redirect': redirect_to}))
