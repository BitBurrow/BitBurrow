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
        help="Decrease verbosity. Can be used multiple times.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action='append_const',
        const=1,
        help="Increase verbosity. Can be used multiple times.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
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
    p_shell = subparsers.add_parser(
        "shell-to-device",
        help="Launch an ssh shell to DEVICE_ID",
    )
    p_shell.add_argument(
        "device_id",
        type=int,
        metavar="DEVICE_ID",
        help="ID from '/home' page as an admin",
    )
    p_port_forward_script = subparsers.add_parser(
        "port-forward-script",
        help="Print a Bash script for forwarding ports to LXC container",
    )
    p_tls_cert_script = subparsers.add_parser(
        "tls-cert-script",
        help="Print a Bash script for installing and configuring Certbot",
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
        help="Listen on port(s) specified in the config file.",
    )
    if return_help_text:  # used by README.py
        return parser.format_help()
    return parser.parse_args()


###
### startup and shutdown
###


def set_logging(args):
    if args.verbose is None:  # no CLI args for log level, so use config setting
        log_index = conf.get('path.log_level')
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
    log_level_uvicorn_map = {
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
    args.log_level_uvicorn = log_level_uvicorn_map[conf.get('path.log_level_uvicorn')]
    logging.config.dictConfig(logs.logging_config(console_log_level=args.console_log_level))


async def on_startup():
    if not conf.is_loaded():  # sanity check
        raise Berror(f"B62896 invalid config data in startup_intf()")
    db.on_startup()  # configure WireGuard, etc.


async def on_shutdown():
    db.on_shutdown()


async def watch_tls_cert() -> None:
    """Verify TLS certificate; run at startup"""
    a_day = 60 * 60 * 24
    rep_count = 0
    while True:
        if rep_count == 0:
            await asyncio.sleep(20)  # check cert 20 seconds after startup
        else:
            frontend_site = f'{conf.get('frontend.domain')}:{conf.get("frontend.web_port")}'
            iaddr = net.default_listen_address(conf.get('backend.ip'))
            backend_site = f'{iaddr}:{conf.get("backend.web_port")}'
            if conf.get('backend.web_proto') == 'http':  # only check the public-facing domain
                await net.check_tls_cert(frontend_site)
            else:  # otherwise, a valid cert should be at backend.web_port too
                await net.check_tls_cert(frontend_site, backend_site)
            await asyncio.sleep(a_day)  # check again in 24 hours
        rep_count += 1


# FIXME: rewrite this to monitor conf.get('path.restart_on_change')
# @app.on_event("startup")
# @repeat_every(seconds=60)
# def monitor_tls_cert_file() -> None:
#     """Automatically restart Uvicorn when TLS cert is updated.
#
#     Check every minute. Don't restart if there are active in-bound network connections.
#     """
#     global restarts_remaining
#     if not hasattr(monitor_tls_cert_file, 'call_count'):
#         monitor_tls_cert_file.call_count = 1
#         monitor_tls_cert_file.minutes_waiting = 0
#         return  # do nothing on first run (possible race condition)
#     file_changes = net.has_file_changed(ssl_keyfile, max_items=1)
#     if file_changes:  # our TLS cert file has changed
#         connection_count = len(net.connected_inbound_list(conf.get('backend.web_port')))
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
            db_file = conf.get('path.db')
            if db_file == '-':
                db_file = ':memory:'
            elif not db_file.startswith("/"):  # if relative, use dir of args.config_file
                db_file = os.path.join(os.path.dirname(args.config_file), db_file)
            util.mkdir_r(os.path.dirname(db_file))
            migrate_db.migrate(db_file)
            db.engine = create_engine(f'sqlite:///{db_file}', echo=args.create_engine_echo)
            SQLModel.metadata.create_all(db.engine)
        if args.command == 'migrate-config':
            conf.save(args.config_file)
            print(f"Config file migrated: {args.config_file}")
            sys.exit(0)
        if args.command == 'create-admin-account':
            login_key = db.new_account(db.AccountKind.ADMIN)
            print(f"Login key for your new {db.AccountKind.ADMIN} (KEEP THIS SAFE!): {login_key}")
            del login_key  # do not store!
            sys.exit(0)
        elif args.command == 'create-coupon-code':
            login_key = db.new_account(db.AccountKind.COUPON)
            print(f"Your new {db.AccountKind.COUPON} (KEEP IT SAFE): {login_key}")
            del login_key  # do not store!
            sys.exit(0)
        elif args.command == 'shell-to-device':
            db.shell_to_device(args.device_id)
            sys.exit(0)
        elif args.command == 'port-forward-script':
            util.port_forward_script()
            sys.exit(0)
        elif args.command == 'tls-cert-script':
            util.tls_cert_script()
            sys.exit(0)
        elif args.command == 'test':
            sys.exit(0 if util.integrity_test_by_id(args.test_name) else 1)
        # args.command == 'serve':
        if conf.get('backend.web_proto') == 'https':
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
            if conf.get('path.tls_key') == '':  # empty means "use Certbot"
                ssl_keyfile = f'/etc/letsencrypt/live/{conf.get('frontend.domain')}/privkey.pem'
                ssl_certfile = f'/etc/letsencrypt/live/{conf.get('frontend.domain')}/fullchain.pem'
                if net.watch_file(ssl_keyfile) == None or net.watch_file(ssl_certfile) == None:
                    sys.exit(1)
            else:
                ssl_keyfile = conf.get('path.tls_key')
                ssl_certfile = conf.get('path.tls_cert')
            scheme = 'https'
        elif conf.get('backend.web_proto') == 'http':
            strong_ciphers = ''
            ssl_keyfile = None
            ssl_certfile = None
            scheme = 'http'
        else:
            raise Berror(f'B41626 {conf.get('backend.web_proto')=}')
    except Berror as e:
        logger.error(e)
        sys.exit(1)
    except sqlalchemy.exc.OperationalError as e:
        logger.error(f"B14242 DB error (may need to increase db_schema_version): {e}")
        sys.exit(1)
    try:
        version_string = f"{util.app_version()}_{migrate_db.db_schema_version}_{conf.config_fv}"
        address_list = net.all_local_ips(conf.get('backend.ip'), ipv6_enclosure='[]')
        public_port = conf.get('frontend.web_port')
        port_spec = '' if public_port == 443 else ':' + str(public_port)
        base_url = f"{conf.get('frontend.web_proto')}://{conf.get('frontend.domain')}{port_spec}"
        logger.info(f"❚ Starting BitBurrow hub")
        logger.info(f"❚   version string: {version_string}")
        logger.info(f"❚   admin accounts: {db.account_count(db.AccountKind.ADMIN)}")
        logger.info(f"❚   coupons: {db.account_count(db.AccountKind.COUPON)}")
        logger.info(f"❚   manager accounts: {db.account_count(db.AccountKind.MANAGER)}")
        logger.info(f"❚   user accounts: {db.account_count(db.AccountKind.USER)}")
        for address in address_list:
            logger.info(f"❚   listening on: {scheme}://{address}:{conf.get('backend.web_port')}")
        logger.info(f"❚   frontend URL: {base_url}/welcome")
        # FIXME: logger.info(f"❚   public URL: {base_url}{conf.get('frontend.site_code')}/welcome")
    except sqlalchemy.exc.OperationalError as e:
        logger.error(f"B50313 DB error (may need to increase db_schema_version): {e}")
        sys.exit(1)
    try:
        nicegui.app.on_startup(on_startup)
        nicegui.app.on_shutdown(on_shutdown)
        nicegui.app.on_startup(watch_tls_cert)
        nicegui.app.docs_url = None  # disable "Docs URLs" to help avoid being identified; see
        nicegui.app.redoc_url = None  # ... https://fastapi.tiangolo.com/tutorial/metadata/
        pages.register_pages()
        nicegui.ui.run(  # docs: https://nicegui.io/documentation/run
            host=conf.get('backend.ip'),
            port=conf.get('backend.web_port'),
            title='BitBurrow',
            favicon='hub/ui/img/favicon.png',
            reload=False,
            uvicorn_logging_level=args.log_level_uvicorn,
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
