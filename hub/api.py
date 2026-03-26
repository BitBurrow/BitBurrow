from fastapi import (
    APIRouter,
    responses,
    Response,
    Request,
    status,
    WebSocket,
    HTTPException,
)
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
from sqlmodel import Session, SQLModel, select
import logging
import hub.db as db
import hub.transmutation as transmutation
import hub.config as conf

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


jsonrpc_path = '/api/v1'
bootstrap0_path = '/bootstrap0'


def device_bootstrap_code(account_id) -> str:
    """Return a shell script, e.g. for /etc/rc.local, to begin adopting a BitBurrow base router"""
    server_token_timedelta = TimeDelta(hours=9)  # base router must call API within 9 hours
    token = db.new_login_session(account_id, server_token_timedelta)
    return f'echo {token} >/tmp/YVB6IEH; curl {conf.base_url()}{bootstrap0_path} |sh'


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


# @router.post('/v1/coupons/{coupon}/managers')
# async def create_manager(request: Request, coupon: str):
#     account = db.Account.validate_login_key(coupon, allowed_kinds=db.coupon)
#     login_key = db.Account.new(db.Account_kind.MANAGER)
#     return responses.JSONResponse(
#         status_code=status.HTTP_201_CREATED,
#         content={'login_key': login_key},
#     )
#     # do not store login_key!


# # @router.get('/v1/managers/{login_key}/bases')
# @router.get('/v1/managers/bases')
# async def list_bases(request: Request):
#     login_key = request.cookies.get("loginkey")
#     account = db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
#     with Session(db.engine) as session:
#         statement = select(db.Base).where(db.Base.account_id == account.id)
#         results = session.exec(statement)
#         return {'bases': [c.pubkey for c in results]}


# @router.post('/v1/managers/{login_key}/bases')
# async def new_base(request: Request, login_key: str):
#     return responses.JSONResponse(
#         status_code=status.HTTP_201_CREATED,
#         content={},
#     )
#     account = Account.validate_login_key(login_key)
#     with Session(db.engine) as session:
#         statement = select(Client).where(Client.account_id == account.id)
#         results = session.exec(statement)
#         return [c.pubkey for c in results]


# @router.get('/v1/login/{login_key}/')
# async def set_login_cookie(request: Request, response: Response, login_key: str):
#     db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
#     response.status_code = 200
#     response.set_cookie(
#         key='loginkey',
#         value=login_key,
#         httponly=True,
#         secure=True,
#         samesite='Strict',
#         max_age=315360000,  # ten years
#     )
#     return {}


# @router.get("/v1/logout")
# async def logout():
#     redirect = responses.RedirectResponse(url='/login')
#     redirect.delete_cookie('loginkey')
#     return redirect
