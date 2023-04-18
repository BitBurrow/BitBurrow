import logging
import socketio
import hub.db as db

# Socket.IO server links:
#     * docs: https://socket.io/docs/
#     * Python docs: https://python-socketio.readthedocs.io/en/latest/server.html
#     * excellent tutorial: https://www.youtube.com/watch?v=tHQvTOcx_Ys
#     * FastAPI integration:
#         * currently using this approach → https://www.youtube.com/watch?v=_dlfzWzYeEM
#         * https://stackoverflow.com/questions/70274482
#         * https://github.com/BimaAdi/fastapi-with-python-socketio-example
#         * https://stackoverflow.com/questions/70429135
#         * https://www.reddit.com/r/FastAPI/comments/neds9c/integrate_socketio_with_fastapi/
#         * https://pypi.org/project/fastapi-socketio/


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)

sio_server = socketio.AsyncServer(
    async_mode='asgi',
    cors_allow_origins=[],  # allow ONLY FastAPI
    ping_timeout=600,
    ping_interval=20,  # send ping every x seconds
)
sio_app = socketio.ASGIApp(
    socketio_server=sio_server,
    socketio_path='messages',
)


@sio_server.event
async def connect(sid, environ):
    async with sio_server.session(sid) as session:
        session['login_key'] = ''
        session['kind'] = db.Account_kind.NONE
    logger.info(f"Socket.IO connection from {sid}")
    return True


@sio_server.event
async def disconnect(sid):
    logger.info(f"Socket.IO disconnection from {sid}")


@sio_server.event
async def disconnect_request(sid):
    logger.info(f"Socket.IO 'disconnect_request' from {sid}")


## TO DECIDE: should client sign_in() via Socket.IO or just send login_key on every message (stateless)
# @sio_server.event
# async def sign_in(sid, data):
#     """Return dict {'kind': string, 'err': string}"""
#     login_key = data.get('login_key')
#     try:
#         account = db.Account.validate_login_key(login_key)
#         kind = account.kind
#         error_message = ""
#         logger.info(f"Sign-in succeeded for {login_key} (kind {kind.value})")
#     except Exception as err:
#         login_key = ''
#         kind = db.Account_kind.NONE
#         error_message = err
#         logger.info(f"Sign-in failed for {login_key}: err")
#     async with sio_server.session(sid) as session:
#         session['login_key'] = login_key
#         session['kind'] = kind
#     return {'kind': str(kind), 'err': error_message}


@sio_server.event
async def test_message(sid, data):
    logger.info(f"Socket.IO 'test_message' from {sid}: {data}")
    await sio_server.emit(event='test_print', data='1235')


@sio_server.event
async def transmutate(sid, data):
    logger.info(f"Socket.IO 'test_transmutate', login_key {data}")
    cid = await sio_server.call(event='tcp_open', data=('192.168.8.1', 22), to=sid)
    logger.info(f"got cid {cid}")
    for n in range(0, 1000):
        sequence = await sio_server.call(
            event='tcp_send',
            data=(cid, f'sending data {n}'),
            to=sid,
        )
        print(f"sequence {sequence}")
    confirm = await sio_server.call(event='tcp_close', data=cid, to=sid)
    return True


@sio_server.on('*')
async def catch_all(event, sid, data):
    logger.info(f"Socket.IO unknown event '{event}' from {sid}: {data}")
