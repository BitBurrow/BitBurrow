import asyncio
import base64
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
from fastapi import APIRouter, Request, HTTPException, status, Body
from fastapi.responses import Response, PlainTextResponse
from pydantic import BaseModel
import fastapi_jsonrpc as jsonrpc
import hashlib
import json
import os
import logging
import re
from sqlmodel import Field
import time
from typing import Any
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric import rsa
import hub.db as db
import hub.config as conf
import hub.uif as uif
import hub.util as util

Berror = db.Berror
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)
active_long_polls = 0
active_long_polls_lock = asyncio.Lock()

adopt5l_route = '/5l/{subd}'  # download adopt5p.sh  (list of adopt stages in class Device())
adopt5s_route = '/5s/{subd}'  # download bbbased.lua
log_err_route = '/er/{subd}'  # errors from base router
jsonrpc_route = '/api/v1'
router = APIRouter()

###
### adoption endpoints--file downloads (no authentication)
###


def sanitize_subd(subd):
    """Return a safer version of subd, but still unverified"""
    return re.sub(r'[^a-zA-Z0-9]', '', subd)[:8]


def get_file(
    request: Request, subd: str, filename: str, expand, info_msg: str
) -> PlainTextResponse:
    ip_address = request.client.host if request.client else '(unknown)'
    file_path = os.path.join(util.project_root_path, filename)
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
    subd = sanitize_subd(subd)  # unverified; 'adopt5p.sh' does not contain any secrets
    expand_braces = lambda s: s.replace(
        '{download_url}', conf.base_url() + adopt5s_route.format(subd=subd)
    ).replace('{log_err_route}', conf.base_url() + log_err_route.format(subd=subd))
    return get_file(
        request,
        subd,
        'hub/adopt5p.sh',
        expand_braces,
        "B28592 base {subd} completed adopt5l from {ip_address}",
    )


@router.get(adopt5s_route, response_class=PlainTextResponse)
def get_adopt5s_download(request: Request, subd: str) -> PlainTextResponse:
    subd = sanitize_subd(subd)  # unverified; 'bbbased.lua' does not contain any secrets
    ip_address = request.client.host if request.client else '(unknown)'
    try:
        version = db.get_adopt5s_version(subd)
    except Berror as e:
        logger.error(util.front_berror_code(e, subd, ip_address))
        raise HTTPException(status_code=500, detail="Internal Server Error")
    fv = version.file_version()
    content = (
        version.code.replace('{file_version}', fv)
        .replace('{api_url}', conf.base_url() + jsonrpc_route)
        .replace('{download_url}', conf.base_url() + adopt5s_route.format(subd=subd))
        .replace('{subd}', subd)
        .replace('{ott_filename}', db.ott_filename(subd))
        .replace('{log_err_route}', conf.base_url() + log_err_route.format(subd=subd))
    )
    logger.info(f"B76218 base {subd} at {ip_address} adopt5s download bbbased {fv}")
    return PlainTextResponse(
        content=content,
        media_type='text/plain; charset=utf-8',
        headers={'Cache-Control': 'no-store'},
    )


@router.post(log_err_route)
async def log_error(subd: str, request: Request) -> Response:
    subd = sanitize_subd(subd)
    body_unsafe = await request.body()
    body_text = body_unsafe[:900].decode('utf-8', errors='replace')
    disp = ''.join(c if c.isprintable() and c not in '\r\n\t' else repr(c)[1:-1] for c in body_text)
    if re.match(r'^B[0-9]{5} ', disp):  # begins with a Berror code
        message = f"{disp[0:7]}base {subd} {disp[7:]}"  # front Berror code
        timeout_markers = (
            '504 Gateway Time-out',
            '504 Gateway Timeout',
            'upstream timed out',
            'Operation timed out',
            'Connection timed out',
            'curl: (28)',
        )
        if disp[1:6] == '64445' and any(marker in message for marker in timeout_markers):
            # Berror code 64445 in bbbased.lua is a ping failure of some sort
            db.record_long_poll_timeout(subd)
    else:
        message = f"base {subd} {disp}"
    if message[0] == 'B' and message[1:6] == '20392':  # bypass Berror code dup detection
        logger.info(message)  # use logger.info() for base daemon startup message
    else:
        logger.warning(message)  # other client errors are warnings here
    return Response(status_code=status.HTTP_204_NO_CONTENT)


###
### JSON-RPC set-up
###

jsonrpc_entrypoint = jsonrpc.Entrypoint(jsonrpc_route)


class BaseResult(BaseModel):
    subd: str
    status: str
    task_id: str | None = None
    task_method: str | None = None
    task_args: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = None


class BaseError(jsonrpc.BaseError):
    MESSAGE = "Error"


###
### JSON-RPC method adopt6c--accept auth_pubkey
###


@jsonrpc_entrypoint.method(errors=[BaseError])
def adopt6c(
    request: Request,
    subd: str = Body(...),
    token: str = Body(...),
    auth_pubkey: str = Body(...),
) -> BaseResult:
    ip = request.client.host if request.client else '(unknown)'
    try:
        # authenticate
        lsid, aid, kind = db.get_account_by_token(token, db.LoginSessionKind.DEVICE_OTT)
        # verify subd
        device = db.get_device_by_ott_id(lsid)
        if device.subd != subd:
            raise Berror(f"B88940 subd mismatch for OTT {lsid} ({device.subd} != {subd})")
        # validate auth_pubkey before storing it
        try:
            public_key = serialization.load_pem_public_key(auth_pubkey.encode("utf-8"))
        except Exception as e:
            raise Berror(f"B35036 invalid auth_pubkey ({e})")
        if not isinstance(public_key, rsa.RSAPublicKey):
            raise Berror(f"B42858 auth_pubkey is {type(public_key)}; need rsa.RSAPublicKey")
        if public_key.key_size < 2048:
            raise Berror("B99756 auth_pubkey too small")
        # store public keys
        db.store_adopt6c_pubkey(device.id, auth_pubkey)
    except (Berror, db.CredentialsError) as e:
        logger.warning(f"{e} (base {subd} at {ip})")
        raise BaseError("B87908 invalid adopt6c request")  # for security, give generic API response
    else:
        logger.info(f"B70924 base {subd} completed adopt6c from {ip}")
    # keep OTT valid until client confirms handshake; search: tag_invalidate_device_ott
    return BaseResult(subd=subd, status="ok")


###
### JSON-RPC method ping--regular check-in from managed router
###

nonce_cache = dict()


def json_string_unescape(value: str) -> str:
    try:
        return json.loads('"' + value + '"')
    except Exception as e:
        raise Berror(f"B23026 invalid quoted string ({e})")


def parse_signature_input(signature_input: str) -> tuple[int, str, str, str]:
    def get_param(params: str, name: str, quoted: bool = False) -> str:
        if quoted:
            match = re.search(rf'(?:^|;){name}="((?:[^"\\]|\\.)*)"(?:;|$)', params)
        else:
            match = re.search(rf'(?:^|;){name}=(\d+)(?:;|$)', params)
        if not match:
            raise Berror(f"B78395 missing {name}")
        return match.group(1)

    prefix = 'sig1=('
    if not signature_input.startswith(prefix):
        raise Berror("B64394 invalid Signature-Input prefix")
    end = signature_input.find(')')
    if end == -1:
        raise Berror("B13246 invalid Signature-Input format")
    expected = '"@method" "@authority" "@target-uri" "content-type" "content-digest" "date"'
    if expected != signature_input[len(prefix) : end]:
        raise Berror("B78346 unexpected covered components")
    params = signature_input[end + 1 :]
    created = int(get_param(params, 'created'))
    keyid = json_string_unescape(get_param(params, 'keyid', quoted=True))
    nonce = json_string_unescape(get_param(params, 'nonce', quoted=True))
    alg = json_string_unescape(get_param(params, 'alg', quoted=True))
    if len(keyid) > 256:
        raise Berror("B22403 keyid too long")
    if not nonce or len(nonce) > 128:
        raise Berror("B22402 invalid nonce")
    if alg not in ('rsa-pss-sha512', 'rsa-pss-sha256'):
        raise Berror(f"B85199 unsupported alg {alg}")
    return created, keyid, nonce, alg


def parse_signature_header(signature_header: str) -> bytes:
    match = re.fullmatch(r'sig1=:([A-Za-z0-9+/=]+):', signature_header.strip())
    if not match:
        raise Berror("B33176 invalid Signature header")
    try:
        return base64.b64decode(match.group(1), validate=True)
    except Exception as e:
        raise Berror(f"B56110 invalid signature encoding ({e})")


def build_content_digest_header(body: bytes) -> str:
    digest = hashlib.sha256(body).digest()
    digest_b64 = base64.b64encode(digest).decode('ascii')
    return f'sha-256=:{digest_b64}:'


def consume_nonce(nonce: str, now: int, ttl_seconds: int) -> bool:
    expired = [k for k, v in nonce_cache.items() if v < now]
    for k in expired:
        del nonce_cache[k]
    if nonce in nonce_cache:
        return False
    nonce_cache[nonce] = now + ttl_seconds
    return True


def build_signature_base(
    method: str,
    authority: str,
    target_uri: str,
    created: int,
    keyid: str,
    nonce: str,
    alg: str,
    content_digest_header: str,
    date_header: str,
) -> str:
    signature_params = (
        '("@method" "@authority" "@target-uri" "content-type" "content-digest" "date")'
        f';created={created}'
        f';keyid="{keyid}"'
        f';nonce="{nonce}"'
        f';alg="{alg}"'
    )
    return (
        f'"@method": {method.upper()}\n'
        f'"@authority": {authority}\n'
        f'"@target-uri": {target_uri}\n'
        f'"content-type": application/json\n'
        f'"content-digest": {content_digest_header}\n'
        f'"date": {date_header}\n'
        f'"@signature-params": {signature_params}'
    )


def verify_signature_bytes(
    signature: bytes,
    signature_base: str,
    auth_pubkey_pem: str,
    alg: str,
    allow_sha256_fallback: bool,
) -> None:
    if alg == 'rsa-pss-sha512':
        hash_alg = hashes.SHA512()
        salt_length = 64
    elif alg == 'rsa-pss-sha256' and allow_sha256_fallback:
        hash_alg = hashes.SHA256()
        salt_length = 32
    elif alg == 'rsa-pss-sha256':
        raise Berror("B89126 rsa-pss-sha256 fallback is disabled")
    else:
        raise Berror(f"B17925 unsupported alg {alg}")
    try:
        public_key = serialization.load_pem_public_key(auth_pubkey_pem.encode('utf-8'))
    except Exception as e:
        raise Berror(f"B40573 invalid public key ({e})")
    try:
        public_key.verify(
            signature,
            signature_base.encode('utf-8'),
            padding.PSS(
                mgf=padding.MGF1(hash_alg),
                salt_length=salt_length,
            ),
            hash_alg,
        )
    except InvalidSignature:
        raise Berror("B54338 signature verification failed")
    except Exception as e:
        raise Berror(f"B53361 signature verification error ({e})")


async def verify_signed_request(
    request: Request,
    auth_pubkey_pem: str,
    allow_sha256_fallback: bool = True,
    max_skew: int = 300,  # allowed clock time difference in seconds
    nonce_ttl: int = 600,  # in seconds
) -> dict:
    """Verify that the requester posesses the privkey for auth_pubkey via RFC 9421"""
    body = await request.body()
    try:
        payload = json.loads(body)
    except Exception as e:
        raise Berror(f"B75948 invalid json body ({e})")
    expected_host = re.sub(r'^https?://', '', conf.base_url()).rstrip('/')
    actual_host = request.headers.get('host', '')
    if actual_host != expected_host:
        raise Berror(f"B70431 {actual_host} != {expected_host}")
    content_type = request.headers.get('content-type', '')
    if content_type.split(';', 1)[0].strip().lower() != 'application/json':
        raise Berror(f"B55664 unexpected Content-Type {content_type}")
    date_header = request.headers.get('date')
    content_digest_header = request.headers.get('content-digest')
    signature_input_header = request.headers.get('signature-input')
    signature_header = request.headers.get('signature')
    if not date_header:
        raise Berror("B70748 missing Date header")
    if not content_digest_header:
        raise Berror("B29725 missing Content-Digest header")
    if not signature_input_header:
        raise Berror("B45919 missing Signature-Input header")
    if not signature_header:
        raise Berror("B59684 missing Signature header")
    expected_content_digest = build_content_digest_header(body)
    if content_digest_header != expected_content_digest:
        raise Berror("B40097 Content-Digest mismatch")
    created, keyid, nonce, alg = parse_signature_input(signature_input_header)
    now = int(time.time())
    skew = now - created
    if abs(skew) > max_skew:
        skew_str = uif.human_duration(TimeDelta(seconds=abs(skew)))
        direction = "slow" if skew > 0 else "fast"
        raise Berror(f"B87034 caller clock appears to be {skew_str} {direction}")
    signature = parse_signature_header(signature_header)
    target_uri = conf.base_url() + jsonrpc_route
    signature_base = build_signature_base(
        method=request.method,
        authority=expected_host,
        target_uri=target_uri,
        created=created,
        keyid=keyid,
        nonce=nonce,
        alg=alg,
        content_digest_header=content_digest_header,
        date_header=date_header,
    )
    try:
        verify_signature_bytes(
            signature=signature,
            signature_base=signature_base,
            auth_pubkey_pem=auth_pubkey_pem,
            alg=alg,
            allow_sha256_fallback=allow_sha256_fallback,
        )
    except Exception:
        # enable to debug signature issues (search: tag_rfc9421_signature_debug):
        logger.warning(f'B62913 {signature_base=}')
        raise
    if not consume_nonce(nonce, now, nonce_ttl):  # consume nonce *after* sig verification
        raise Berror("B56057 replayed nonce")
    return payload


@jsonrpc_entrypoint.method(errors=[BaseError])
async def ping(
    request: Request,
    subd: str = Body(...),
    time: str = Body(...),
    telemetry: dict = Body(...),
    request_id: str = Body(...),
) -> BaseResult:
    ip = request.client.host if request.client else '(unknown)'
    ping_now = DateTime.now(TimeZone.utc)
    completed_full_wait = False
    wait_seconds = 25
    with db.device_by_subd(subd) as device:
        try:
            payload = await verify_signed_request(
                request=request,
                auth_pubkey_pem=device.auth_pubkey,
                allow_sha256_fallback=True,
            )
            params = payload['params']
            if params.get('subd') != subd:
                raise Berror(f"B33465 subd mismatch: {params.get('subd')} != {subd}")
        except (Berror, db.CredentialsError) as e:
            logger.warning(util.front_berror_code(e, subd, ip))
            raise BaseError("B23086 invalid ping request")  # generic API response for security
        if device.ott_id is not None:
            db.log_out(device.ott_id)
            device.ott_id = None  # tag_invalidate_device_ott
            logger.info(f"B51437 base {device.subd} completed adopt6e from {ip}")
        device.last_endpoint = ip
        device.last_handshake = int(ping_now.timestamp())
        try:
            db.process_ping(device=device, ip=ip, telem_data=telemetry, subd=subd)
        except Berror as e:
            logger.warning(util.front_berror_code(e, subd, ip))
        wait_seconds = device.long_poll_probe or device.long_poll_safe
        device_id = device.id
    global active_long_polls
    logger.debug(f"B37237 base {subd} at {ip} connect (1 of {active_long_polls+1})")
    async with active_long_polls_lock:
        active_long_polls += 1
    try:
        wait_until = ping_now + TimeDelta(seconds=wait_seconds)
        while True:
            now = DateTime.now(TimeZone.utc)
            if now > wait_until:  # long polling timeout
                completed_full_wait = True
                return BaseResult(subd=subd, status='ok', timeout_seconds=wait_seconds)
            if util.shutdown_event.is_set():  # e.g. ctrl-c
                remaining = round((wait_until - now).total_seconds())
                logger.info(f'B64194 base {subd} long polling canceled ({remaining}s remaining)')
                return BaseResult(subd=subd, status='ok')
            if await request.is_disconnected():
                remaining = round((wait_until - now).total_seconds())
                logger.info(f'B88349 base {subd} connection disconnected ({remaining}s remaining)')
                return BaseResult(subd=subd, status='ok')
            task = db.next_task(device_id)
            if task:
                return BaseResult(
                    subd=subd,
                    status='ok',
                    task_id=str(task.id),
                    task_method=task.method,
                    task_args=task.args or dict(),
                    timeout_seconds=wait_seconds,
                )
            await asyncio.sleep(2)
    finally:
        async with active_long_polls_lock:
            active_long_polls -= 1
        if completed_full_wait:
            with db.device_by_subd(subd) as device:
                db.record_long_poll_clean(device)


@jsonrpc_entrypoint.method(errors=[BaseError])
async def task_result(
    request: Request,
    subd: str = Body(...),
    task_id: str = Body(...),
    task_method: str = Body(...),
    ok: bool = Body(...),
    output: str = Body(...),
) -> BaseResult:
    ip = request.client.host if request.client else '(unknown)'
    with db.device_by_subd(subd) as device:
        try:
            payload = await verify_signed_request(
                request=request,
                auth_pubkey_pem=device.auth_pubkey,
                allow_sha256_fallback=True,
            )
            params = payload['params']
            if params.get('subd') != subd:
                raise Berror(f"B99725 subd mismatch: {params.get('subd')} != {subd}")
            if params.get('task_id') != task_id:
                raise Berror("B43120 task_id mismatch")
            if params.get('task_method') != task_method:
                raise Berror("B68410 task_method mismatch")
            if len(output) > 20000:
                raise Berror("B32614 task output too large")
            status = db.DeviceTaskStatus.DONE if ok else db.DeviceTaskStatus.FAILED
            try:
                db.mark_task_status(int(task_id), status, device_id=device.id)
            except ValueError:
                raise Berror(f"B72809 invalid task_id: {task_id}")
        except (Berror, db.CredentialsError) as e:
            logger.warning(f"{e} (base {subd} at {ip})")
            raise BaseError("B34089 invalid task_result request")
        # device.telemetry = ...
    return BaseResult(subd=subd, status='ok')


@jsonrpc_entrypoint.method(errors=[BaseError])
async def wg(
    request: Request,
    subd: str = Body(...),
    pubkey: str = Body(...),
) -> BaseResult:
    """Store WireGuard pubkey and return assigned WireGuard wg_shape."""
    ip = request.client.host if request.client else '(unknown)'
    with db.device_by_subd(subd) as device:
        try:
            payload = await verify_signed_request(
                request=request,
                auth_pubkey_pem=device.auth_pubkey,
                allow_sha256_fallback=True,
            )
            params = payload['params']
            if params.get('subd') != subd:
                raise Berror(f"B71924 subd mismatch: {params.get('subd')} != {subd}")
            if params.get('pubkey') != pubkey:
                raise Berror("B21788 pubkey mismatch")
            wg_shape = db.store_wg_pubkey(device.id, pubkey)
        except (Berror, db.CredentialsError) as e:
            logger.warning(f"{e} (base {subd} at {ip})")
            raise BaseError("B80541 invalid wg request")
    return BaseResult(subd=subd, status='ok', wg_shape=wg_shape)
