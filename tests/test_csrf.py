import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
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
