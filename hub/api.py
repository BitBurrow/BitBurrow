from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import Response, PlainTextResponse
import os
import logging
import re
import threading
import hub.db as db
import hub.config as conf

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


adopt5l_route = '/5l/{subd}'  # download adopt5p.sh
adopt5s_route = '/5s/{subd}'  # download bbbased.lua
log_err_route = '/er/{subd}'  # errors from base router
jsonrpc_route = '/api/v1'
hub_path = os.path.dirname(os.path.abspath(__file__))
requests_by_ip = dict()
rate_lock = threading.Lock()
router = APIRouter()


def get_file(
    request: Request, subd: str, filename: str, expand, info_msg: str
) -> PlainTextResponse:
    ip_address = request.client.host if request.client else '0.0.0.0'
    file_path = os.path.join(hub_path, filename)
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError as exc:
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
