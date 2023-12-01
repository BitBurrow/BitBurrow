import asyncio
import inspect
import logging
import os
import platformdirs
import psutil
import signal
import socket
import sys
import time
from fastapi import (
    FastAPI,
    responses,
    Request,
    HTTPException,
)
from fastapi_restful.tasks import repeat_every
from sqlmodel import SQLModel, create_engine, sql
import hub.logs as logs
import hub.db as db
import hub.api as api
import hub.net as net

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
        help="Domain to access hub api and for VPN client subdomains, e.g. vxm.example.org",
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
### globals
###

hub_state: db.Hub = None
app = FastAPI(
    exception_handlers={404: not_found_error},
    docs_url=None,  # disable "Docs URLs" to help avoid being identified; see
    redoc_url=None,  # ... https://fastapi.tiangolo.com/tutorial/metadata/#docs-urls
)
app.include_router(api.router)
is_worker_zero: bool = True
restarts_remaining = 1  # for restarting Uvicorn
ssl_keyfile = ""


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
    if db.engine is not None:
        return  # already initialized
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
        logger.info(f"API listening on port 8443")
    logger.debug(f"initialization complete")


@app.on_event('shutdown')
def on_shutdown():
    if is_worker_zero:
        db.Netif.shutdown()


@app.on_event("startup")
@repeat_every(seconds=60 * 60 * 24)
def check_tls_cert_daily() -> None:
    """Verify TLS certificate after 24 hours, 48 hours, etc."""
    if not hasattr(check_tls_cert_daily, 'call_count'):
        check_tls_cert_daily.call_count = 1
        return  # handled by check_tls_cert_at_startup() (on_event("startup") above is required)
    net.check_tls_cert(hub_state.domain, 8443)


@app.on_event("startup")
@repeat_every(seconds=20, max_repetitions=2)  # run at t+0 and t+20 only
def check_tls_cert_at_startup() -> None:
    """Verify TLS certificate 20 seconds after startup"""
    if not hasattr(check_tls_cert_at_startup, 'call_count'):
        check_tls_cert_at_startup.call_count = 1
        return  # do nothing on first run (possible race condition)
    net.check_tls_cert(hub_state.domain, 8443)


@app.on_event("startup")
@repeat_every(seconds=60)
def monitor_tls_cert_file() -> None:
    """Automatically restart Uvicorn when TLS cert is updated.

    Check every minute. Don't restart if there are active in-bound network connections.
    """
    global restarts_remaining
    if not hasattr(monitor_tls_cert_file, 'call_count'):
        monitor_tls_cert_file.call_count = 1
        monitor_tls_cert_file.minutes_waiting = 0
        return  # do nothing on first run (possible race condition)
    if net.has_file_changed(ssl_keyfile):  # our TLS cert file has changed
        connection_count = len(net.connected_inbound_list(8443))
        # if we have no TCP connections (but give up waiting after 24 hours)
        if connection_count == 0 or monitor_tls_cert_file.minutes_waiting > (60 * 24):
            # reset 'restart' conditions ...
            monitor_tls_cert_file.minutes_waiting = 0
            net.watch_file(ssl_keyfile)
            del check_tls_cert_daily.call_count
            del check_tls_cert_at_startup.call_count
            logger.info("B50371 Restarting Uvicorn to load new TLS certificate")
            time.sleep(1)  # help avoid race condition where ssl_cerfile has not yet been updated
            restarts_remaining = 1  # so ctrl-C won't exit the 'while' loop around uvicorn.run()
            us = psutil.Process()
            children = us.children(recursive=True)
            # send ctrl-C to our children; seems unnecessary but see https://stackoverflow.com/a/64129180/10590519
            for child in children:
                os.kill(child.pid, signal.SIGINT)
            # send ctrl-C to ourselves to make uvicorn.run() exit
            os.kill(us.pid, signal.SIGINT)
        else:
            logger.info(
                f"B57200 need to restart to load new TLS certificate; waiting for "
                f"{connection_count} connections to finish"
            )
            monitor_tls_cert_file.minutes_waiting += 1


###
### Startup (called from pyproject.toml)
###


def entry_point():
    import uvicorn  # https://www.uvicorn.org/
    import ssl

    global restarts_remaining
    global ssl_keyfile
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
    if net.watch_file(ssl_keyfile) == None or net.watch_file(ssl_certfile) == None:
        sys.exit(1)
    logger.info(f"❚ Starting BitBurrow hub {hub_state.hub_number}")
    logger.info(f"❚   admin accounts: {db.Account.count(db.Account_kind.ADMIN)}")
    logger.info(f"❚   coupons: {db.Account.count(db.Account_kind.COUPON)}")
    logger.info(f"❚   manager accounts: {db.Account.count(db.Account_kind.MANAGER)}")
    logger.info(f"❚   user accounts: {db.Account.count(db.Account_kind.USER)}")
    logger.info(f"❚   listening on: {hub_state.domain}:8443")
    this_python_file = os.path.abspath(inspect.getsourcefile(lambda: 0))
    logger.info(f"Running {this_python_file}")
    while restarts_remaining > 0:
        restarts_remaining -= 1
        try:
            uvicorn.run(  # https://www.uvicorn.org/deployment/#running-programmatically
                f'{app_name()}:app',
                host='',  # both IPv4 and IPv6; for one use '0.0.0.0' or '::0'
                port=8443,
                # FIXME: when using `workers=3`, additional workers' messages aren't ...
                # delivered to api.messages.message_handler()
                # may be helpful: https://medium.com/cuddle-ai/1bd809916130
                workers=1,
                log_level='info',
                log_config=logs.logging_config(console_log_level=args.console_log_level),
                ssl_keyfile=ssl_keyfile,
                ssl_certfile=ssl_certfile,
                ssl_ciphers=strong_ciphers,
                # to help avoid being identified, don't use these headers
                date_header=False,
                server_header=False,  # default 'uvicorn'
            )
        except KeyboardInterrupt:  # I don't think this ever gets triggered
            logger.info(f"B23324 KeyboardInterrupt")
        except Exception as e:
            logger.exception(f"B22237 Uvicorn error: {e}")
        logger.info(f"B76443 exiting")
