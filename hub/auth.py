from fastapi import Request, Response, Body, APIRouter
from fastapi.responses import RedirectResponse
from nicegui import ui, app, Client
import os
import hub.db as db

SECURE_COOKIES = bool(int(os.environ.get('SECURE_COOKIES', '0')))
CSRF_COOKIE_NAME = '__Host-csrf' if SECURE_COOKIES else 'csrf'
CSRF_HEADER = 'x-csrf-token'
SESSION_COOKIE_NAME = '__Host-session' if SECURE_COOKIES else 'session'
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def require_login(client: Client, redirect: str = '/login') -> tuple[int, int, db.Account_kind]:
    """If the client is logged, return login_session.id, else None and redirect client browser."""
    token = client.request.cookies.get(SESSION_COOKIE_NAME)
    try:
        lsid, aid, kind = db.Account.get_by_token(token)
    except db.CredentialsError:
        ui.notify('Please log in first.', color='warning')
        ui.timer(3, lambda: ui.navigate.to(redirect), once=True)
        raise
    return lsid, aid, kind  # login_session.id, account.id, kind


router = APIRouter()


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


def sanitize_redirect(url: str) -> str:
    return url if url.startswith('/') and '://' not in url else '/home'


@router.post('/set_session')
def post_set_session(token: str = Body(..., embed=True), redirect: str = Body('/home', embed=True)):
    response = RedirectResponse(sanitize_redirect(redirect), status_code=303)
    set_session_cookie(response, token)
    return response


@router.post('/logout')
def logout(request: Request, redirect: str = Body('/login', embed=True)):
    safe_redirect = sanitize_redirect(redirect)
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        ls = db.LoginSession.from_token(token, log_out=True)
    resp = RedirectResponse(safe_redirect, status_code=303)
    resp.delete_cookie(SESSION_COOKIE_NAME, path='/')
    return resp


app.include_router(router)


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


def log_in(login_token):
    ui.run_javascript(js_fetch_post('/set_session', {'token': login_token, 'redirect': '/home'}))


def log_out():
    ui.run_javascript(js_fetch_post('/logout', {'redirect': '/login'}))
