from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse
import os
import logging
import threading
import hub.db as db
import hub.config as conf

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


jsonrpc_route = '/api/v1'
adopt5p_route = '/5p'
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


@router.get(adopt5p_route, response_class=PlainTextResponse)
def get_adopt5p_script(request: Request) -> PlainTextResponse:
    ip_address = request.client.host if request.client else '0.0.0.0'
    check_rate_limit(ip_address)
    adopt5p_path = os.path.join(hub_path, 'adopt5p.sh')
    try:
        with open(adopt5p_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError as exc:
        logger.error(f"B98850 cannot open: {adopt5p_path}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
    api_url = conf.base_url() + jsonrpc_route
    substituted = content.replace('{api_url}', api_url)
    return PlainTextResponse(
        content=substituted,
        media_type='text/plain; charset=utf-8',
        headers={'Cache-Control': 'no-store'},
    )
