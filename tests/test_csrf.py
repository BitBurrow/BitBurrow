from datetime import datetime, timedelta, timezone
import hashlib
import threading

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from starlette.datastructures import Headers
from starlette.websockets import WebSocket, WebSocketDisconnect

import hub.config as conf
from hub.csrf import OriginMiddleware, is_same_origin, require_same_origin


@pytest.fixture(autouse=True)
def public_origin(monkeypatch):
    monkeypatch.setattr(
        conf,
        'config',
        {
            'frontend': {'web_proto': 'https', 'domain': 'hub.example.org', 'web_port': 443},
        },
    )


@pytest.mark.parametrize(
    'headers,expected',
    [
        ({'origin': 'https://hub.example.org'}, True),
        ({'origin': 'HTTPS://HUB.EXAMPLE.ORG:443'}, True),
        ({'referer': 'https://hub.example.org/site/login?next=/home'}, True),
        ({'origin': 'https://hub.example.org', 'referer': 'https://evil.example/'}, True),
        ({}, False),
        ({'origin': ''}, False),
        ({'origin': 'null'}, False),
        ({'origin': 'null', 'referer': 'https://hub.example.org/'}, False),
        ({'origin': 'https://evil.example', 'referer': 'https://hub.example.org/'}, False),
        ({'origin': 'http://hub.example.org'}, False),
        ({'origin': 'https://hub.example.org:8443'}, False),
        ({'origin': 'https://hub.example.org.evil.example'}, False),
        ({'origin': 'https://other.hub.example.org'}, False),
        ({'origin': 'https://hub.example.org@evil.example'}, False),
        ({'origin': 'https://evil.example@hub.example.org'}, False),
        ({'origin': 'https://hub.example.org/'}, False),
        ({'origin': 'https://hub.example.org?x=1'}, False),
        ({'origin': 'https://hub.example.org#x'}, False),
        ({'origin': 'https://hub.example.org:invalid'}, False),
        ({'origin': 'https://hub.example.org:65536'}, False),
        ({'origin': 'https://[invalid'}, False),
        ({'origin': ' https://hub.example.org'}, False),
        ({'origin': 'https://hub.example.org\n'}, False),
        ({'origin': 'https://hub.example.org\\@evil.example'}, False),
        ({'origin': 'https://hub.example.org https://evil.example'}, False),
        ({'referer': '/site/login'}, False),
        ({'referer': 'https://evil.example/login'}, False),
        ({'referer': 'https://hub.example.org.evil.example/login'}, False),
        ({'referer': 'https://hub.example.org\\@evil.example/login'}, False),
    ],
)
def test_origin_validation(headers, expected):
    assert is_same_origin(Headers(headers)) is expected


@pytest.mark.parametrize('name', ['origin', 'referer'])
def test_duplicate_source_headers_are_rejected(name):
    header = name.encode()
    headers = Headers(
        raw=[
            (header, b'https://hub.example.org'),
            (header, b'https://hub.example.org'),
        ]
    )
    assert not is_same_origin(headers)


@pytest.mark.parametrize(
    'scheme,port,source',
    [
        ('http', 80, 'http://hub.example.org'),
        ('http', 80, 'http://hub.example.org:80'),
        ('http', 8080, 'http://hub.example.org:8080'),
        ('https', 8443, 'https://hub.example.org:8443'),
    ],
)
def test_public_scheme_and_port_are_read_at_request_time(scheme, port, source):
    conf.config['frontend'].update(web_proto=scheme, web_port=port)
    assert is_same_origin(Headers({'origin': source}))
    assert not is_same_origin(Headers({'origin': 'https://hub.example.org'}))


@pytest.fixture(params=['', '/nested/site'])
def browser(request):
    prefix = request.param
    effects = []
    ui_app = FastAPI()
    ui_app.add_middleware(OriginMiddleware)

    @ui_app.get('/entry')
    def entry():
        return {'ok': True}

    @ui_app.api_route('/change', methods=['POST', 'PUT', 'PATCH', 'DELETE'])
    def change():
        effects.append('changed')
        return {'ok': True}

    @ui_app.get('/creates-on-get', dependencies=[Depends(require_same_origin)])
    def creates_on_get():
        effects.append('created')
        return {'ok': True}

    @ui_app.get('/_nicegui_ws/socket.io/')
    def polling():
        return {'ok': True}

    @ui_app.websocket('/_nicegui_ws/socket.io/')
    async def websocket(ws: WebSocket):
        await ws.accept()
        await ws.send_text('connected')
        await ws.close()

    parent = FastAPI()

    @parent.post('/api/v1')
    @parent.post('/er/{subd}')
    def base_api():
        return {'base_api': True}

    parent.mount('/', ui_app)
    outer = FastAPI()
    outer.mount(prefix or '/', parent)
    # The backend address and scheme deliberately differ from the public origin.
    with TestClient(outer, base_url='http://backend.internal:8080') as client:
        yield client, prefix, effects


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE'])
def test_unsafe_requests_are_checked_before_side_effects(browser, method):
    client, prefix, effects = browser
    for headers in [{}, {'origin': 'https://evil.example'}, {'origin': 'null'}]:
        response = client.request(method, prefix + '/change', headers=headers)
        assert response.status_code == 403
        assert response.headers['cache-control'] == 'no-store'
    assert effects == []
    response = client.request(
        method,
        prefix + '/change',
        headers={
            'origin': 'https://hub.example.org',
            'host': 'backend.internal:8080',
            'x-forwarded-host': 'irrelevant.example',
            'x-forwarded-proto': 'http',
        },
    )
    assert response.status_code == 200
    assert effects == ['changed']


def test_forged_target_headers_do_not_authorize_an_origin(browser):
    client, prefix, effects = browser
    response = client.post(
        prefix + '/change',
        headers={
            'origin': 'https://evil.example',
            'host': 'evil.example',
            'x-forwarded-host': 'evil.example',
            'x-forwarded-proto': 'https',
        },
    )
    assert response.status_code == 403
    assert effects == []


def test_navigation_and_get_side_effects(browser):
    client, prefix, effects = browser
    assert client.get(prefix + '/entry').status_code == 200
    assert client.get(prefix + '/creates-on-get').status_code == 403
    assert (
        client.get(
            prefix + '/creates-on-get',
            headers={
                'referer': 'https://evil.example/',
            },
        ).status_code
        == 403
    )
    assert effects == []
    assert (
        client.get(
            prefix + '/creates-on-get',
            headers={
                'referer': 'https://hub.example.org' + prefix + '/entry',
            },
        ).status_code
        == 200
    )
    assert effects == ['created']


def test_polling_requires_origin_or_same_origin_referer(browser):
    client, prefix, _ = browser
    path = prefix + '/_nicegui_ws/socket.io/?EIO=4&transport=polling'
    for headers in [{}, {'origin': 'null'}, {'origin': 'https://evil.example'}]:
        assert client.get(path, headers=headers).status_code == 403
    assert (
        client.get(
            path,
            headers={
                'referer': 'https://hub.example.org' + prefix + '/entry',
            },
        ).status_code
        == 200
    )


def test_websocket_handshake_is_checked(browser):
    client, prefix, _ = browser
    path = prefix + '/_nicegui_ws/socket.io/'
    for headers in [{}, {'origin': 'null'}, {'origin': 'https://evil.example'}]:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(path, headers=headers):
                pytest.fail('untrusted websocket accepted')
        assert exc.value.code == 1008
    with client.websocket_connect(path, headers={'origin': 'https://hub.example.org'}) as ws:
        assert ws.receive_text() == 'connected'


def test_base_endpoints_are_outside_the_browser_guard(browser):
    client, prefix, _ = browser
    for path in ['/api/v1', '/er/abcd']:
        response = client.post(prefix + path, json={})
        assert response.status_code == 200
        assert response.json() == {'base_api': True}


def test_middleware_construction_does_not_read_configuration(monkeypatch):
    monkeypatch.setattr(conf, 'config', None)
    app = FastAPI()
    middleware = OriginMiddleware(app)
    assert middleware.app is app


@pytest.fixture(params=['', '/nested/site'])
def adopt_browser(request, tmp_path, monkeypatch):
    import hub.auth as auth
    import hub.db as db
    import hub.pages as pages
    from nicegui import app as nicegui_app

    db_path = tmp_path / 'adopt.db'
    engine = create_engine(f'sqlite:///{db_path}', connect_args={'check_same_thread': False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(conf, 'base_url', lambda: 'https://hub.example.org')
    monkeypatch.setattr(db.get_adopt5c_code, 'cache', {}, raising=False)
    monkeypatch.setattr(db.get_adopt5c_code, 'cache_lock', threading.Lock(), raising=False)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add_all(
            [
                db.Account(id=101, login='owner', kind=db.AccountKind.MANAGER),
                db.Account(id=102, login='other', kind=db.AccountKind.MANAGER),
            ]
        )
        session.commit()
        session.add_all(
            [
                db.Base(id=2, account_id=101, subd='aaaa'),
                db.Base(id=3, account_id=102, subd='bbbb'),
            ]
        )
        for token, account_id, hours in (
            ('owner-session', 101, 1),
            ('expired-session', 101, -1),
            ('other-session', 102, 1),
        ):
            session.add(
                db.LoginSession(
                    account_id=account_id,
                    kind=db.LoginSessionKind.COOKIE,
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    valid_until=now + timedelta(hours=hours),
                )
            )
        session.commit()
    # Exercise the registered route's own dependency without relying on global middleware.
    routes = [
        route
        for route in nicegui_app.routes
        if getattr(route, 'endpoint', None) is pages.setup_adopt5c_code
    ]
    assert len(routes) == 1
    ui_app = FastAPI()
    ui_app.router.routes.append(routes[0])
    outer = FastAPI()
    prefix = request.param
    outer.mount(prefix or '/', ui_app)
    cookie_name = auth.cookie_name('session')
    with TestClient(outer, base_url='http://backend.internal:8080') as client:
        client.cookies.set(cookie_name, 'owner-session')
        yield client, prefix, engine, db, cookie_name
    engine.dispose()


def post_adopt_code(browser, action, subd='aaaa', headers=None):
    client, prefix, _, _, _ = browser
    if headers is None:
        headers = {'origin': 'https://hub.example.org'}
    return client.post(
        f'{prefix}/setup/{subd}/adopt5c_code', params={'action': action}, headers=headers
    )


def adoption_state(browser):
    _, _, engine, db, _ = browser
    with Session(engine) as session:
        bases = {base.subd: base.ott_id for base in session.exec(select(db.Base)).all()}
        tokens = {
            row.id: (row.kind, row.token_hash)
            for row in session.exec(
                select(db.LoginSession).where(db.LoginSession.kind != db.LoginSessionKind.COOKIE)
            ).all()
        }
        return bases, tokens


def test_adopt_code_rejects_untrusted_origins(adopt_browser):
    issued = post_adopt_code(adopt_browser, 'issue')
    assert issued.status_code == 200
    before = adoption_state(adopt_browser)
    for action in ('view', 'issue', 'regenerate'):
        for headers in ({}, {'origin': 'null'}, {'origin': 'https://evil.example'}):
            response = post_adopt_code(adopt_browser, action, headers=headers)
            assert response.status_code == 403
            assert response.headers['cache-control'] == 'no-store'
            assert adoption_state(adopt_browser) == before
    assert post_adopt_code(adopt_browser, 'view').text == issued.text


def test_adopt_code_rejects_expired_session(adopt_browser):
    client, _, _, _, cookie_name = adopt_browser
    assert post_adopt_code(adopt_browser, 'issue').status_code == 200
    before = adoption_state(adopt_browser)
    client.cookies.set(cookie_name, 'expired-session')
    for action in ('view', 'issue', 'regenerate'):
        response = post_adopt_code(adopt_browser, action)
        assert response.status_code == 401
        assert adoption_state(adopt_browser) == before


def test_adopt_code_rejects_another_accounts_base(adopt_browser):
    client, _, _, _, cookie_name = adopt_browser
    client.cookies.set(cookie_name, 'other-session')
    issued = post_adopt_code(adopt_browser, 'issue', subd='bbbb')
    assert issued.status_code == 200
    before = adoption_state(adopt_browser)
    client.cookies.set(cookie_name, 'owner-session')
    for action in ('view', 'issue', 'regenerate'):
        response = post_adopt_code(adopt_browser, action, subd='bbbb')
        assert response.status_code == 404
        assert adoption_state(adopt_browser) == before
    client.cookies.set(cookie_name, 'other-session')
    assert post_adopt_code(adopt_browser, 'view', subd='bbbb').text == issued.text


def test_adopt_code_view_does_not_issue_a_token(adopt_browser):
    before = adoption_state(adopt_browser)
    response = post_adopt_code(adopt_browser, 'view')
    assert response.status_code == 200
    assert response.text == '(unable to display)'
    assert response.headers['cache-control'] == 'no-store'
    assert adoption_state(adopt_browser) == before
    assert before == ({'aaaa': None, 'bbbb': None}, {})


def test_adopt_code_issue_reuses_the_existing_token(adopt_browser):
    _, _, _, db, _ = adopt_browser
    first = post_adopt_code(adopt_browser, 'issue')
    assert first.status_code == 200
    assert first.headers['cache-control'] == 'no-store'
    assert 'https://hub.example.org' in first.text
    bases, tokens = adoption_state(adopt_browser)
    token_id = bases['aaaa']
    assert token_id is not None
    assert set(tokens) == {token_id}
    assert tokens[token_id][0] == db.LoginSessionKind.BASE_OTT
    for action in ('view', 'issue'):
        response = post_adopt_code(adopt_browser, action)
        assert response.status_code == 200
        assert response.text == first.text
        assert response.headers['cache-control'] == 'no-store'
        assert adoption_state(adopt_browser) == (bases, tokens)


def test_adopt_code_regenerate_revokes_the_previous_token(adopt_browser):
    _, _, _, db, _ = adopt_browser
    first = post_adopt_code(adopt_browser, 'issue')
    assert first.status_code == 200
    old_bases, old_tokens = adoption_state(adopt_browser)
    old_id = old_bases['aaaa']
    assert old_id is not None
    assert old_tokens[old_id][0] == db.LoginSessionKind.BASE_OTT
    regenerated = post_adopt_code(adopt_browser, 'regenerate')
    assert regenerated.status_code == 200
    assert regenerated.headers['cache-control'] == 'no-store'
    assert regenerated.text != first.text
    bases, tokens = adoption_state(adopt_browser)
    new_id = bases['aaaa']
    assert new_id is not None and new_id != old_id
    assert set(tokens) == {old_id, new_id}
    assert tokens[old_id] == (db.LoginSessionKind.TRASH, old_tokens[old_id][1])
    assert tokens[new_id][0] == db.LoginSessionKind.BASE_OTT
    assert tokens[new_id][1] != old_tokens[old_id][1]
    assert post_adopt_code(adopt_browser, 'view').text == regenerated.text
    assert adoption_state(adopt_browser) == (bases, tokens)


@pytest.mark.parametrize('action', ['', 'invalid', 'VIEW'])
def test_adopt_code_rejects_invalid_action(adopt_browser, action):
    assert post_adopt_code(adopt_browser, 'issue').status_code == 200
    before = adoption_state(adopt_browser)
    response = post_adopt_code(adopt_browser, action)
    assert response.status_code == 400
    assert adoption_state(adopt_browser) == before
