import base64
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
from email.utils import format_datetime, parsedate_to_datetime
from fastapi import APIRouter, Request, HTTPException, status, Body
from fastapi.responses import Response, PlainTextResponse
from pydantic import BaseModel
import fastapi_jsonrpc as jsonrpc
import hashlib
import json
import os
import logging
import re
import threading
import time
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
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
### adoption endpoints--file downloads (no authentication)
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
### JSON-RPC method adopt6c--accept auth_pubkey
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
        raise BaseError(str(e))
    # verify subd
    try:
        device = db.get_device_by_ott_id(lsid)
    except db.Berror as e:
        logger.warning(f"B19344 cannot find device for OTT {lsid}: {e}")
        raise BaseError(str(e))
    if device.subd != subd:
        logger.warning(f"B88940 subd mismatch for OTT {lsid} ({device.subd} != {subd})")
        raise BaseError(f"B01839 invalid subd {subd}")
    # store public keys
    try:
        db.store_adopt6c_pubkeys(device.id, auth_pubkey, wg_pubkey)
    except db.Berror as e:
        logger.warning(f"B44512 base {subd} adopt6c error: {e}")
        raise BaseError(str(e))
    ip = request.client.host if request.client else '(unknown)'
    logger.info(f"B70924 base {subd} completed adopt6c from {ip}")
    return BaseResult(subd=subd, status='ok')


###
### JSON-RPC method ping--regular check-in from managed router
###

requests_by_ip = dict()
requests_by_ip_lock = threading.Lock()
nonce_cache = dict()


def json_string_unescape(value: str) -> str:
    try:
        return json.loads('"' + value + '"')
    except Exception as e:
        raise BaseError(f"B80028 invalid quoted string ({e})")


def parse_signature_input(signature_input: str) -> tuple[int, str, str, str]:
    prefix = "sig1=("
    if not signature_input.startswith(prefix):
        raise BaseError("B80001 invalid Signature-Input prefix")
    end = signature_input.find(")")
    if end == -1:
        raise BaseError("B80002 invalid Signature-Input format")
    covered = signature_input[len(prefix) : end]
    expected = '"@method" "@authority" "@target-uri" "content-type" "content-digest" "date"'
    if covered != expected:
        raise BaseError("B80003 unexpected covered components")
    params = signature_input[end + 1 :]
    created_match = re.search(r"(?:^|;)created=(\d+)(?:;|$)", params)
    keyid_match = re.search(r'(?:^|;)keyid="((?:[^"\\]|\\.)*)"(?:;|$)', params)
    nonce_match = re.search(r'(?:^|;)nonce="((?:[^"\\]|\\.)*)"(?:;|$)', params)
    alg_match = re.search(r'(?:^|;)alg="((?:[^"\\]|\\.)*)"(?:;|$)', params)
    if not created_match:
        raise BaseError("B80004 missing created")
    if not keyid_match:
        raise BaseError("B80005 missing keyid")
    if not nonce_match:
        raise BaseError("B80006 missing nonce")
    if not alg_match:
        raise BaseError("B80007 missing alg")
    created = int(created_match.group(1))
    keyid = json_string_unescape(keyid_match.group(1))
    nonce = json_string_unescape(nonce_match.group(1))
    alg = json_string_unescape(alg_match.group(1))
    if alg not in ("rsa-pss-sha512", "rsa-pss-sha256"):
        raise BaseError(f"B80008 unsupported alg {alg}")
    return created, keyid, nonce, alg


def parse_signature_header(signature_header: str) -> bytes:
    match = re.fullmatch(r"sig1=:([A-Za-z0-9+/=]+):", signature_header.strip())
    if not match:
        raise BaseError("B80009 invalid Signature header")
    try:
        return base64.b64decode(match.group(1), validate=True)
    except Exception as e:
        raise BaseError(f"B80010 invalid signature encoding ({e})")


def build_content_digest_header(body: bytes) -> str:
    digest = hashlib.sha256(body).digest()
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"sha-256=:{digest_b64}:"


def canonical_date_from_created(created: int) -> str:
    dt = DateTime.fromtimestamp(created, tz=TimeZone.utc)
    return format_datetime(dt, usegmt=True)


def parse_http_date(date_header: str) -> DateTime:
    try:
        dt = parsedate_to_datetime(date_header)
    except Exception as e:
        raise BaseError(f"B80011 invalid Date header ({e})")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TimeZone.utc)
    return dt.astimezone(TimeZone.utc)


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
    if alg == "rsa-pss-sha512":
        hash_alg = hashes.SHA512()
        salt_length = 64
    elif alg == "rsa-pss-sha256" and allow_sha256_fallback:
        hash_alg = hashes.SHA256()
        salt_length = 32
    elif alg == "rsa-pss-sha256":
        raise BaseError("B80012 rsa-pss-sha256 fallback is disabled")
    else:
        raise BaseError(f"B80013 unsupported alg {alg}")
    try:
        public_key = serialization.load_pem_public_key(auth_pubkey_pem.encode("utf-8"))
    except Exception as e:
        raise BaseError(f"B80014 invalid public key ({e})")
    try:
        public_key.verify(
            signature,
            signature_base.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hash_alg),
                salt_length=salt_length,
            ),
            hash_alg,
        )
    except InvalidSignature:
        raise BaseError("B80015 signature verification failed")
    except Exception as e:
        raise BaseError(f"B80016 signature verification error ({e})")


async def verify_signed_ping_request(
    request: Request,
    auth_pubkey_pem: str,
    allow_sha256_fallback: bool = True,
    max_clock_skew_seconds: int = 300,
    nonce_ttl_seconds: int = 600,
) -> dict:
    """Verify that the requester posesses the privkey for auth_pubkey via RFC 9421"""
    body = await request.body()
    try:
        payload = json.loads(body)
    except Exception as e:
        raise BaseError(f"B80017 invalid JSON body ({e})")
    content_type = request.headers.get("content-type", "")
    if content_type != "application/json":
        raise BaseError(f"B80018 unexpected Content-Type {content_type}")
    date_header = request.headers.get("date")
    content_digest_header = request.headers.get("content-digest")
    signature_input_header = request.headers.get("signature-input")
    signature_header = request.headers.get("signature")
    if not date_header:
        raise BaseError("B80019 missing Date header")
    if not content_digest_header:
        raise BaseError("B80020 missing Content-Digest header")
    if not signature_input_header:
        raise BaseError("B80021 missing Signature-Input header")
    if not signature_header:
        raise BaseError("B80022 missing Signature header")
    expected_content_digest = build_content_digest_header(body)
    if content_digest_header != expected_content_digest:
        raise BaseError("B80023 Content-Digest mismatch")
    created, keyid, nonce, alg = parse_signature_input(signature_input_header)
    now = int(time.time())
    if abs(now - created) > max_clock_skew_seconds:
        raise BaseError("B80024 created is outside acceptable clock skew")
    canonical_date = canonical_date_from_created(created)
    if date_header != canonical_date:
        raise BaseError("B80025 Date does not match created exactly")
    parsed_date = parse_http_date(date_header)
    if int(parsed_date.timestamp()) != created:
        raise BaseError("B80026 Date timestamp does not match created")
    if not consume_nonce(nonce, now, nonce_ttl_seconds):
        raise BaseError("B80027 replayed nonce")
    signature = parse_signature_header(signature_header)
    target_uri = conf.base_url() + jsonrpc_route
    authority = re.sub(r"^https?://", "", conf.base_url()).rstrip("/")
    signature_base = build_signature_base(
        method=request.method,
        authority=authority,
        target_uri=target_uri,
        created=created,
        keyid=keyid,
        nonce=nonce,
        alg=alg,
        content_digest_header=content_digest_header,
        date_header=date_header,
    )
    logger.debug(f'{signature_base=}')
    verify_signature_bytes(
        signature=signature,
        signature_base=signature_base,
        auth_pubkey_pem=auth_pubkey_pem,
        alg=alg,
        allow_sha256_fallback=allow_sha256_fallback,
    )
    return payload


@jsonrpc_entrypoint.method(errors=[BaseError])
async def ping(
    request: Request,
    subd: str = Body(...),
    time: str = Body(...),
    uptime: str = Body(...),
    request_id: str = Body(...),
) -> BaseResult:
    try:
        device = db.get_device_by_subd(subd)
    except db.Berror as e:
        logger.warning(f"B81001 ping unknown subd {subd}: {e}")
        raise BaseError(f"B81002 invalid subd {subd}")
    try:
        payload = await verify_signed_ping_request(
            request=request,
            auth_pubkey_pem=device.auth_pubkey,
            allow_sha256_fallback=True,
            max_clock_skew_seconds=300,
            nonce_ttl_seconds=600,
        )
    except BaseError as e:
        logger.warning(f"B81003 ping verification failed for {subd}: {e}")
        raise
    try:
        params = payload["params"]
    except Exception:
        logger.warning(f"B81004 ping payload missing params for {subd}")
        raise BaseError("B81005 invalid payload")
    if params.get("subd") != subd:
        logger.warning(f"B81006 ping signed subd mismatch for {subd}")
        raise BaseError("B81007 subd mismatch")
    return BaseResult(subd=subd, status="ok")
