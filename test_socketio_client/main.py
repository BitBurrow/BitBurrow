import asyncio
import re
import socketio
import sys

sio_client = socketio.AsyncClient()


@sio_client.event
async def connect():
    print("Socket.IO connected")


@sio_client.event
async def disconnect():
    print("Socket.IO disconnected")


@sio_client.event
async def server_is_ready():
    print(f"Socket.IO 'server_is_ready' event")


@sio_client.event
async def disconnect_approval():
    print(f"Socket.IO 'disconnect_approval' event")
    await sio_client.disconnect()


@sio_client.event
async def test_print(data):
    print(f"Socket.IO 'test_print' event: {data}")


@sio_client.event
async def tcp_open(ip, port):
    print(f"Socket.IO tcp_open ip {ip}, port {port}'")
    return 13


@sio_client.event
async def tcp_close(data):
    print(f"Socket.IO tcp_close cid {data}'")
    return True


@sio_client.event
async def tcp_send(cid, bytes):
    print(f"Socket.IO tcp_send cid {cid}, bytes {bytes}'")
    return 3


async def main(url):
    url_port = re.match(r'(.*):([0-9]+)$', url)  # split at last colon
    if url_port is None:
        hub = url
        port = '8443'
    else:
        hub = url_port.group(1)
        port = url_port.group(2)
    while(True):
        try:
            await sio_client.connect(url=f'{hub}:{port}', socketio_path='messages')
        except socketio.exceptions.ConnectionError as err:
            # alternatively, we could use: async def connect_error(err)
            print(f"Connection error: {err}")
            #sys.exit(1)
        else:
            break;
    await sio_client.emit('test_message', data=2)
    await sio_client.call('transmutate', data='EEEEEEEEEEEEEEE')
    await sio_client.emit('disconnect_request', callback=disconnect_approval)
    await sio_client.wait()  # wait for disconnect


if __name__ == '__main__':
    if len(sys.argv) <= 1:
        print("Hub URL must be given as the first argument")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
