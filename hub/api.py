import asyncio
from fastapi import (
    APIRouter,
    responses,
    Request,
    status,
    WebSocket,
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


@router.websocket('/test_ws_client/{client_id}')
async def websocket_testahwibb(websocket: WebSocket, client_id: str):
    logger.info(f"wss:/test_ahwibbviclipytr/{client_id} connected")
    await websocket.accept()
    try:
        while True:
            request = await websocket.receive_text()
            if request == "ping":
                if random.randrange(0, 2) == 0:
                    logger.info(f"wss: ping-pong")
                    await websocket.send_text("pong")
                else:
                    logger.info(f"wss: ping-pong1")
                    await websocket.send_text("pong1")
            else:
                logger.error(f"wss: unrecognized request {request}")
    except Exception as e:
        logger.error(f"wss: error a {e}")  # usually: 1005
    try:
        await websocket.close()
    except Exception as e:
        logger.error(f"wss: error b {e}")  # usually: Unexpected ASGI message 'websocket.close' ...
