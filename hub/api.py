import asyncio
from fastapi import (
    APIRouter,
    responses,
    Request,
    status,
    WebSocket,
)
from sqlmodel import Session, select
from typing import Dict, List
import logging
import os
import sys
import hub.db as db
import hub.transmutation as transmutation

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(base_dir, "libs", "python"))
import persistent_websocket.persistent_websocket as persistent_websocket

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)
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
    persistent_websocket.logger.setLevel(logging.DEBUG)
    messages = persistent_websocket.PersistentWebsocket(server_id)
    runTasks = transmutation.ServerSetup(websocket, messages)
    try:
        await runTasks.transmute_steps()
    except asyncio.exceptions.CancelledError:
        logger.info(f"B15058 transmute canceled")


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


messages: Dict[str, persistent_websocket.PersistentWebsocket] = dict()  # one entry for each client


@router.websocket('/pw/{client_id}')
async def websocket_testahwibb(websocket: WebSocket, client_id: str):
    if client_id not in messages:
        # FIXME: mitigate DOS attack via opening a bunch of unique connections
        messages[client_id] = persistent_websocket.PersistentWebsocket(client_id)
        # (TESTING) messages[client_id].chaos = 50  # 5% chance of closing WebSocket on each send or receive
        persistent_websocket.logger.setLevel(logging.DEBUG)
    try:
        await websocket.accept()
        async for m in messages[client_id].connected(websocket):
            print(f"------------------------------------------ {client_id} incoming: {m.decode()}")
    except persistent_websocket.PWUnrecoverableError:
        del messages[client_id]  # data is no longer usable
