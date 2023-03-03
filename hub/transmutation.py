import asyncio
from fastapi import (
    WebSocket,
    WebSocketDisconnect,
)
import json
import logging
import os
import yaml


logger = logging.getLogger(__name__)


###
### Server transmutation (configure router to be a VPN server)
###


class ServerSetup:
    def __init__(self, ws: WebSocket):
        self._ws = ws

    async def send_command_to_client(self, json_string):
        try:
            await self._ws.send_text(json_string)
        except Exception as e:
            logger.error(f"B38260 WebSocket error: {e}")
        try:
            return await self._ws.receive_text()
        except WebSocketDisconnect:
            logger.info(f"B38261 WebSocket disconnect")
        except Exception as e:
            logger.error(f"B38262 WebSocket error: {e}")
            # self._error_count += 1

    async def transmute_steps(self):
        # user connects router
        f_path = f'{os.path.dirname(__file__)}/server_setup_steps.yaml'
        with open(f_path, "r") as f:
            server_setup_steps = yaml.safe_load(f)
        priorId = 0
        for step in server_setup_steps:
            assert step['id'] > priorId
            priorId = step['id']
            reply = await self.send_command_to_client(json.dumps({step['key']: step['value']}))
            logger.debug(f"app WebSocket reply: {reply}")


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
