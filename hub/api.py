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


jsonrpc_route = '/api/v1'
adopt5l_route = "/5l/{subd}"  # download adopt5p.sh
adopt5s_route = "/5s/{subd}"  # download bbbased.lua
log_err_route = "/er/{subd}"  # errors from base router
hub_path = os.path.dirname(os.path.abspath(__file__))
requests_by_ip = dict()
rate_lock = threading.Lock()
router = APIRouter()


def check_rate_limit(ip_address: str) -> None:
    now = DateTime.now(TimeZone.utc)
    window_start = now - TimeDelta(minutes=1)
    stale_before = now - TimeDelta(minutes=2)
    with rate_lock:
        stale_ips = list()
        for stored_ip, timestamps in requests_by_ip.items():
            fresh_timestamps = [dt for dt in timestamps if dt > stale_before]
            if fresh_timestamps:
                requests_by_ip[stored_ip] = fresh_timestamps
            else:
                stale_ips.append(stored_ip)
        for stored_ip in stale_ips:
            del requests_by_ip[stored_ip]
        recent_requests = requests_by_ip.get(ip_address, list())
        recent_requests = [dt for dt in recent_requests if dt > window_start]
        if len(recent_requests) >= 2:
            raise HTTPException(status_code=429, detail='Too many requests')
        recent_requests.append(now)
        requests_by_ip[ip_address] = recent_requests


def get_file(
    request: Request, subd: str, filename: str, expand, info_msg: str
) -> PlainTextResponse:
    ip_address = request.client.host if request.client else '0.0.0.0'
    check_rate_limit(ip_address)
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
        '{download_url}',
        conf.base_url() + adopt5s_route.format(subd=subd),
    ).replace(
        '{log_err_route}',
        conf.base_url() + log_err_route.format(subd=subd),
    )
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
    expand_braces = lambda s: s.replace(
        '{api_url}',
        conf.base_url() + jsonrpc_route,
    ).replace(
        '{subd}',
        subd,
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
