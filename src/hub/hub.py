import asyncio
import json
import logging
import os
import platformdirs
import socket
import sys
from fastapi import (
    FastAPI,
    Form,
    responses,
    Request,
    HTTPException,
    status,
    WebSocket,
    WebSocketDisconnect,
)
import slowapi  # https://slowapi.readthedocs.io/en/latest/
from sqlmodel import Session, SQLModel, create_engine, select, sql
import yaml
import hub.logs as logs
import hub.db as db

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)

assert sys.version_info >= (3, 8)
sql.expression.Select.inherit_cache = False  # https://github.com/tiangolo/sqlmodel/issues/189
sql.expression.SelectOfScalar.inherit_cache = False


def app_name():
    return os.path.splitext(os.path.basename(__file__))[0]


async def not_found_error(request: Request, exc: HTTPException):
    return responses.PlainTextResponse(content=None, status_code=404)


###
### command-line interface
###


def cli(return_help_text=False):
    import argparse  # https://docs.python.org/3/library/argparse.html

    help_width = 78 if return_help_text else None  # consistent width for README.py
    formatter_class = lambda prog: argparse.HelpFormatter(
        prog,
        max_help_position=33,
        width=help_width,
    )
    parser = argparse.ArgumentParser(
        prog=app_name(),
        formatter_class=formatter_class,
    )
    parser.add_argument(
        "--set-domain",
        type=str,
        default='',
        help="Domain to access hub api and for VPN client subdomains, e.g. a19.example.org",
    )
    parser.add_argument(
        "--get-domain",
        action='store_true',
        help="Display the configured domain and exit",
    )
    parser.add_argument(
        "--set-ip",
        type=str,
        default='',
        help="Public IP address to access hub api",
    )
    parser.add_argument(
        "--get-ip",
        action='store_true',
        help="Display the configured public IP address and exit",
    )
    parser.add_argument(
        "--set-wg-port",
        type=int,
        default=0,
        help="UDP port used by VPN servers; default: random [2000,65535]",
    )
    parser.add_argument(
        "--get-wg-port",
        action='store_true',
        help="Display wg port and exit",
    )
    parser.add_argument(
        "--create-admin-account",
        action='store_true',
        help="Create a new admin account and display its login key; KEEP THIS LOGIN KEY SAFE",
    )
    parser.add_argument(
        "--create-coupon-code",
        action='store_true',
        help="Create a new coupon and display it; KEEP THIS SAFE",
    )
    parser.add_argument(
        "--test",
        type=str,
        default='',
        help="Run internal test TEST",
    )
    parser.add_argument(
        "--daemon",
        action='store_true',
        help="Listen on API port for requests from the app",
    )
    db_file_display = db_pathname().replace(os.path.expanduser('~'), '~')
    parser.add_argument(
        "--dbfile",
        type=str,
        default='',  # need to call db_pathname() again later with create_dir=True
        help=f"Path for database file ('-' for memory-only; default: {db_file_display})",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action='append_const',
        const=-1,
        dest="verbose",  # mapping:  "-q"->ERROR / ""->WARNING / "-v"->INFO / "-vv"->DEBUG
        help="Silence warning messages",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action='append_const',
        const=1,
        help="Increase verbosity",
    )
    if return_help_text:  # used by README.py
        return parser.format_help()
    if not os.access(os.getcwd(), os.W_OK):  # if cwd is not writable ...
        os.chdir(os.path.expanduser('~'))  # cd to home directory so we can write log file
    args = parser.parse_args()
    log_index = 2 + (0 if args.verbose is None else sum(args.verbose))
    del args.verbose
    log_levels = [
        logging.CRITICAL,
        logging.ERROR,
        logging.WARNING,
        logging.INFO,
        logging.DEBUG,
        logging.DEBUG,  # corresponds to 'trace' in uvicorn
    ]
    if log_index < 0 or log_index >= len(log_levels):
        raise ValueError("Invalid log level")
    args.console_log_level = log_levels[log_index]
    logging.config.dictConfig(logs.logging_config(console_log_level=args.console_log_level))
    return args


###
### Server configuration
###


class ServerSetup:
    def __init__(self, ws: WebSocket):
        self._ws = ws

    async def send_command_to_client(self, json_string):
        try:
            await self._ws.send_text(json_string)
        except Exception as e:
            logger.error(f"B38260 WebSocket error: {e}")
        try:
            return await self._ws.receive_text()
        except WebSocketDisconnect:
            logger.info(f"B38261 WebSocket disconnect")
        except Exception as e:
            logger.error(f"B38262 WebSocket error: {e}")
            # self._error_count += 1

    async def config_steps(self):
        # user connects router
        f_path = f'{os.path.dirname(__file__)}/server_setup_steps.yaml'
        with open(f_path, "r") as f:
            server_setup_steps = yaml.safe_load(f)
        priorId = 0
        for step in server_setup_steps:
            assert step['id'] > priorId
            priorId = step['id']
            reply = await self.send_command_to_client(json.dumps({step['key']: step['value']}))
            logger.debug(f"app WebSocket reply: {reply}")


class TcpWebSocket:
    # originally based on https://github.com/jimparis/unwebsockify/blob/master/unwebsockify.py
    def __init__(self, tcp_address, tcp_port, ws: WebSocket):
        self._addr = tcp_address
        self._port = tcp_port
        self._ws = ws

    async def copy(self, reader, writer):
        while True:
            data = await reader()
            if data == b'':
                break
            future = writer(data)
            if future:
                await future

    async def handle_client(self, r, w):
        peer = w.get_extra_info("peername")
        logger.info(f'TCP connection: {peer}')
        loop = asyncio.get_event_loop()

        def r_reader():
            return r.read(65536)

        try:
            tcp_to_ws = loop.create_task(self.copy(r_reader, self._ws.send_bytes))
            ws_to_tcp = loop.create_task(self.copy(self._ws.receive_bytes, w.write))
            done, pending = await asyncio.wait(
                [tcp_to_ws, ws_to_tcp], return_when=asyncio.FIRST_COMPLETED
            )
            for x in done:
                try:
                    await x
                except:
                    pass
            for x in pending:
                x.cancel()
        except Exception as e:
            print(f'{peer} exception:', e)
        w.close()
        print(f'{peer} closed')

    async def start(self):
        server = await asyncio.start_server(self.handle_client, self._addr, self._port)
        logger.debug(f'listening on {self._addr} port {self._port}')
        async with server:
            await server.serve_forever()


###
### globals
###

hub_state: db.Hub = None
app = FastAPI(
    exception_handlers={404: not_found_error},
    docs_url=None,  # disable "Docs URLs" to help avoid being identified; see
    redoc_url=None,  # ... https://fastapi.tiangolo.com/tutorial/metadata/#docs-urls
)
limiter = slowapi.Limiter(key_func=slowapi.util.get_remote_address, default_limits=["10/minute"])
app.state.limiter = limiter
app.add_exception_handler(slowapi.errors.RateLimitExceeded, slowapi._rate_limit_exceeded_handler)
is_worker_zero: bool = True


###
### startup and shutdown
###


def mkdir_r(path):  # like Linux `mkdir --parents`
    if path == '':
        return
    base = os.path.dirname(path)
    if not os.path.exists(base):
        mkdir_r(base)
    os.makedirs(path, exist_ok=True)
    # except (PermissionError, FileNotFoundError, NotADirectoryError):
    #     one of these will be raised if the directory cannot be created


def db_pathname(create_dir=False):
    config_dir = platformdirs.user_config_dir('bitburrow')
    if create_dir:
        mkdir_r(config_dir)
    return os.path.join(config_dir, f'data.sqlite')


def get_lock(process_name):  # source: https://stackoverflow.com/a/7758075
    # hold a reference to our socket so it does not get garbage collected when the function exits
    get_lock._lock_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        # the null byte (\0) means the socket is created in the abstract namespace instead of being
        # created on the file system itself;  works only in Linux
        get_lock._lock_socket.bind('\0' + process_name)
    except socket.error:
        return False
    return True


def init(args):
    assert db.engine is None
    if args.dbfile == '':  # use default location
        db_file = db_pathname(create_dir=True)
    elif args.dbfile == '-':
        db_file = ':memory:'
    else:
        db_file = args.dbfile
    db.engine = create_engine(
        f'sqlite:///{db_file}', echo=(args.console_log_level <= logging.DEBUG)
    )
    if is_worker_zero:
        # avoid race condition creating tables: OperationalError: table ... already exists
        SQLModel.metadata.create_all(db.engine)
    db.Hub.startup()  # initialize hub data if needed
    global hub_state
    hub_state = db.Hub.state()


@app.on_event('startup')
def on_startup():
    global is_worker_zero
    is_worker_zero = get_lock('worker_init_lock_BMADCTCY')
    init(cli())
    if is_worker_zero:
        # only first worker does network set-up, tear-down; avoid "RTNETLINK answers: File exists"
        try:
            wgif = db.Netif.startup()  # configure new WireGuard interface
            db.Client.startup(wgif)  # add peers to WireGuard interface
        except Exception as e:
            on_shutdown()
            raise e
    if is_worker_zero:
        print(f"API listening on port 8443")
    logger.debug(f"initialization complete")


@app.on_event('shutdown')
def on_shutdown():
    if is_worker_zero:
        db.Netif.shutdown()


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


@app.post('/v1/coupons/{coupon}/managers')
async def create_manager(request: Request, coupon: str):
    account = db.Account.validate_login_key(coupon, allowed_kinds=db.coupon)
    login_key = db.Account.new(db.Account_kind.MANAGER)
    return responses.JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={'login_key': login_key},
    )
    # do not store login_key!


@app.get('/v1/managers/{login_key}/servers')
async def list_servers(request: Request, login_key: str):
    account = db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
    with Session(db.engine) as session:
        statement = select(db.Server).where(db.Server.account_id == account.id)
        results = session.exec(statement)
        return {'servers': [c.id for c in results]}


@app.post('/v1/managers/{login_key}/servers')
async def new_server(login_key: str):
    account = db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
    server_id = db.Server.new(account.id)
    return responses.JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={'server_id': server_id},
    )


@app.websocket('/v1/managers/{login_key}/servers/{server_id}/setup')
# @limiter.limit('10/minute')  # https://slowapi.readthedocs.io/en/latest/#websocket-endpoints
async def websocket_endpoint(websocket: WebSocket, login_key: str, server_id: int):
    account = db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
    await websocket.accept()
    runTasks = ServerSetup(websocket)
    try:
        await runTasks.config_steps()
    except asyncio.exceptions.CancelledError:
        logger.info(f"B15058 config canceled")
    try:
        await websocket.close()
    except Exception as e:
        logger.error(f"B38263 WebSocket error: {e}")  # e.g. websocket already closed


@app.websocket('/v1/managers/{login_key}/servers/{server_id}/proxy')
async def websocket_endpoint(websocket: WebSocket, login_key: str, server_id: int):
    account = db.Account.validate_login_key(login_key, allowed_kinds=db.admin_or_manager)
    await websocket.accept()
    tcp_websocket = TcpWebSocket(tcp_port=30915, tcp_address='127.0.0.1', ws=websocket)
    await tcp_websocket.start()
    try:
        await websocket.close()
    except Exception as e:
        logger.error(f"B38264 WebSocket error: {e}")  # e.g. websocket already closed


###
### Startup (called from setup.cfg)
###


def entry_point():
    import uvicorn  # https://www.uvicorn.org/
    import ssl

    args = cli()
    init(args)
    arg_combo_okay = False
    if args.set_domain != '':
        hub_state.domain = args.set_domain
        hub_state.update()
        arg_combo_okay = True
    if args.get_domain:
        print(hub_state.domain)
        arg_combo_okay = True
    if args.set_ip != '':
        hub_state.public_ip = args.set_ip
        hub_state.update()
        arg_combo_okay = True
    if args.get_ip:
        print(hub_state.public_ip)
        arg_combo_okay = True
    if args.set_wg_port != 0:
        hub_state.wg_port = args.set_wg_port
        hub_state.update()
        arg_combo_okay = True
    if args.get_wg_port:
        print(hub_state.wg_port)
        arg_combo_okay = True
    if args.create_admin_account:
        login_key = db.Account.new(db.Account_kind.ADMIN)
        print(f"Login key for your new {db.Account_kind.ADMIN} (KEEP THIS SAFE!): {login_key}")
        del login_key  # do not store!
        arg_combo_okay = True
    if args.create_coupon_code:
        login_key = db.Account.new(db.Account_kind.COUPON)
        print(f"Your new {db.Account_kind.COUPON} (KEEP IT SAFE): {login_key}")
        del login_key  # do not store!
        arg_combo_okay = True
    if args.test != '':
        sys.exit(0 if hub_state.integrity_test_by_id(args.test) else 1)
    if arg_combo_okay:
        if args.daemon:
            logger.error("Argument '--daemon' cannot be used with '--get-xxx' or '--set-xxx'.")
            sys.exit(2)
        sys.exit(0)
    if not args.daemon:
        logger.error("No arguments specified. See '--help' for usage.")
        sys.exit(2)
    if hub_state.domain == '':
        logger.error("Use `--set-domain` to configure your domain name before running API.")
        sys.exit(2)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    strong_ciphers = ':'.join(
        [
            cipher['name']
            for cipher in ctx.get_ciphers()
            if cipher['name']
            not in [
                # remove ciphers considered weak by https://www.ssllabs.com/
                # SSL Labs says remaining ciphers still work for Android 4.4.2 and iOS 9
                'ECDHE-ECDSA-AES256-SHA384',
                'ECDHE-RSA-AES256-SHA384',
                'ECDHE-ECDSA-AES128-SHA256',
                'ECDHE-RSA-AES128-SHA256',
            ]
        ]
    )
    ssl_keyfile = f'/etc/letsencrypt/live/{hub_state.domain}/privkey.pem'
    ssl_certfile = f'/etc/letsencrypt/live/{hub_state.domain}/fullchain.pem'
    if not os.access(ssl_keyfile, os.R_OK):
        logger.error(f"File {ssl_keyfile} is missing or unreadable.")
        sys.exit(1)
    if not os.access(ssl_certfile, os.R_OK):
        logger.error(f"File {ssl_certfile} is missing or unreadable.")
        sys.exit(1)
    logger.info(f"❚ Starting BitBurrow hub {hub_state.hub_number}")
    logger.info(f"❚   admin accounts: {db.Account.count(db.Account_kind.ADMIN)}")
    logger.info(f"❚   coupons: {db.Account.count(db.Account_kind.COUPON)}")
    logger.info(f"❚   manager accounts: {db.Account.count(db.Account_kind.MANAGER)}")
    logger.info(f"❚   user accounts: {db.Account.count(db.Account_kind.USER)}")
    try:
        uvicorn.run(  # https://www.uvicorn.org/deployment/#running-programmatically
            f'{app_name()}:app',
            host='',  # both IPv4 and IPv6; for one use '0.0.0.0' or '::0'
            port=8443,
            workers=3,
            log_level='info',
            log_config=logs.logging_config(console_log_level=args.console_log_level),
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
            ssl_ciphers=strong_ciphers,
            # to help avoid being identified, don't use these headers
            date_header=False,
            server_header=False,  # default 'uvicorn'
        )
    except KeyboardInterrupt:
        logger.info(f"B23324 KeyboardInterrupt")
    except Exception as e:
        logger.exception(f"B22237 Uvicorn error: {e}")
