import asyncio
import logging
import nicegui
import os
import platformdirs
import ssl
import sys
import sqlalchemy.exc
from fastapi import (
    responses,
    Request,
    HTTPException,
)
from sqlmodel import SQLModel, create_engine, sql
import hub.logs as logs
import hub.config as conf
import hub.db as db
import hub.net as net
import hub.migrate_db as migrate_db
import hub.util as util
import hub.pages as pages

Berror = util.Berror
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)

assert sys.version_info >= (3, 8)
sql.expression.Select.inherit_cache = False  # https://github.com/tiangolo/sqlmodel/issues/189
sql.expression.SelectOfScalar.inherit_cache = False


def app_name():
    return 'bbhub'  # this is just 'hub': os.path.splitext(os.path.basename(__file__))[0]


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
    uvicorn_log_level_map = {
        0: 'critical',
        1: 'error',
        2: 'warning',
        3: 'info',
        4: 'debug',
        5: 'trace',
    }
    if log_index < 0 or log_index >= len(log_levels):
        raise ValueError("B43857 invalid log level")
    args.console_log_level = log_levels[log_index]
    args.create_engine_echo = log_index == 5
    args.uvicorn_log_level = uvicorn_log_level_map[log_index - 1 if log_index > 0 else 0]
    logging.config.dictConfig(logs.logging_config(console_log_level=args.console_log_level))


async def startup_netif():
    if not conf.is_loaded():  # sanity check
        raise Berror(f"B62896 invalid config data in startup_netif()")
    try:
        wgif = db.Netif.startup()  # configure new WireGuard interface
        db.Client.startup(wgif)  # add peers to WireGuard interface
    except Exception as e:
        shutdown_netif()
        raise e


async def shutdown_netif():
    db.Netif.shutdown()


async def watch_tls_cert() -> None:
    """Verify TLS certificate; run at startup"""
    a_day = 60 * 60 * 24
    rep_count = 0
    while True:
        if rep_count == 0:
            await asyncio.sleep(20)  # check cert 20 seconds after startup
        else:
            iaddr = net.default_listen_address(conf.get('http.address'))
            internal_site = f'{iaddr}:{conf.get("http.port")}'
            external_site = f'{conf.get('common.domain')}:{conf.get("http.port")}'
            # FIXME: above should use http.external_port
            if conf.get('http.tls_enabled'):  # we use TLS, so a valid cert should be at http.port
                await net.check_tls_cert(external_site, internal_site)
            else:  # otherwise, only check the external domain
                await net.check_tls_cert(external_site)
            await asyncio.sleep(a_day)  # check again in 24 hours
        rep_count += 1


# FIXME: rewrite this to monitor conf.get('http.restart_when_file_changed')
# @app.on_event("startup")
# @repeat_every(seconds=60)
# def monitor_tls_cert_file() -> None:
#     """Automatically restart Uvicorn when TLS cert is updated.
#
#     Check every minute. Don't restart if there are active in-bound network connections.
#     """
#     global restarts_remaining
#     if conf.get('http.tls_enabled') == False or conf.get('http.tls_use_certbot') == False:
#         return
#     if not hasattr(monitor_tls_cert_file, 'call_count'):
#         monitor_tls_cert_file.call_count = 1
#         monitor_tls_cert_file.minutes_waiting = 0
#         return  # do nothing on first run (possible race condition)
#     file_changes = net.has_file_changed(ssl_keyfile, max_items=1)
#     if file_changes:  # our TLS cert file has changed
#         connection_count = len(net.connected_inbound_list(conf.get('http.port')))
#         # if we have no TCP connections (but give up waiting after 24 hours)
#         if connection_count == 0 or monitor_tls_cert_file.minutes_waiting > (60 * 24):
#             # reset 'restart' conditions ...
#             monitor_tls_cert_file.minutes_waiting = 0
#             net.watch_file(ssl_keyfile)
#             del check_tls_cert_daily.call_count
#             del check_tls_cert_at_startup.call_count
#             logger.info(f"B50371 Restarting Uvicorn (TLS cert file has new {file_changes})")
#             time.sleep(1)  # help avoid race condition where ssl_cerfile has not yet been updated
#             restarts_remaining = 1  # so ctrl-C won't exit the 'while' loop around uvicorn.run()
#             us = psutil.Process()
#             children = us.children(recursive=True)
#             # send ctrl-C to our children; seems unnecessary but see https://stackoverflow.com/a/64129180/10590519
#             for child in children:
#                 os.kill(child.pid, signal.SIGINT)
#             # send ctrl-C to ourselves to make uvicorn.run() exit
#             os.kill(us.pid, signal.SIGINT)
#         else:
#             logger.info(
#                 f"B57200 need to restart (TLS cert file has new {file_changes}); waiting for "
#                 f"{connection_count} connections to finish"
#             )
#             monitor_tls_cert_file.minutes_waiting += 1


###
### bbhub (called from pyproject.toml)
###


def entry_point():
    try:
        args = cli()
        if args.command == 'generate-config':
            conf.generate(args.config_file, args.gc_domain, args.gc_public_ip)
            print(f"Config file generated: {args.config_file}")
            sys.exit(0)
        conf.load(args.config_file)
        set_logging(args)  # requires config file be loaded
        if db.engine is None:
            db_file = conf.get('common.db_file')
            if db_file == '-':
                db_file = ':memory:'
            elif not db_file.startswith("/"):  # if relative, use dir of args.config_file
                db_file = os.path.join(os.path.dirname(args.config_file), db_file)
            mkdir_r(os.path.dirname(db_file))
            migrate_db.migrate(db_file)
            db.engine = create_engine(f'sqlite:///{db_file}', echo=args.create_engine_echo)
            SQLModel.metadata.create_all(db.engine)
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
        logger.info(f"❚   public URL: {base_url}/")
        # FIXME: logger.info(f"❚   public URL: {base_url}{conf.get('common.site_code')}/welcome")
    except sqlalchemy.exc.OperationalError as e:
        logger.error(f"B50313 DB error (may need to increase db_schema_version): {e}")
        sys.exit(1)
    try:
        nicegui.app.on_startup(startup_netif)
        nicegui.app.on_shutdown(shutdown_netif)
        nicegui.app.on_startup(watch_tls_cert)
        nicegui.app.docs_url = None  # disable "Docs URLs" to help avoid being identified; see
        nicegui.app.redoc_url = None  # ... https://fastapi.tiangolo.com/tutorial/metadata/
        pages.register_pages()
        nicegui.ui.run(  # docs: https://nicegui.io/documentation/run
            host=conf.get('http.address'),
            port=conf.get('http.port'),
            title='BitBurrow',
            reload=False,
            uvicorn_logging_level=args.uvicorn_log_level,
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
            ssl_ciphers=strong_ciphers,
            # to help avoid being identified, don't use these headers
            date_header=False,
            server_header=False,  # default 'uvicorn'
            show_welcome_message=False,  # silence "NiceGUI ready to go on ..."
            show=False,  # don't try to open web browser
        )
    except KeyboardInterrupt:
        logger.info(f"B23324 KeyboardInterrupt")
        nicegui.app.shutdown()
    except Exception as e:
        logger.exception(f"B22237 Uvicorn error: {e}")
    logger.info(f"B76443 exiting")
