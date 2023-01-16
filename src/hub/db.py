import argon2
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
import enum
from fastapi import HTTPException
import ipaddress
import logging
import os
import re
import secrets
import socket
import subprocess
import sqlalchemy
from sqlmodel import Field, Session, SQLModel, select, JSON, Column
import tempfile
from typing import Optional
import yaml
import hub.login_key as lk

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)
engine = None


###
### DB table 'hub' - details for this BitBurrow hub; should be exactly 1 row
###

integrity_tests_yaml = '''
# note: the ending "|keep_only 'regex'" is similar to CLI "|grep -o 'regex'" with Python's re
# localhost domain A record
- id: bind_a
  cmd: dig @127.0.0.1 A {domain} +short
  expected: '{public_ip}'
# localhost domain NS record
- id: bind_ns
  cmd: dig @127.0.0.1 NS {domain} +short
  expected: '{domain}.'
# domain A record
- id: bind_a
  cmd: dig @{public_ip} A {domain} +short
  expected: '{public_ip}'
# domain NS record
- id: bind_ns
  cmd: dig @{public_ip} NS {domain} +short
  expected: '{domain}.'
# domain SOA record
- id: bind_soa
  cmd: dig @{public_ip} SOA {domain} +short |keep_only '^\S*'
  expected: '{domain}.'
# global A record
# FIXME: Is there any reason to get this directly from the nameserver of the parent domain,
#     i.e. `dig @{parent_domain_ns} {domain} |keep_only '^[0-9a-z\._-]+.*'` shows an A 
#     record and an NS record?
- id: global_a
  cmd: dig A {domain} +short
  expected: '{public_ip}'
# global NS record
- id: global_ns
  cmd: dig NS {domain} +short
  expected: '{domain}.'
# ensure BIND recursive resolver is disabled
- id: bind_recursive_off
  cmd: dig @{public_ip} A google.com +short
  expected: ''
# ensure zone transfer is disabled (list of VPN servers should not be public)
- id: bind_axfr_off
  cmd: dig @{public_ip} {domain} AXFR +short
  expected: '; Transfer failed.'

'''
integrity_tests = yaml.safe_load(integrity_tests_yaml)


class Hub(SQLModel, table=True):
    id: Optional[int] = Field(primary_key=True, default=None)
    domain: str = ''  # API url in form example.com or node.example.com
    public_ip: str = ''
    ssh_port: int = 0
    wg_port: int = 0
    # FIXME: add note--recommend url not contain `vpn` or `proxy` for firewalls of clients to block
    hub_number = lk.generate_login_key(lk.login_len)  # uniquely identify this hub
    db_version: int = 1

    @staticmethod
    def startup():
        with Session(engine) as session:
            hub_count = session.query(Hub).count()
        if hub_count == 0:
            with Session(engine) as session:
                hub = Hub()
                hub.ssh_port = random_free_port(use_udp=False, avoid=[8443])
                hub.wg_port = random_free_port(use_udp=True, avoid=[5353])
                session.add(hub)
                session.commit()
                logger.debug(f"hub row created; ssh_port {hub.ssh_port}; wg_port {hub.wg_port}")

    @staticmethod
    def state():
        with Session(engine) as session:
            statement = select(Hub).where(Hub.id == 1)
            result = session.exec(statement).one_or_none()
            assert result is not None
            return result

    def update(self):
        with Session(engine) as session:
            session.add(self)
            session.commit()

    def integrity_test(self, test):
        cmd = test['cmd'].format(domain=self.domain, public_ip=self.public_ip)
        keep_only = None  # support syntax at end of cmd similar to `grep -o`
        keep_only_search = re.search(r'^(.*?)\s*\|\s*keep_only\s+["\']([^"\']+)["\']$', cmd)
        if keep_only_search != None:
            cmd = keep_only_search.group(1)
            keep_only = keep_only_search.group(2)
        expected = test['expected'].format(domain=self.domain, public_ip=self.public_ip)
        try:
            result = run_external(cmd.split(' '))
        except RuntimeError:
            result = 'command exited with non-zero return code'
            keep_only = None
        if keep_only != None:
            first_match = re.search(keep_only, result)
            if first_match != None:
                result = first_match.group(0)
        is_good = expected == result
        log_level = logging.INFO if is_good else logging.ERROR
        logger.log(log_level, f"integrity test: {test['id']}")
        logger.log(log_level, f"    {cmd}")
        logger.log(log_level, f"    expected result: {expected}")
        logger.log(log_level, f"    actual result:   {result}")
        logger.log(log_level, f"    status:          {'good' if is_good else 'TEST FAILED'}")
        return is_good

    def integrity_test_by_id(self, test_id):
        # returns True only if there are no failed tests
        pass_count = 0
        failed_count = 0
        for t in integrity_tests:
            if (
                test_id == 'all'
                or test_id == t['id']
                or (test_id == 'dig' and t['cmd'].startswith('dig '))
            ):
                if self.integrity_test(t):
                    pass_count += 1
                else:
                    failed_count += 1
        if pass_count == 0:
            logger.warning(f"nonexistent test ID {test_id}")
        return failed_count == 0


###
### DB table 'account' - an administrative login, coupon code, manager, or user
###


class Account_kind(enum.Enum):
    ADMIN = 0  # can create, edit, and delete coupon codes; can edit and delete managers and users
    COUPON = 100  # can create managers (that's all)
    MANAGER = 200  # can set up, edit, and delete servers and clients
    USER = 300  # can set up, edit, and delete clients for a specific netif_id on 1 server
    NONE = 9999

    def __str__(self):
        str_map = {
            0: "admin account",
            100: "coupon code",
            200: "manager account",
            300: "user account",
        }
        try:
            return str_map[self.value]
        except:
            pass
        return "none"


admin_or_manager = {Account_kind.ADMIN, Account_kind.MANAGER}
coupon = {Account_kind.COUPON}


class Account(SQLModel, table=True):
    __table_args__ = (sqlalchemy.UniqueConstraint('login'),)  # must have a unique login
    id: Optional[int] = Field(primary_key=True, default=None)
    # FIXME: retry or increment on non-unique login
    login: str = Field(  # used like a username
        index=True,
        default_factory=lambda: lk.generate_login_key(lk.login_len),
    )
    key_hash: str = ''  # Argon2 hash of key (used like a hashed password)
    clients_max: int = 7
    created_at: DateTime = Field(
        sa_column=sqlalchemy.Column(
            sqlalchemy.DateTime(timezone=True),
            # FIXME: don't use utcnow() https://news.ycombinator.com/item?id=33138302
            default=DateTime.utcnow,
        )
    )
    valid_until: DateTime = Field(
        sa_column=sqlalchemy.Column(
            sqlalchemy.DateTime(timezone=True),
            # FIXME: don't use utcnow() https://news.ycombinator.com/item?id=33138302
            default=lambda: DateTime.utcnow() + TimeDelta(days=3650),
        )
    )
    kind: Account_kind = Account_kind.NONE
    netif_id: Optional[int] = Field(foreign_key='netif.id')  # used only if kind == USER
    comment: str = ""

    @staticmethod
    def count(account_kind=Account_kind.NONE):
        with Session(engine) as session:
            if account_kind == Account_kind.NONE:
                return session.query(Account).count()
            return session.query(Account).filter(Account.kind == account_kind).count()

    @staticmethod
    def newAccount(kind):  # create a new account and return its login key
        account = Account()
        key = lk.generate_login_key(lk.key_len)
        hasher = argon2.PasswordHasher()
        account.key_hash = hasher.hash(key)
        if kind == Account_kind.ADMIN:
            account.clients_max = 0  # admins cannot create VPN clients
        account.kind = kind
        login_key = account.login + key  # avoids: sqlalchemy.orm.exc.DetachedInstanceError
        account.update()
        logger.info(f"Created new {kind} {login_key}")
        return login_key

    def update(self):
        with Session(engine) as session:
            session.add(self)
            session.commit()

    @staticmethod
    def login_portion(login_key):
        return login_key[0 : lk.login_len]

    @staticmethod
    def key_portion(login_key):
        return login_key[lk.login_len :]

    @staticmethod
    def validate_login_key(login_key, allowed_kinds=None):
        if len(login_key) != lk.login_key_len:
            raise HTTPException(
                status_code=400, detail=f"Login key length must be {lk.login_key_len}"
            )
        if not set(lk.base28_digits).issuperset(login_key):
            raise HTTPException(status_code=400, detail="Invalid login key characters")
        with Session(engine) as session:
            statement = select(Account).where(Account.login == Account.login_portion(login_key))
            result = session.exec(statement).one_or_none()
        hasher = argon2.PasswordHasher()
        # attempt near constant-time key checking whether login exsists or not
        key_hash_to_test = 'x' if result is None else result.key_hash
        key = Account.key_portion(login_key)
        try:
            hasher.verify(key_hash_to_test, key)
        except (argon2.exceptions.VerifyMismatchError, argon2.exceptions.InvalidHash):
            raise HTTPException(status_code=401, detail="Login key not found")
        if hasher.check_needs_rehash(key_hash_to_test):
            result.key_hash = hasher.hash(key)  # FIXME: untested
            result.update()
            logger.info("Rehashed {login_key}")
        if result.valid_until.replace(tzinfo=TimeZone.utc) < DateTime.now(TimeZone.utc):
            raise HTTPException(status_code=403, detail="Login key expired")
        if allowed_kinds is not None:
            if result.kind not in allowed_kinds:
                raise HTTPException(status_code=422, detail="Invalid account kind")
        # FIXME: verify pubkey limit
        return result


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
    server_id: Optional[int] = Field(index=True, foreign_key='server.id')  # server this netif is on
    ipv4_base: str
    ipv6_base: str
    privkey: str
    pubkey: str
    listening_port: int  # on LAN
    # use JSON because lists are not yet supported: https://github.com/tiangolo/sqlmodel/issues/178
    public_ports: list[int] = Field(sa_column=Column(JSON))  # on server's public IP
    comment: str = ""

    def __init__(self):
        self.server_id = None
        # IPv4 base is 10. + random xx.xx. + 0
        self.ipv4_base = str(ipaddress.ip_address('10.0.0.0') + secrets.randbelow(2**16) * 2**8)
        # IPv6 base is prefix + 2 random groups + 5 0000 groups
        seven_groups = secrets.randbelow(2**32) * 2**80
        self.ipv6_base = str(ipaddress.ip_address(f'{wgif_prefix}::') + seven_groups)
        self.privkey = sudo_wg(['genkey'])
        self.pubkey = sudo_wg(['pubkey'], input=self.privkey)
        self.listening_port = 123
        self.public_ports = [123]

    class Config:  # needed for Column(JSON)
        arbitrary_types_allowed = True

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
        Netif.delete_our_wgif(isShutdown=False)
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
            + ['--out-interface', default_route_interface()]
            + '--jump MASQUERADE'.split(' ')
        )
        return i

    @staticmethod
    def delete_our_wgif(isShutdown):  # clean up wg network interfaces
        for s in re.split(r'(?:^|\n)interface:\s*', sudo_wg()):
            if s == '' or s == '\n':
                continue
            if_name = re.match(r'\S+', s).group(0)
            if if_name.startswith(wgif_prefix):  # if it was ours, it's safe to delete
                if isShutdown:
                    logger.debug(f"Removing wg interface {if_name}")
                else:
                    logger.warning(f"Removing abandoned wg interface {if_name}")
                sudo_ip(['link', 'del', 'dev', if_name])

    @staticmethod
    def shutdown():
        sudo_undo_iptables()
        Netif.delete_our_wgif(isShutdown=True)


###
### DB table 'client' - VPN client device
###


class Client(SQLModel, table=True):
    __table_args__ = (sqlalchemy.UniqueConstraint('pubkey'),)  # no 2 clients may share a key
    id: Optional[int] = Field(primary_key=True, default=None)
    netif_id: int = Field(foreign_key='netif.id')  # the server interface this client connects to
    pubkey: str
    preshared_key: str
    keepalive: int = 23  # 0==disabled
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


def random_free_port(use_udp, avoid=None):
    # for TCP, set use_udp to False
    min = 2000
    max = 65536  # min <= port < max
    attempts = 0
    default_route_ip = default_route_local_ip()
    while True:  # try ports until we find one that's available
        port = secrets.randbelow(max - min) + min
        attempts += 1
        assert attempts <= 99
        if avoid is not None and port in avoid:
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if use_udp else socket.SOCK_STREAM)
        try:
            sock.bind((default_route_ip, port))
            sock.close()
            break
        except Exception:  # probably errno.EADDRINUSE (port in use)
            pass
    return port


def default_route_interface():  # network interface of default route
    return ip_route_get('dev')


def default_route_local_ip():  # local IP address of default route
    return ip_route_get('src')


def default_route_gateway():  # default gateway IP adddress
    return ip_route_get('via')


def ip_route_get(item: str):  # get default route
    droute = ip(['route', 'get', '1.0.0.0'])
    # example output: 1.0.0.0 via 192.168.8.1 dev wlp58s0 src 192.168.8.101 uid 1000
    value_portion = re.search(r'\s' + item + r'\s(\S+)', droute)
    assert value_portion is not None, f"ip route returned: {droute}"
    return value_portion[1]


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
    log_detail = f"running: {'␣'.join(args)}"  # alternatives: ␣⋄∘•⁕⁔⁃–
    logger.info(log_detail if len(log_detail) < 170 else log_detail[:168] + "…")
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
