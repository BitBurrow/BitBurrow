import socketio

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


sio_server = socketio.AsyncServer(
    async_mode='asgi',
    cors_allow_origins=[],  # allow ONLY FastAPI
)
sio_app = socketio.ASGIApp(
    socketio_server=sio_server,
    socketio_path='messages',
)


@sio_server.event
async def connect(sid, environ, auth):
    print(f"Socket.IO connection from {sid}")
    return True  # return False to reject the connection


@sio_server.event
async def disconnect(sid):
    print(f"Socket.IO disconnection from {sid}")


@sio_server.event
async def disconnect_request(sid):
    print(f"Socket.IO 'disconnect_request' from {sid}")


@sio_server.event
async def test_message(sid, data):
    print(f"Socket.IO 'test_message' from {sid}: {data}")
    await sio_server.emit(event='test_print', data='1235')


@sio_server.on('*')
async def catch_all(event, sid, data):
    print(f"Socket.IO unknown event '{event}' from {sid}: {data}")
