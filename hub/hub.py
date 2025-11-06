import asyncio
import inspect
import logging
import os
import platformdirs
import psutil
import signal
import socket
import sys
import sqlalchemy.exc
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
import hub.config as conf
import hub.db as db
import hub.api as api
import hub.net as net
import hub.migrate_db as migrate_db
import hub.util as util

Berror = util.Berror
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
    default_config_file = os.path.join(platformdirs.user_config_dir('bitburrow'), 'config.yaml')
    parser.add_argument(
        "--config-file",
        type=str,
        default=default_config_file,
        help=f"Config file to use (default '{default_config_file}').",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action='append_const',
        const=-1,
        dest="verbose",  # see log_levels for mapping
        help="Silence warning messages.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action='append_const',
        const=1,
        help="Increase verbosity. Can be used multiple times.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    p_generate_config = subparsers.add_parser(
        "generate-config",
        help="Create a new config file with specified domain and public IP address",
    )
    p_generate_config.add_argument(
        "gc_domain",
        type=str,
    )
    p_generate_config.add_argument(
        "gc_public_ip",
        type=str,
    )
    subparsers.add_parser(
        "migrate-config",
        help="Migrate an existing config file to the current version. Make a backup "
        "of the current file.",
    )
    subparsers.add_parser(
        "create-admin-account",
        help="Create a new admin account and display its login key. KEEP THIS LOGIN KEY SAFE!",
    )
    subparsers.add_parser(
        "create-coupon-code",
        help="Create a new coupon and display it. KEEP THIS SAFE!",
    )
    p_test = subparsers.add_parser(
        "test",
        help="Run internal test TEST.",
    )
    p_test.add_argument(
        "test_name",
        nargs="?",
        default="all",
    )
    subparsers.add_parser(
        "serve",
        help="Listen on specified port(s).",
    )
    if return_help_text:  # used by README.py
        return parser.format_help()
    return parser.parse_args()


###
### globals
###

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
    base_dir = os.path.dirname(path)
    if not os.path.exists(base_dir):
        mkdir_r(base_dir)
    try:
        os.makedirs(path, exist_ok=True)
    except (PermissionError, FileNotFoundError, NotADirectoryError):
        raise Berror(f"B19340 cannot create directory: {path}")


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
    db_file = conf.get('common.db_file')
    if db_file == '-':
        db_file = ':memory:'
    elif not db_file.startswith("/"):  # if relative, use dir of args.config_file
        db_file = os.path.join(os.path.dirname(args.config_file), db_file)
    mkdir_r(os.path.dirname(db_file))
    migrate_db.migrate(db_file)
    db.engine = create_engine(f'sqlite:///{db_file}', echo=args.create_engine_echo)
    if is_worker_zero:
        # avoid race condition creating tables: OperationalError: table ... already exists
        SQLModel.metadata.create_all(db.engine)
        db.Hub.startup()  # initialize hub data if needed


def set_logging(args):
    if args.verbose is None:  # no CLI args for log level, so use config setting
        log_index = conf.get('common.log_level')
    else:  # CLI -v and -q override
        log_index = 2 + sum(args.verbose)
    del args.verbose
    log_levels = [
        logging.CRITICAL,  # 0, -qq
        logging.ERROR,  # 1, -q
        logging.WARNING,  # 2, default
        logging.INFO,  # 3, -v
        logging.DEBUG,  # 4, -vv
        logging.DEBUG,  # 5, -vvv; sets 'echo=True' in create_engine()
    ]
    if log_index < 0 or log_index >= len(log_levels):
        raise ValueError("B43857 invalid log level")
    args.console_log_level = log_levels[log_index]
    args.create_engine_echo = log_index == 5
    logging.config.dictConfig(logs.logging_config(console_log_level=args.console_log_level))


@app.on_event('startup')
def on_startup():
    # sanity check now that we don't call cli() or init() here
    if not conf.is_loaded():
        raise Berror(f"B62896 invalid config data in on_startup()")
    global is_worker_zero
    is_worker_zero = get_lock('worker_init_lock_BMADCTCY')
    if is_worker_zero:
        # only first worker does network set-up, tear-down; avoid "RTNETLINK answers: File exists"
        try:
            wgif = db.Netif.startup()  # configure new WireGuard interface
            db.Client.startup(wgif)  # add peers to WireGuard interface
        except Exception as e:
            on_shutdown()
            raise e
    logger.debug(f"initialization complete")


@app.on_event('shutdown')
def on_shutdown():
    if is_worker_zero:
        db.Netif.shutdown()


@app.on_event("startup")
@repeat_every(seconds=60 * 60 * 24)
def check_tls_cert_daily() -> None:
    """Verify TLS certificate after 24 hours, 48 hours, etc."""
    if conf.get('http.tls_enabled') == False or conf.get('http.tls_use_certbot') == False:
        return
    if not hasattr(check_tls_cert_daily, 'call_count'):
        check_tls_cert_daily.call_count = 1
        return  # handled by check_tls_cert_at_startup() (on_event("startup") above is required)
    net.check_tls_cert(conf.get('common.domain'), conf.get('http.port'))


@app.on_event("startup")
@repeat_every(seconds=20, max_repetitions=2)  # run at t+0 and t+20 only
def check_tls_cert_at_startup() -> None:
    """Verify TLS certificate 20 seconds after startup"""
    if conf.get('http.tls_enabled') == False or conf.get('http.tls_use_certbot') == False:
        return
    if not hasattr(check_tls_cert_at_startup, 'call_count'):
        check_tls_cert_at_startup.call_count = 1
        return  # do nothing on first run (possible race condition)
    net.check_tls_cert(conf.get('common.domain'), conf.get('http.port'))


@app.on_event("startup")
@repeat_every(seconds=60)
def monitor_tls_cert_file() -> None:
    """Automatically restart Uvicorn when TLS cert is updated.

    Check every minute. Don't restart if there are active in-bound network connections.
    """
    global restarts_remaining
    if conf.get('http.tls_enabled') == False or conf.get('http.tls_use_certbot') == False:
        return
    if not hasattr(monitor_tls_cert_file, 'call_count'):
        monitor_tls_cert_file.call_count = 1
        monitor_tls_cert_file.minutes_waiting = 0
        return  # do nothing on first run (possible race condition)
    file_changes = net.has_file_changed(ssl_keyfile, max_items=1)
    if file_changes:  # our TLS cert file has changed
        connection_count = len(net.connected_inbound_list(conf.get('http.port')))
        # if we have no TCP connections (but give up waiting after 24 hours)
        if connection_count == 0 or monitor_tls_cert_file.minutes_waiting > (60 * 24):
            # reset 'restart' conditions ...
            monitor_tls_cert_file.minutes_waiting = 0
            net.watch_file(ssl_keyfile)
            del check_tls_cert_daily.call_count
            del check_tls_cert_at_startup.call_count
            logger.info(f"B50371 Restarting Uvicorn (TLS cert file has new {file_changes})")
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
                f"B57200 need to restart (TLS cert file has new {file_changes}); waiting for "
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
    try:
        args = cli()
        if args.command == 'generate-config':
            conf.generate(args.config_file, args.gc_domain, args.gc_public_ip)
            print(f"Config file generated: {args.config_file}")
            sys.exit(0)
        conf.load(args.config_file)
        set_logging(args)  # requires config file be loaded
        init(args)
        if args.command == 'migrate-config':
            conf.save(args.config_file)
            print(f"Config file migrated: {args.config_file}")
            sys.exit(0)
        if args.command == 'create-admin-account':
            login_key = db.Account.new(db.Account_kind.ADMIN)
            print(f"Login key for your new {db.Account_kind.ADMIN} (KEEP THIS SAFE!): {login_key}")
            del login_key  # do not store!
            sys.exit(0)
        elif args.command == 'create-coupon-code':
            login_key = db.Account.new(db.Account_kind.COUPON)
            print(f"Your new {db.Account_kind.COUPON} (KEEP IT SAFE): {login_key}")
            del login_key  # do not store!
            sys.exit(0)
        elif args.command == 'test':
            hub_state = db.Hub.state()
            sys.exit(0 if util.integrity_test_by_id(args.test_name) else 1)
        # args.command == 'serve':
        if conf.get('http.tls_enabled'):
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
            if conf.get('http.tls_use_certbot'):
                ssl_keyfile = f'/etc/letsencrypt/live/{conf.get('common.domain')}/privkey.pem'
                ssl_certfile = f'/etc/letsencrypt/live/{conf.get('common.domain')}/fullchain.pem'
                if net.watch_file(ssl_keyfile) == None or net.watch_file(ssl_certfile) == None:
                    sys.exit(1)
            else:
                ssl_keyfile = conf.get('http.tls_key_file')
                ssl_certfile = conf.get('http.tls_cert_file')
            scheme = 'https'
        else:
            strong_ciphers = ''
            ssl_keyfile = None
            ssl_certfile = None
            scheme = 'http'
    except Berror as e:
        logger.error(e)
        sys.exit(1)
    except sqlalchemy.exc.OperationalError as e:
        logger.error(f"B14242 DB error (may need to increase db_schema_version): {e}")
        sys.exit(1)
    try:
        version_string = f"{util.app_version()}_{migrate_db.db_schema_version}_{conf.config_fv}"
        address_list = net.all_local_ips(conf.get('http.address'), ipv6_enclosure='[]')
        public_port = conf.get('common.port')
        port_spec = '' if public_port == 443 else ':' + str(public_port)
        base_url = f"https://{conf.get('common.domain')}{port_spec}"
        logger.info(f"❚ Starting BitBurrow hub")
        logger.info(f"❚   version string: {version_string}")
        logger.info(f"❚   admin accounts: {db.Account.count(db.Account_kind.ADMIN)}")
        logger.info(f"❚   coupons: {db.Account.count(db.Account_kind.COUPON)}")
        logger.info(f"❚   manager accounts: {db.Account.count(db.Account_kind.MANAGER)}")
        logger.info(f"❚   user accounts: {db.Account.count(db.Account_kind.USER)}")
        for address in address_list:
            logger.info(f"❚   listening on: {scheme}://{address}:{conf.get('http.port')}")
        logger.info(f"❚   public URL: {base_url}/welcome")
        # FIXME: logger.info(f"❚   public URL: {base_url}{conf.get('common.site_code')}/welcome")
        this_python_file = os.path.abspath(inspect.getsourcefile(lambda: 0))
        logger.info(f"Running {this_python_file}")
    except sqlalchemy.exc.OperationalError as e:
        logger.error(f"B50313 DB error (may need to increase db_schema_version): {e}")
        sys.exit(1)
    while restarts_remaining > 0:
        restarts_remaining -= 1
        try:
            uvicorn.run(  # https://www.uvicorn.org/deployment/#running-programmatically
                f'{app_name()}.hub:app',
                host=conf.get('http.address'),
                port=conf.get('http.port'),
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
