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
sys.path.append(os.path.join(base_dir, "libs", "persistent_websocket"))
import python.persistent_websocket as persistent_websocket


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


###
### Base transmutation (configure router to be a VPN base)
###


def transmute_task(task_id):
    if not hasattr(transmute_init, 'tasks'):  # on first call
        transmute_init()
    return transmute_init.id_index.get(task_id, None)


def transmute_next_task(task_id):
    if not hasattr(transmute_init, 'tasks'):  # on first call
        transmute_init()
    return transmute_init.next_id.get(task_id, 0)  # returns 0 for nonexistant or last task


def transmute_init():
    f_path = f'{os.path.dirname(__file__)}/base_setup_tasks.yaml'
    with open(f_path, "r") as f:
        transmute_init.tasks = yaml.safe_load(f)
    transmute_init.id_index = dict()  # index to look up task by id
    transmute_init.next_id = dict()  # to compute next id
    priorId = 0
    for task in transmute_init.tasks:
        id = task['id']
        assert isinstance(id, int)
        assert id > priorId
        assert 'method' in task
        assert 'params' in task
        transmute_init.id_index[id] = task
        transmute_init.next_id[priorId] = id
        priorId = id


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
        service = await asyncio.start_server(self.handle_client, self._addr, self._port)
        logger.debug(f'listening on {self._addr} port {self._port}')
        async with service:
            await service.serve_forever()
