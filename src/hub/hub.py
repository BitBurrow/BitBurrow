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
        "--set-ssh-port",
        type=int,
        default=0,
        help="TCP port used when configuring VPN servers; default: random [2000,65535]",
    )
    parser.add_argument(
        "--get-ssh-port",
        action='store_true',
        help="Display ssh port and exit",
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
        "--api",
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
### globals
###

hub_state: db.Hub = None
app = FastAPI()
limiter = slowapi.Limiter(key_func=slowapi.util.get_remote_address)
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


@app.post('/v1/accounts/{login_key}/accounts')
# @limiter.limit('10/minute')  # FIXME: uncomment this to make brute-forcing login_key harder
async def create_account(login_key: str):
    return responses.JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={'login_key': 'NWXL8GNXK33XXXXYM7'},
    )
    account = db.Account.validate_login_key(login_key)
    with Session(db.engine) as session:
        statement = select(db.Client).where(db.Client.account_id == account.id)
        results = session.exec(statement)
        return [c.pubkey for c in results]


@app.get('/v1/accounts/{login_key}/servers')
# @limiter.limit('10/minute')  # FIXME: uncomment this to make brute-forcing login_key harder
async def list_servers(login_key: str):
    return responses.JSONResponse(
        status_code=status.HTTP_200_OK,
        # status_code=status.HTTP_403_FORBIDDEN,
        content={'servers': [10, 11, 20, 21]},
    )
    account = db.Account.validate_login_key(login_key)
    with Session(db.engine) as session:
        statement = select(db.Client).where(db.Client.account_id == account.id)
        results = session.exec(statement)
        return [c.pubkey for c in results]


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
            logger.info(f"app WebSocket reply: {reply}")


@app.post('/v1/accounts/{login_key}/servers')
# @limiter.limit('10/minute')  # FIXME: uncomment this to make brute-forcing login_key harder
async def new_server(login_key: str):
    return responses.JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={},
    )
    account = Account.validate_login_key(login_key)
    with Session(db.engine) as session:
        statement = select(Client).where(Client.account_id == account.id)
        results = session.exec(statement)
        return [c.pubkey for c in results]


@app.websocket('/v1/accounts/{login_key}/servers/{server_id}/setup_ws')
async def websocket_endpoint(websocket: WebSocket, login_key: str, server_id: int):
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


@app.get('/pubkeys/{login_key}')
# @limiter.limit('10/minute')  # FIXME: uncomment this to make brute-forcing login_key harder
async def get_pubkeys(login_key: str):
    account = db.Account.validate_login_key(login_key)
    with Session(db.engine) as session:
        statement = select(db.Client).where(db.Client.account_id == account.id)
        results = session.exec(statement)
        return [c.pubkey for c in results]


@app.post('/wg/', response_class=responses.PlainTextResponse)
@limiter.limit('100/minute')  # FIXME: reduce to 10
async def new_client(request: Request, login_key: str = Form(...), pubkey: str = Form(...)):
    account = db.Account.validate_login_key(login_key)
    with Session(db.engine) as session:
        account_client_count = (
            session.query(db.Client).filter(db.Client.account_id == account.id).count()
        )
    if account_client_count >= account.clients_max:
        raise HTTPException(status_code=422, detail="No additional clients are allowed")
    db.Client.validate_pubkey(pubkey)
    with Session(db.engine) as session:  # look for pubkey in database
        statement = select(db.Client).where(db.Client.pubkey == pubkey)
        first = session.exec(statement).first()
    if first is not None:
        if first.account_id != account.id:  # different account already has this pubkey
            raise HTTPException(status_code=422, detail="Public key already in use")
        return first.ip_list()  # return existing IPs for this pubkey
    with Session(db.engine) as session:
        client = db.Client(
            account_id=account.id,
            pubkey=pubkey,
            netif_id=1,  # FIXME: figure out how to do multiple interfaces
        )
        session.add(client)
        session.commit()  # FIXME: possible race condition where account could exceed clients_max
        client.set_peer()  # configure WireGuard for this peer
        return client.ip_list()


@app.post('/new_login_key/', response_class=responses.PlainTextResponse)
@limiter.limit('100/minute')  # FIXME: reduce to 10
async def new_login_key(
    request: Request, master_login_key: str = Form(...), comment: str = Form(...)
):
    master_account = db.Account.validate_login_key(master_login_key)
    if master_account.id != 1:
        raise HTTPException(status_code=422, detail="Master login key required")
    if len(comment) > 99:
        raise HTTPException(status_code=422, detail="Comment too long")
    with Session(db.engine) as session:
        login_key = db.Account(comment=comment)
        session.add(login_key)
        session.commit()
        return login_key.login_key


@app.get('/raise_error/')
async def error_test():
    raise HTTPException(status_code=404, detail="Test exception from /raise_error/")


def entry_point():  # called from setup.cfg
    import uvicorn  # https://www.uvicorn.org/

    args = cli()
    init(args)
    arg_combo_okay = False
    if args.set_domain != '':
        hub_state.domain = args.set_domain
    if args.get_domain:
        print(hub_state.domain)
    if args.set_ssh_port != 0:
        hub_state.ssh_port = args.set_ssh_port
    if args.get_ssh_port:
        print(hub_state.ssh_port)
    if args.set_wg_port != 0:
        hub_state.wg_port = args.set_wg_port
    if args.get_wg_port:
        print(hub_state.wg_port)
    if args.create_admin_account:
        login_key = db.Account.newAccount(db.Account_kind.ADMIN)
        print(f"Login key for your new {db.Account_kind.ADMIN} (KEEP THIS SAFE!): {login_key}")
        del login_key  # do not store!
    if args.set_domain or args.set_ssh_port or args.set_wg_port:
        hub_state.update()
        arg_combo_okay = True
    if args.get_domain or args.get_ssh_port or args.get_wg_port or args.create_admin_account:
        arg_combo_okay = True
    if arg_combo_okay:
        if args.api:
            logger.warning(
                "Argument '--api' ignored because '--get-xxx' or '--set-xxx' was specified."
            )
        sys.exit()
    if not args.api:
        logger.error("Argument '--api' not specified.")
        sys.exit()
    logger.info(f"❚ Starting BitBurrow hub")
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
            # FIXME: generate self-signed TLS cert based on IP address:
            #     mkdir -p ../.ssl/private ../.ssl/certs
            #     IP=$(echo $SSH_CONNECTION |grep -Po "^\S+\s+\S+\s+\K\S+")
            #     openssl req -new -x509 -nodes -days 3650 -newkey rsa:2048 -keyout ../.ssl/private/fastapiselfsigned.key -out ../.ssl/certs/fastapiselfsigned.crt -subj "/C=  /ST=  /L=   /O=   /OU=   /CN=$IP"
            # enable TLS in uvicorn.run():
            #     ssl_keyfile='../.ssl/private/fastapiselfsigned.key',
            #     ssl_certfile='../.ssl/certs/fastapiselfsigned.crt',
        )
    except KeyboardInterrupt:
        logger.info(f"B23324 KeyboardInterrupt")
    except Exception as e:
        logger.exception(f"B22237 Uvicorn error: {e}")
