import asyncio
import socketio

sio_client = socketio.AsyncClient()

@sio_client.event
async def connect():
    print("Socket.IO connected")

@sio_client.event
async def connect_error(data):
    print(f"Socket.IO connection failed: {data}")

@sio_client.event
async def disconnect():
    print("Socket.IO disconnected")

@sio_client.event
async def test_print(data):
    print(f"Socket.IO 'test_print' event: {data}")

@sio_client.event
async def server_is_ready():
    print(f"Socket.IO 'server_is_ready' event")

@sio_client.event
async def disconnect_approval():
    print(f"Socket.IO 'disconnect_approval' event")
    await sio_client.disconnect()

async def main():
    await sio_client.connect(url='https://example.com:8443', socketio_path='messages')
    await sio_client.emit('test_message', data={'foo': 2})
    await sio_client.emit('aadzujasiewreww', data=3333333)
    await sio_client.emit('disconnect_request', callback=disconnect_approval)
    await sio_client.wait()  # wait for disconnect

asyncio.run(main())

