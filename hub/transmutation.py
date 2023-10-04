import asyncio
from fastapi import (
    WebSocket,
    WebSocketDisconnect,
)
import json
import logging
import os
import sys
import yaml

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(base_dir, "libs", "python"))
import persistent_websocket.persistent_websocket as persistent_websocket


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


###
### Server transmutation (configure router to be a VPN server)
###


class ServerSetup:
    def __init__(self, ws: WebSocket, pws: persistent_websocket.PersistentWebsocket):
        self._ws = ws
        self._pws = pws

    async def transmute_steps(self):
        # user connects router
        f_path = f'{os.path.dirname(__file__)}/server_setup_steps.yaml'
        with open(f_path, "r") as f:
            server_setup_steps = yaml.safe_load(f)
        priorId = 0
        listening = asyncio.create_task(self.listener())
        for step in server_setup_steps:
            assert step['id'] > priorId
            priorId = step['id']
            await self.send_command_to_client(json.dumps({step['key']: step['value']}))
        await listening

    async def listener(self):
        try:
            async for m in self._pws.connected(self._ws):
                logger.debug(f"app WebSocket reply: {m.decode()}")
        except Exception as err:
            print(f"B38924 error: {err}")
            sys.exit(1)

    async def send_command_to_client(self, json_string):
        try:
            await self._pws.send(json_string)
        except Exception as e:
            logger.error(f"B38260 pws error: {e}")


class TcpWebSocket:
    # originally based on https://github.com/jimparis/unwebsockify/blob/master/unwebsockify.py
    def __init__(self, tcp_address, tcp_port, ws: WebSocket):
        self._addr = tcp_address
        self._port = tcp_port
        self._ws = ws

    async def copy(self, reader, writer):
        while True:
            data = await reader()
            if data == b'':
                break
            future = writer(data)
            if future:
                await future

    async def handle_client(self, r, w):
        peer = w.get_extra_info("peername")
        logger.info(f'TCP connection: {peer}')
        loop = asyncio.get_event_loop()

        def r_reader():
            return r.read(65536)

        try:
            tcp_to_ws = loop.create_task(self.copy(r_reader, self._ws.send_bytes))
            ws_to_tcp = loop.create_task(self.copy(self._ws.receive_bytes, w.write))
            done, pending = await asyncio.wait(
                [tcp_to_ws, ws_to_tcp], return_when=asyncio.FIRST_COMPLETED
            )
            for x in done:
                try:
                    await x
                except:
                    pass
            for x in pending:
                x.cancel()
        except Exception as e:
            print(f'{peer} exception:', e)
        w.close()
        print(f'{peer} closed')

    async def start(self):
        server = await asyncio.start_server(self.handle_client, self._addr, self._port)
        logger.debug(f'listening on {self._addr} port {self._port}')
        async with server:
            await server.serve_forever()
