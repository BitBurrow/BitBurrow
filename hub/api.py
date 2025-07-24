import asyncio
from fastapi import (
    APIRouter,
    responses,
    Request,
    status,
    WebSocket,
    HTTPException,
)
from sqlmodel import Session, SQLModel, select
from typing import Dict, List
import json
import jsonrpc
import logging
import hub.db as db
import hub.transmutation as transmutation

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)
router = APIRouter()

###
### web API
###

#                                              read          create      update†       delete
# -------------------------------------------- GET --------- POST ------ PATCH ------- DELETE ------
# ⍉ /v1/managers/🗝                            view self     --          update self   delete self
# ⍉ /v1/managers/🗝/bases                      list bases    new base    --            --
# ⍉ /v1/managers/🗝/bases/18                   view base     --          update base   delete base
# ⍉ /v1/managers/🗝/bases/18/clients           list clients  new client  --            --
# ⍉ /v1/managers/🗝/bases/18/clients/4         view client   --          update client delete client
# ⍉ /v1/managers/🗝/bases/18/users             list users    new user    --            --
# ⍉ /v1/managers/🗝/bases/18/v1/users/🗝       view user     --          update user   delete user
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


# FIXME: to limit brute-force attacks, throttle any IP address with 4 connects in 60 seconds; https://github.com/tiangolo/fastapi/issues/448


@jsonrpc.dispatcher.add_method
def create_manager(coupon: str):
    account = db.Account.validate_login_key(coupon, allowed_kinds=db.coupon)  # verfiy validity
    return db.Account.new(db.Account_kind.MANAGER)
    # do not store login_key!


@jsonrpc.dispatcher.add_method
def list_bases(login_key: str):
    account = db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
    with Session(db.engine) as session:
        statement = select(db.Base).where(db.Base.account_id == account.id)
        return list(session.exec(statement))


@jsonrpc.dispatcher.add_method
def create_base(login_key: str, task_id: int, base_id: int):
    account = db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
    if task_id == 0:
        base_id = db.Base.new(account.id)
        task_id = transmutation.transmute_next_task(task_id)
    task = transmutation.transmute_task(task_id)
    return {
        'method': task['method'],
        'params': task['params'],
        'next_task': transmutation.transmute_next_task(task['id']),
        'base_id': base_id,
    }
