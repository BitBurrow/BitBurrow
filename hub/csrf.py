import urllib.parse
import fastapi
import starlette.datastructures
import starlette.responses
import starlette.types
import hub.config as conf


def is_same_origin(headers: starlette.datastructures.Headers) -> bool:
    """Validate Origin, falling back to Referer only when Origin is absent."""
    origins = headers.getlist('origin')
    sources = origins if origins else headers.getlist('referer')
    if len(sources) != 1:
        return False  # missing or duplicate source headers
    source = sources[0]
    if not source or any(ord(c) <= 32 or ord(c) == 127 or c == '\\' for c in source):
        return False  # urlsplit otherwise silently strips some control characters
    try:
        url = urllib.parse.urlsplit(source)
        if url.scheme not in ('http', 'https') or not url.hostname:
            return False  # includes opaque Origin: null
        if url.username is not None or url.password is not None or url.fragment:
            return False
        if origins and (url.path or url.query):
            return False  # origin must contain only scheme, host, and optional port
        port = url.port if url.port is not None else (443 if url.scheme == 'https' else 80)
    except ValueError:
        return False
    # use the configured public origin, never Host or X-Forwarded-* supplied by a request
    # https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#using-standard-headers-to-verify-origin
    return (url.scheme, url.hostname, port) == (
        conf.get('frontend.web_proto'),
        conf.get('frontend.domain'),
        conf.get('frontend.web_port'),
    )


def require_same_origin(request: fastapi.Request) -> None:
    """Also protect page GETs that create accounts or adoption tokens."""
    if not is_same_origin(request.headers):
        raise fastapi.HTTPException(
            403, 'Untrusted request origin', headers={'Cache-Control': 'no-store'}
        )


class OriginMiddleware:
    """Protect the browser app, including NiceGUI's WebSocket and polling transports."""

    def __init__(self, app: starlette.types.ASGIApp):
        self.app = app

    async def __call__(
        self,
        scope: starlette.types.Scope,
        receive: starlette.types.Receive,
        send: starlette.types.Send,
    ) -> None:
        if scope['type'] in ('http', 'websocket'):
            path = scope['path'].removeprefix(scope.get('root_path', ''))
            protected = (
                scope['type'] == 'websocket'
                or scope['method'] not in ('GET', 'HEAD', 'OPTIONS')
                or path == '/_nicegui_ws'
                or path.startswith('/_nicegui_ws/')
            )
            if protected and not is_same_origin(starlette.datastructures.Headers(scope=scope)):
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1008})
                else:
                    response = starlette.responses.JSONResponse(
                        {'detail': 'Untrusted request origin'},
                        status_code=403,
                        headers={'Cache-Control': 'no-store'},
                    )
                    await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
