from asyncio import subprocess
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
import enum
import ipaddress
import logging
import os
import platformdirs
import re
import secrets
import subprocess
import sys
import tempfile
from typing import Optional, Final, final
from unittest import result
from fastapi import FastAPI, Form, responses, Depends, Request, Response, HTTPException
import slowapi  # https://slowapi.readthedocs.io/en/latest/
from sqlmodel import Field, Session, SQLModel, create_engine, select, sql
import sqlalchemy

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
    db_file_display = db_pathname().replace(os.path.expanduser('~'), '~')
    parser.add_argument(
        "--dbfile",
        type=str,
        default='',  # need to call db_pathname() again later with create_dir=True
        help=f"path for database file ('-' for memory-only; default: {db_file_display})",
    )
    parser.add_argument(
        "-l",
        "--logfile",
        type=str,
        default='-',
        help="path for log file (default: write to STDERR)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action='append_const',
        const=-1,
        dest="verbose",  # mapping:  "-q"->ERROR / ""->WARNING / "-v"->INFO / "-vv"->DEBUG
        help="silence warning messages",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action='append_const',
        const=1,
        help="increase verbosity",
    )
    if return_help_text:  # used by README.py
        return parser.format_help()
    args = parser.parse_args()
    args.log_level = 2 + (0 if args.verbose is None else sum(args.verbose))
    del args.verbose
    if args.log_level >= 3:  # info or debug
        log_format = '%(asctime)s.%(msecs)03d %(levelname)s %(message)s'
    else:
        log_format = '%(message)s'
    logging.basicConfig(
        format=log_format,
        datefmt='%H:%M:%S',
        filename=args.logfile if args.logfile != '-' else None,
        filemode='a',
    )
    logger = logging.getLogger(app_name())
    log_levels = [
        logging.CRITICAL,
        logging.ERROR,
        logging.WARNING,
        logging.INFO,
        logging.DEBUG,
        logging.DEBUG,  # corresponds to 'trace' in uvicorn
    ]
    try:
        logger.setLevel(log_levels[args.log_level])
    except IndexError:
        logger.setLevel(logging.WARNING)
        error = "Invalid log level"
        logger.error(error)
        raise ValueError(error)
    return args


###
### DB table 'axis' - website configuration; should be exactly 1 row
###


class Axis(SQLModel, table=True):
    id: Optional[int] = Field(primary_key=True, default=None)
    url: str = ""  # website url in form example.com or node.example.com
    # FIXME: add note--recommend url not contain `vpn` or `proxy` for firewalls of clients to block
    name: str = ""  # displayed at the top of the home screen
    image: str = ""  # base64-encoded; displayed at the top of the home screen


###
### DB table 'account' - an administrative login
###

# login key, e.g. 'L7V2BCMM3PRKVF2'
#     → log(28^15)÷log(2) ≈ 72 bits of entropy
# 6 words from 4000-word dictionary, e.g. 'OstrichPrecipiceWeldLinkRoastedLeopard'
#     → log(4000^6)÷log(2) ≈ 72 bits of entropy
# Mullvad 16-digit account number
#     → log(10^16)÷log(2) ≈ 53 bits of entropy
base28_digits: Final[str] = '23456789BCDFGHJKLMNPQRSTVWXZ'  # avoid bad words, 1/i, 0/O


class Account_kind(enum.Enum):
    ROOT = 0  # can create, edit, and delete coupon codes; can edit and delete managers and users
    COUPON = 100  # can create managers (that's all)
    MANAGER = 200  # can set up, edit, and delete servers and clients
    USER = 300  # can set up, edit, and delete clients for a specific netif_id on 1 server


class Account(SQLModel, table=True):
    __table_args__ = (sqlalchemy.UniqueConstraint('login_key'),)  # must have a unique login key
    id: Optional[int] = Field(primary_key=True, default=None)
    login_key: str = Field(  # e.g. 'L7V2BCMM3PRKVF2'
        index=True,
        default_factory=lambda: ''.join(secrets.choice(base28_digits) for i in range(15)),
    )
    clients_max: int = 7
    created_at: DateTime = Field(
        sa_column=sqlalchemy.Column(
            sqlalchemy.DateTime(timezone=True),
            default=DateTime.utcnow,
        )
    )
    valid_until: DateTime = Field(
        sa_column=sqlalchemy.Column(
            sqlalchemy.DateTime(timezone=True),
            default=lambda: DateTime.utcnow() + TimeDelta(days=3650),
        )
    )
    kind: Account_kind
    netif_id: int = Field(foreign_key='netif.id')  # used only if kind == USER
    comment: str = ""

    @staticmethod
    def partition_login_key(k):  # display version, e.g. 'L7V-2BCM-M3P-RKVF2'
        assert len(k) == 15
        return f'{k[0:3]}-{k[3:7]}-{k[7:10]}-{k[10:15]}'

    @staticmethod
    def validate_login_key(a):
        if len(a) != 15:
            raise HTTPException(status_code=422, detail="Login key length must be 15")
        if not set(base28_digits).issuperset(a):
            raise HTTPException(status_code=422, detail="Invalid login key characters")
        with Session(engine) as session:
            statement = select(Account).where(Account.login_key == a)
            result = session.exec(statement).one_or_none()
        if result is None:
            raise HTTPException(status_code=422, detail="Login key not found")
        if result.valid_until.replace(tzinfo=TimeZone.utc) < DateTime.now(TimeZone.utc):
            raise HTTPException(status_code=422, detail="Login key expired")
        # FIXME: verify pubkey limit
        return result

    @staticmethod
    def startup():
        with Session(engine) as session:
            account_count = session.query(Account).count()
        if account_count == 0:  # first run--need to define a master login key
            with Session(engine) as session:
                account = Account()
                account.clients_max = 0  # reserve master login key for login key creation, not VPNs
                account.comment = "master login key"
                session.add(account)
                session.commit()


###
### DB table 'server' - VPN server device
###


class Server(SQLModel, table=True):
    id: Optional[int] = Field(primary_key=True, default=None)
    account_id: int = Field(index=True, foreign_key='account.id')  # device admin--manager
    comment: str = ""


###
### DB table 'netif' - WireGuard network interface
###

wgif_prefix = 'fdfb'
reserved_ips = 38


class Netif(SQLModel, table=True):
    id: Optional[int] = Field(primary_key=True, default=None)
    server_id: int = Field(index=True, foreign_key='server.id')  # server this netif is on
    ipv4_base: str
    ipv6_base: str
    privkey: str
    pubkey: str
    listening_port: int
    comment: str = ""

    def __init__(self):
        # IPv4 base is 10. + random xx.xx. + 0
        self.ipv4_base = str(ipaddress.ip_address('10.0.0.0') + secrets.randbelow(2**16) * 2**8)
        # IPv6 base is prefix + 2 random groups + 5 0000 groups
        seven_groups = secrets.randbelow(2**32) * 2**80
        self.ipv6_base = str(ipaddress.ip_address(f'{wgif_prefix}::') + seven_groups)
        self.privkey = sudo_wg(['genkey'])
        self.pubkey = sudo_wg(['pubkey'], input=self.privkey)
        self.listening_port = 123

    def iface(self):
        return f'{wgif_prefix}{self.id}'  # interface name and Netif.id match

    def ipv4(self):
        # ending in '/32' feels cleaner but client can't ping, even if client uses
        # `ip address add dev wg0 10.110.169.40 peer 10.110.169.1`
        # fix seems to be `ip -4 route add .../18 dev wg0` on server or use '/18' below
        return str(ipaddress.ip_address(self.ipv4_base) + 1) + '/18'  # max 16000 clients

    def ipv6(self):
        return str(ipaddress.ip_address(self.ipv6_base) + 1) + '/114'  # max 16000 clients

    @staticmethod
    def startup():
        logger = logging.getLogger(app_name())
        sudo_sysctl('net.ipv4.ip_forward=1')
        sudo_sysctl('net.ipv6.conf.all.forwarding=1')
        with Session(engine) as session:
            netif_count = session.query(Netif).count()
        if netif_count == 0:  # first run--need to define a WireGuard interface
            with Session(engine) as session:
                new_if = Netif()
                session.add(new_if)
                session.commit()
        with Session(engine) as session:
            statement = select(Netif)
            i = session.exec(statement).one_or_none()  # for the time being, support 1 wg interface
        Netif.delete_our_wgif(logger)
        wgif = i.iface()
        # configure wgif; see `systemctl status wg-quick@wg0.service`
        sudo_ip(['link', 'add', 'dev', wgif, 'type', 'wireguard'])
        sudo_ip(['link', 'set', 'mtu', '1420', 'up', 'dev', wgif])
        sudo_ip(['-4', 'address', 'add', 'dev', wgif, i.ipv4()])
        sudo_ip(['-6', 'address', 'add', 'dev', wgif, i.ipv6()])
        sudo_wg(['set', wgif, 'private-key', f'!FILE!{i.privkey}'])
        sudo_wg(['set', wgif, 'listen-port', str(i.listening_port)])
        sudo_iptables(
            '--append FORWARD'.split(' ')
            + f'--in-interface {wgif}'.split(' ')
            + '--jump ACCEPT'.split(' ')
        )
        sudo_iptables(
            '--table nat'.split(' ')
            + '--append POSTROUTING'.split(' ')
            + ['--out-interface', ip_route_show('dev')]  # name of interface with default route
            + '--jump MASQUERADE'.split(' ')
        )
        return i

    @staticmethod
    def delete_our_wgif(logger=None):  # clean up wg network interfaces
        for s in re.split(r'(?:^|\n)interface:\s*', sudo_wg()):
            if s == '' or s == '\n':
                continue
            if_name = re.match(r'\S+', s).group(0)
            if if_name.startswith(wgif_prefix):  # if it was ours, it's safe to delete
                if logger is not None:
                    logger.warning(f"Removing abandoned wg interface {if_name}")
                sudo_ip(['link', 'del', 'dev', if_name])

    @staticmethod
    def shutdown():
        sudo_undo_iptables()
        Netif.delete_our_wgif()


###
### DB table 'client' - VPN client device
###


class Client(SQLModel, table=True):
    __table_args__ = (sqlalchemy.UniqueConstraint('pubkey'),)  # no 2 clients may share a key
    id: Optional[int] = Field(primary_key=True, default=None)
    netif_id: int = Field(foreign_key='netif.id')  # the server interface this client connects to
    pubkey: str
    # preshared_key: str
    # keepalive: int
    account_id: int = Field(index=True, foreign_key='account.id')  # device admin--manager or user
    comment: str = ""

    def ip_list(self, wgif: Netif = None):  # calculate client's 2 IP addresses for allowed-ips
        if wgif is None:
            with Session(engine) as session:
                statement = select(Netif).where(Netif.id == self.netif_id)
                wgif = session.exec(statement).one_or_none()
        ipv4 = ipaddress.ip_address(wgif.ipv4_base) + (reserved_ips + self.id)
        ipv6 = ipaddress.ip_address(wgif.ipv6_base) + (reserved_ips + self.id)
        return f'{ipv4}/32,{ipv6}/128'

    def set_peer(self, wgif: Netif = None):
        sudo_wg(  # see https://www.man7.org/linux/man-pages/man8/wg.8.html
            f'set {self.iface()}'.split(' ')
            + f'peer {self.pubkey}'.split(' ')
            # consider: + f'preshared-key !FILE!(self.preshared_key)}'  # see man page
            # consider: + f'persistent-keepalive {self.keepalive}'  # see man page
            + f'allowed-ips {self.ip_list(wgif)}'.split(' ')
        )

    def iface(self):
        return f'{wgif_prefix}{self.netif_id}'  # interface name and Netif.id match

    @staticmethod
    def validate_pubkey(k):
        if not (42 <= len(k) < 72):
            raise HTTPException(status_code=422, detail="Invalid pubkey length")
        if re.search(r'[^A-Za-z0-9/+=]', k):
            raise HTTPException(status_code=422, detail="Invalid pubkey characters")

    @staticmethod
    def startup(wgif):
        with Session(engine) as session:
            statement = select(Client)
            results = session.exec(statement)
            for c in results:  # let wg know about each valid peer
                c.set_peer(wgif)


###
### helper methods
###


def ip_route_show(item: str):  # name of interface ('dev') or IP ('via') with default route
    droute = ip(['route', 'show', 'to', '0.0.0.0/0'])
    # example output: default via 192.168.8.1 dev wlp58s0 proto dhcp metric 600
    dev_portion = re.search(r'\s' + item + r'\s(\S+)', droute)
    assert dev_portion is not None, f"ip route returned: {droute}"
    return dev_portion[1]


def sudo_sysctl(args):
    arg_list = args if type(args) is list else [args]
    return run_external(['sudo', 'sysctl'] + arg_list)


def sudo_iptables(args):
    if not hasattr(sudo_iptables, 'log'):
        sudo_iptables.log = list()
    sudo_iptables.log.append(args)
    return run_external(['sudo', 'iptables'] + args)


def sudo_undo_iptables():
    if not hasattr(sudo_iptables, 'log'):
        return
    for args in sudo_iptables.log:
        exec = ['sudo', 'iptables'] + args
        for i, a in enumerate(exec):  # invert '--append'
            if a == '--append' or a == '--insert' or a == '-A' or a == '-I':
                exec[i] = '--delete'
        run_external(exec)
    del sudo_iptables.log


def ip(args):  # without `sudo`
    return run_external(['ip'] + args)


def sudo_ip(args):
    return run_external(['sudo', 'ip'] + args)


def sudo_wg(args=[], input=None):
    exec = ['sudo', 'wg'] + args
    to_delete = list()
    for i, a in enumerate(exec):  # replace '!FILE!...' args with a temp file
        if a.startswith('!FILE!'):
            h = tempfile.NamedTemporaryFile(delete=False)
            h.write(a[6:].encode())
            h.close()
            to_delete.append(h.name)
            exec[i] = h.name
    try:
        r = run_external(exec, input=input)
    except Exception as e:
        raise e
    finally:
        for f in to_delete:  # remove temp file(s)
            os.unlink(f)
    return r


def run_external(args, input=None):
    logger = logging.getLogger(app_name())
    logger.info(f"running: {' '.join(args)}")
    exec_count = 2 if args[0] == 'sudo' else 1
    for i, a in enumerate(args[:exec_count]):  # e.g. expand 'wg' to '/usr/bin/wg'
        for p in '/usr/sbin:/usr/bin:/sbin:/bin'.split(':'):
            joined = os.path.join(p, a)
            if os.path.isfile(joined):
                args[i] = joined
                break
    proc = subprocess.run(
        args,
        input=None if input is None else input.encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"`{' '.join(args)}` returned error: {proc.stderr.decode().rstrip()}")
    return proc.stdout.decode().rstrip()


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
    config_dir = platformdirs.account_config_dir(app_name())
    if create_dir:
        mkdir_r(config_dir)
    return os.path.join(config_dir, f'data.sqlite')


engine = None
app = FastAPI()
limiter = slowapi.Limiter(key_func=slowapi.util.get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(slowapi.errors.RateLimitExceeded, slowapi._rate_limit_exceeded_handler)


@app.on_event('startup')
def on_startup():
    args = cli()  # https://www.uvicorn.org/deployment/#running-programmatically
    global engine
    if engine:  # initialize once
        return
    if args.dbfile == '':  # use default location
        db_file = db_pathname(create_dir=True)
    elif args.dbfile == '-':
        db_file = ':memory:'
    else:
        db_file = args.dbfile
    engine = create_engine(f'sqlite:///{db_file}', echo=(args.log_level >= 4))
    SQLModel.metadata.create_all(engine)
    try:
        wgif = Netif.startup()  # configure new WireGuard interface
        Account.startup()  # add master login key on first run
        Client.startup(wgif)  # add peers to WireGuard interface
    except Exception as e:
        on_shutdown()
        raise e
    logger = logging.getLogger(app_name())
    logger.debug(f"initialization complete")


@app.on_event('shutdown')
def on_shutdown():
    Netif.shutdown()


###
### web API
###


@app.get('/pubkeys/{login_key}')
# @limiter.limit('10/minute')  # FIXME: uncomment this to make brute-forcing login_key harder
def get_pubkeys(login_key: str):
    account = Account.validate_login_key(login_key)
    with Session(engine) as session:
        statement = select(Client).where(Client.account_id == account.id)
        results = session.exec(statement)
        return [c.pubkey for c in results]


@app.post('/wg/', response_class=responses.PlainTextResponse)
@limiter.limit('100/minute')  # FIXME: reduce to 10
def new_client(request: Request, login_key: str = Form(...), pubkey: str = Form(...)):
    account = Account.validate_login_key(login_key)
    with Session(engine) as session:
        account_client_count = session.query(Client).filter(Client.account_id == account.id).count()
    if account_client_count >= account.clients_max:
        raise HTTPException(status_code=422, detail="No additional clients are allowed")
    Client.validate_pubkey(pubkey)
    with Session(engine) as session:  # look for pubkey in database
        statement = select(Client).where(Client.pubkey == pubkey)
        first = session.exec(statement).first()
    if first is not None:
        if first.account_id != account.id:  # different account already has this pubkey
            raise HTTPException(status_code=422, detail="Public key already in use")
        return first.ip_list()  # return existing IPs for this pubkey
    with Session(engine) as session:
        client = Client(
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
def new_login_key(request: Request, master_login_key: str = Form(...), comment: str = Form(...)):
    master_account = Account.validate_login_key(master_login_key)
    if master_account.id != 1:
        raise HTTPException(status_code=422, detail="Master login key required")
    if len(comment) > 99:
        raise HTTPException(status_code=422, detail="Comment too long")
    with Session(engine) as session:
        login_key = Account(comment=comment)
        session.add(login_key)
        session.commit()
        return login_key.login_key


@app.get('/raise_error/')
def error_test():
    raise HTTPException(status_code=404, detail="Test exception from /raise_error/")


def entry_point():  # called from setup.cfg
    import uvicorn  # https://www.uvicorn.org/

    try:
        uvicorn.run(  # https://www.uvicorn.org/deployment/#running-programmatically
            f'{app_name()}:app',
            host='',  # both IPv4 and IPv6; for one use '0.0.0.0' or '::0'
            port=8000,
            # workers=3,  # FIXME
            workers=1,
            log_level='debug'
            # FIXME: generate self-signed TLS cert based on IP address:
            #     mkdir -p ../.ssl/private ../.ssl/certs
            #     IP=$(echo $SSH_CONNECTION |grep -Po "^\S+\s+\S+\s+\K\S+")
            #     openssl req -new -x509 -nodes -days 3650 -newkey rsa:2048 -keyout ../.ssl/private/fastapiselfsigned.key -out ../.ssl/certs/fastapiselfsigned.crt -subj "/C=  /ST=  /L=   /O=   /OU=   /CN=$IP"
            # enable TLS in uvicorn.run():
            #     ssl_keyfile='../.ssl/private/fastapiselfsigned.key',
            #     ssl_certfile='../.ssl/certs/fastapiselfsigned.crt',
        )
    except KeyboardInterrupt:
        on_shutdown()
        sys.exit()
    except Exception as e:
        on_shutdown()
        raise e
