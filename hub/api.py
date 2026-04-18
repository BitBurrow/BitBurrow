from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
from fastapi import APIRouter, Request, HTTPException, status, Body
from fastapi.responses import Response, PlainTextResponse
from pydantic import BaseModel
import fastapi_jsonrpc as jsonrpc
import os
import logging
import re
import threading
import hub.db as db
import hub.config as conf

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


adopt5l_route = '/5l/{subd}'  # download adopt5p.sh  (list of adopt stages in class Device())
adopt5s_route = '/5s/{subd}'  # download bbbased.lua
log_err_route = '/er/{subd}'  # errors from base router
jsonrpc_route = '/api/v1'
hub_path = os.path.dirname(os.path.abspath(__file__))
requests_by_ip = dict()
rate_lock = threading.Lock()
router = APIRouter()

###
### adoption endpoints
###


def get_file(
    request: Request, subd: str, filename: str, expand, info_msg: str
) -> PlainTextResponse:
    ip_address = request.client.host if request.client else '(unknown)'
    file_path = os.path.join(hub_path, filename)
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError as e:
        logger.error(f"B98850 cannot open: {file_path}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
    logger.info(info_msg.format(subd=subd, ip_address=ip_address))
    return PlainTextResponse(
        content=expand(content),
        media_type='text/plain; charset=utf-8',
        headers={'Cache-Control': 'no-store'},
    )


@router.get(adopt5l_route, response_class=PlainTextResponse)
def get_adopt5l_script(request: Request, subd: str) -> PlainTextResponse:
    # we do not verify subd; 'adopt5p.sh' does not contain any secrets
    subd = re.sub(r"[^a-zA-Z0-9]", "", subd)[:8]  # minimal security precaution
    expand_braces = lambda s: s.replace(
        '{download_url}', conf.base_url() + adopt5s_route.format(subd=subd)
    ).replace('{log_err_route}', conf.base_url() + log_err_route.format(subd=subd))
    return get_file(
        request,
        subd,
        'adopt5p.sh',
        expand_braces,
        "B28592 base {subd} completed adopt5l from {ip_address}",
    )


@router.get(adopt5s_route, response_class=PlainTextResponse)
def get_adopt5s_script(request: Request, subd: str) -> PlainTextResponse:
    # we do not verify subd; 'bbbased.lua' does not contain any secrets
    subd = re.sub(r"[^a-zA-Z0-9]", "", subd)[:8]  # minimal security precaution
    expand_braces = (
        lambda s: s.replace('{api_url}', conf.base_url() + jsonrpc_route)
        .replace('{subd}', subd)
        .replace('{ott_filename}', db.ott_filename(subd))
    )
    return get_file(
        request,
        subd,
        'bbbased.lua',
        expand_braces,
        "B76218 base {subd} completed adopt5s from {ip_address}",
    )


@router.post(log_err_route)
async def log_error(subd: str, request: Request) -> Response:
    body = await request.body()
    body_text = body.decode("utf-8", errors="replace")
    logger.error(f"base {subd} {body_text}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


###
### JSON-RPC set-up
###

jsonrpc_entrypoint = jsonrpc.Entrypoint(jsonrpc_route)


class BaseResult(BaseModel):
    subd: str
    status: str


class BaseError(jsonrpc.BaseError):
    MESSAGE = "Error"


###
### JSON-RPC method adopt6c
###


@jsonrpc_entrypoint.method(errors=[BaseError])
def adopt6c(
    request: Request,
    subd: str = Body(...),
    token: str = Body(...),
    auth_pubkey: str = Body(...),
    wg_pubkey: str = Body(...),
) -> BaseResult:
    # authenticate
    try:
        lsid, aid, kind = db.get_account_by_token(token, db.LoginSessionKind.DEVICE_OTT)
    except db.CredentialsError as e:
        logger.warning(f"B55311 base {subd} adopt6c error: {e}")
        raise BaseError(message=str(e))
    # verify subd
    try:
        device = db.get_device_by_ott_id(lsid)
    except db.Berror as e:
        logger.warning(f"B19344 cannot find device for OTT {lsid}: {e}")
        raise BaseError(message=str(e))
    if device.subd != subd:
        logger.warning(f"B88940 subd mismatch for OTT {lsid} ({device.subd} != {subd})")
        raise BaseError(message=f"B01839 invalid subd {subd}")
    # store public keys
    try:
        db.store_adopt6c_pubkeys(device.id, auth_pubkey, wg_pubkey)
    except db.Berror as e:
        logger.warning(f"B44512 base {subd} adopt6c error: {e}")
        raise BaseError(message=str(e))
    ip = request.client.host if request.client else '(unknown)'
    logger.info(f"B70924 base {subd} completed adopt6c from {ip}")
    return BaseResult(subd=subd, status='ok')
