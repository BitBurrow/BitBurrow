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
import time
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric import rsa
import hub.db as db
import hub.config as conf

Berror = db.Berror
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)

adopt5l_route = '/5l/{subd}'  # download adopt5p.sh  (list of adopt stages in class Device())
adopt5s_route = '/5s/{subd}'  # download bbbased.lua
log_err_route = '/er/{subd}'  # errors from base router
jsonrpc_route = '/api/v1'
hub_path = os.path.dirname(os.path.abspath(__file__))
router = APIRouter()

###
### adoption endpoints--file downloads (no authentication)
###


def sanitize_subd(subd):
    """Return a safer version of subd, but still unverified"""
    return re.sub(r"[^a-zA-Z0-9]", "", subd)[:8]


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
    subd = sanitize_subd(subd)  # unverified; 'adopt5p.sh' does not contain any secrets
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
    subd = sanitize_subd(subd)  # unverified; 'bbbased.lua' does not contain any secrets
    expand_braces = (
        lambda s: s.replace('{api_url}', conf.base_url() + jsonrpc_route)
        .replace('{subd}', subd)
        .replace('{ott_filename}', db.ott_filename(subd))
        .replace('{log_err_route}', conf.base_url() + log_err_route.format(subd=subd))
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
    subd = sanitize_subd(subd)
    body_unsafe = await request.body()
    body_text = body_unsafe[:900].decode("utf-8", errors="replace")
    disp = ''.join(c if c.isprintable() and c not in "\r\n\t" else repr(c)[1:-1] for c in body_text)
    if re.match(r"^B[0-9]{5} ", disp):  # front the Berror code
        logger.warning(f"{disp[0:7]}base {subd} {disp[7:]}")  # client errors are warnings here
    else:
        logger.warning(f"base {subd} {disp}")
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
        db.store_adopt6c_pubkeys(device.id, auth_pubkey, wg_pubkey)
    except (Berror, db.CredentialsError) as e:
        logger.warning(f"{e} (base {subd} at {ip})")
        raise BaseError("B87908 invalid adopt6c request")  # for security, give generic API response
    else:
        logger.info(f"B70924 base {subd} completed adopt6c from {ip}")
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

    prefix = "sig1=("
    if not signature_input.startswith(prefix):
        raise Berror("B64394 invalid Signature-Input prefix")
    end = signature_input.find(")")
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
    match = re.fullmatch(r"sig1=:([A-Za-z0-9+/=]+):", signature_header.strip())
    if not match:
        raise Berror("B33176 invalid Signature header")
    try:
        return base64.b64decode(match.group(1), validate=True)
    except Exception as e:
        raise Berror(f"B56110 invalid signature encoding ({e})")


def build_content_digest_header(body: bytes) -> str:
    digest = hashlib.sha256(body).digest()
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"sha-256=:{digest_b64}:"


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
        raise Berror("B89126 rsa-pss-sha256 fallback is disabled")
    else:
        raise Berror(f"B17925 unsupported alg {alg}")
    try:
        public_key = serialization.load_pem_public_key(auth_pubkey_pem.encode("utf-8"))
    except Exception as e:
        raise Berror(f"B40573 invalid public key ({e})")
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
        raise Berror("B54338 signature verification failed")
    except Exception as e:
        raise Berror(f"B53361 signature verification error ({e})")


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
        raise Berror(f"B75948 invalid json body ({e})")
    expected_host = re.sub(r"^https?://", "", conf.base_url()).rstrip("/")
    actual_host = request.headers.get("host", "")
    if actual_host != expected_host:
        raise Berror(f"B70431 {actual_host} != {expected_host}")
    content_type = request.headers.get("content-type", "")
    if content_type.split(';', 1)[0].strip().lower() != "application/json":
        raise Berror(f"B55664 unexpected Content-Type {content_type}")
    date_header = request.headers.get("date")
    content_digest_header = request.headers.get("content-digest")
    signature_input_header = request.headers.get("signature-input")
    signature_header = request.headers.get("signature")
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
    if abs(now - created) > max_clock_skew_seconds:
        raise Berror("B87034 created is outside acceptable clock skew")
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
        logger.warning(f'{signature_base=}')
        raise
    if not consume_nonce(nonce, now, nonce_ttl_seconds):  # consume nonce *after* sig verification
        raise Berror("B56057 replayed nonce")
    return payload


@jsonrpc_entrypoint.method(errors=[BaseError])
async def ping(
    request: Request,
    subd: str = Body(...),
    time: str = Body(...),
    uptime: str = Body(...),
    request_id: str = Body(...),
) -> BaseResult:
    ip = request.client.host if request.client else '(unknown)'
    try:
        device = db.get_device_by_subd(subd)
        payload = await verify_signed_ping_request(
            request=request,
            auth_pubkey_pem=device.auth_pubkey,
            allow_sha256_fallback=True,
            max_clock_skew_seconds=300,
            nonce_ttl_seconds=600,
        )
        params = payload["params"]
        if params.get("subd") != subd:
            raise Berror(f"B33465 subd mismatch: {params.get("subd")} != {subd}")
    except (Berror, db.CredentialsError) as e:
        logger.warning(f"{e} (base {subd} at {ip})")
        raise BaseError("B23086 invalid ping request")  # for security, give generic API response
    else:
        logger.info(f"B37237 base {subd} at {ip} ping {uptime.lstrip(' ')}")
    return BaseResult(subd=subd, status="ok")
