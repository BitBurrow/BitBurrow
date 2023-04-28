import asyncio
import queue
from fastapi import (
    APIRouter,
    BackgroundTasks,
    responses,
    Request,
    status,
    WebSocket,
    WebSocketDisconnect,
)
import random
from sqlmodel import Session, select
from typing import List
import logging
import hub.db as db
import hub.transmutation as transmutation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")

###
### web API
###

#                                              read          create      update†       delete
# -------------------------------------------- GET --------- POST ------ PATCH ------- DELETE ------
# ⍉ /v1/managers/🗝                            view self     --          update self   delete self
# ⍉ /v1/managers/🗝/servers                    list servers  new server  --            --
# ⍉ /v1/managers/🗝/servers/18                 view server   --          update server delete server
# ⍉ /v1/managers/🗝/servers/18/clients         list clients  new client  --            --
# ⍉ /v1/managers/🗝/servers/18/clients/4       view client   --          update client delete client
# ⍉ /v1/managers/🗝/servers/18/users           list users    new user    --            --
# ⍉ /v1/managers/🗝/servers/18/v1/users/🗝     view user     --          update user   delete user
#   /v1/coupons/🧩/managers                    --            new mngr    --            --
# ⍉ /v1/admins/🔑/managers                     list mngrs    --          --            --
# ⍉ /v1/admins/🔑/managers/🗝                  view mngr     --          update mngr   delete mngr
# #️⃣ /v1/admins/🔑/coupons                     list coupons  new coupon  --            --
# ⍉ /v1/admins/🔑/accounts/🗝                  view coupon   --          update coupon delete coupon
# idempotent                                   ✅            —           ✅            ✅
# 200 OK                                       ✅            —           ✅            —
# 201 created                                  —             —           —             —
# 204 no content                               —             —           —             ✅
# ⍉ not yet implemented
# #️⃣ CLI only (may implement in app later)
# 🔑 admin login key
# 🗝 manager (or admin) login key
# 🧩 coupon code
# †  cleint should send only modified fields
# ‡  new coupon or manager
# §  delete coupon, manager, or user
# https://medium.com/hashmapinc/rest-good-practices-for-api-design-881439796dc9


@router.post('/coupons/{coupon}/managers')
async def create_manager(request: Request, coupon: str):
    account = db.Account.validate_login_key(coupon, allowed_kinds=db.coupon)
    login_key = db.Account.new(db.Account_kind.MANAGER)
    return responses.JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={'login_key': login_key},
    )
    # do not store login_key!


@router.get('/managers/{login_key}/servers', response_model=List[db.Server])
async def list_servers(request: Request, login_key: str):
    account = db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
    with Session(db.engine) as session:
        statement = select(db.Server).where(db.Server.account_id == account.id)
        return list(session.exec(statement))


@router.post('/managers/{login_key}/servers', status_code=status.HTTP_201_CREATED)
async def new_server(login_key: str):
    account = db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
    server_id = db.Server.new(account.id)
    return server_id


@router.websocket('/managers/{login_key}/servers/{server_id}/setup')
async def websocket_setup(websocket: WebSocket, login_key: str, server_id: int):
    account = db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
    await websocket.accept()
    runTasks = transmutation.ServerSetup(websocket)
    try:
        await runTasks.transmute_steps()
    except asyncio.exceptions.CancelledError:
        logger.info(f"B15058 transmute canceled")
    try:
        await websocket.close()
    except Exception as e:
        logger.error(f"B38263 WebSocket error: {e}")  # e.g. websocket already closed


@router.websocket('/managers/{login_key}/servers/{server_id}/proxy')
async def websocket_proxy(websocket: WebSocket, login_key: str, server_id: int):
    account = db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
    await websocket.accept()
    tcp_websocket = transmutation.TcpWebSocket(
        tcp_port=30915, tcp_address='127.0.0.1', ws=websocket
    )
    await tcp_websocket.start()
    try:
        await websocket.close()
    except Exception as e:
        logger.error(f"B38264 WebSocket error: {e}")  # e.g. websocket already closed


class PersistentWebsocket:
    # important: mirror changes in corresponding Dart code--search "bMjZmLdFv"
    _ping_id = '!ping_id_R3PHK!'
    _pong_id = '!pong_id_R3PHK!'
    _ws = None
    _output_queue = queue.Queue()

    async def connected(self, ws):
        self._ws = ws
        self._send_queued()

    def _send_after_id_check(self, data):
        assert self._ws != None
        if data != self._pong_id:
            # 'pong_id' because we are the server
            self._ws.send_text(data)
        else:
            # split into 2 messages so it isn't confused with a pong
            self._ws.send_text(data[0:7])
            self._ws.send_text(data[7:])

    def _send_queued(self):
        assert self._ws != None
        while True:
            try:
                data = self._output_queue.get(block=False)
                self._send_after_id_check(data)
            except queue.Empty:
                return

    def send(self, data):
        if self.is_connected():
            self._send_after_id_check(data)
        else:
            self._output_queue.add(data)  # buffer output until WebSocket reconnects

    async def receive(self):
        while True:
            if not self.is_connected():
                await asyncio.sleep(5)
                continue
            try:
                data = await self._ws.receive_text()
                if data != self._ping_id:
                    break
                logger.info(f"wss: ping-pong")
                await self._ws.send_text(self._pong_id)
            except WebSocketDisconnect:
                self._ws = None
                logger.info(f"B44792 WebSocket disconnect")
        return data

    def is_connected(self):
        return self._ws is not None

    async def message_handler(self):
        while True:
            data = await self.receive()
            print(f"received: {data}")


messages = PersistentWebsocket()


@router.websocket('/test_ws_client/{client_id}')
async def websocket_testahwibb(websocket: WebSocket, client_id: str):
    # FIXME: new connection forces existing one closed, even with unique client_id
    logger.info(f"wss:/test_ahwibbviclipytr/{client_id} connected")
    await websocket.accept()
    await messages.connected(websocket)
    try:
        while messages.is_connected():
            await asyncio.sleep(60)
    except asyncio.exceptions.CancelledError:  # ctrl-C
        logger.info(f"B32045 WebSocket canceled")
