import argon2
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
import enum
from fastapi import HTTPException
import ipaddress
import logging
import re
import secrets
import sqlalchemy
from sqlmodel import Field, Session, SQLModel, select, JSON, Column
from typing import Optional
import hub.login_key as lk
import hub.net as net
from pydantic import ConfigDict

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)
engine = None


class CredentialsError(Exception):
    pass


###
### DB table 'account' - an administrative login, coupon code, manager, or user
###


class Account_kind(enum.Enum):
    ADMIN = 900  # can create, edit, and delete coupon codes; can edit and delete managers and users
    COUPON = 700  # can create managers (that's all)
    MANAGER = 400  # can set up, edit, and delete bases and clients
    USER = 200  # can set up, edit, and delete clients for a specific netif_id on 1 base
    NONE = 0  # not signed in

    def __str__(self):
        str_map = {
            900: "admin account",
            700: "coupon code",
            400: "manager account",
            200: "user account",
            0: "none",
        }
        try:
            return str_map[self.value]
        except:
            pass
        return f"kind_{self.value}"

    def token_name(self):
        str_map = {
            900: "admin login key",
            700: "coupon code",
            400: "login key",
            200: "user login key",
            0: "none",
        }
        try:
            return str_map[self.value]
        except:
            pass
        return f"kind_{self.value}"


admin_or_manager = {Account_kind.ADMIN, Account_kind.MANAGER}
coupon = {Account_kind.COUPON}
admin_manager_or_coupon = {Account_kind.ADMIN, Account_kind.MANAGER, Account_kind.COUPON}
lkocc_string = '__login_key_or_coupon_code__'


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
        sa_column=Column(sqlalchemy.DateTime(timezone=True)),
        default_factory=lambda: DateTime.now(TimeZone.utc),
    )
    valid_until: DateTime = Field(
        sa_column=Column(sqlalchemy.DateTime(timezone=True)),
        default_factory=lambda: DateTime.now(TimeZone.utc) + TimeDelta(days=3650),
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
    def new(kind):  # create a new account and return its login key
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
            return self.id

    @staticmethod
    def login_portion(login_key):
        return login_key[0 : lk.login_len]

    @staticmethod
    def key_portion(login_key):
        return login_key[lk.login_len :]

    @staticmethod
    def validate_login_key(login_key, allowed_kinds=None):
        if len(login_key) != lk.login_key_len:
            raise CredentialsError(f"B64292 {lkocc_string} length must be {lk.login_key_len}")
        if not set(lk.base28_digits).issuperset(login_key):
            raise CredentialsError(f"B51850 invalid {lkocc_string} characters")
        with Session(engine) as session:
            statement = select(Account).where(Account.login == Account.login_portion(login_key))
            result = session.exec(statement).one_or_none()
        hasher = argon2.PasswordHasher()
        if result is None:
            # attempt near constant-time key checking whether login exsists or not
            # https://chatgpt.com/share/68812be3-5f14-800d-ba89-55d5914881d9
            key_hash_to_test = '$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        else:
            key_hash_to_test = result.key_hash
        key = Account.key_portion(login_key)
        try:
            hasher.verify(key_hash_to_test, key)
        except argon2.exceptions.VerifyMismatchError:
            raise CredentialsError(
                f"B54441 {lkocc_string} not found; " "make sure it was entered correctly"
            )
        if hasher.check_needs_rehash(key_hash_to_test):
            result.key_hash = hasher.hash(key)  # FIXME: untested
            result.update()
            logger.info("B74657 rehashed {login_key}")
        if result.valid_until.replace(tzinfo=TimeZone.utc) < DateTime.now(TimeZone.utc):
            raise CredentialsError(f"B18952 {lkocc_string} expired")
        if allowed_kinds is not None:
            if result.kind not in allowed_kinds:
                if result.kind in admin_or_manager and allowed_kinds == coupon:
                    raise CredentialsError(
                        "B10052 this is a login key; please enter a coupon code "
                        "or select 'Sign in' from the ⋮ menu"
                    )
                elif result.kind in coupon and allowed_kinds == admin_or_manager:
                    raise CredentialsError(
                        "B20900 this is a coupon code; please enter a login key "
                        " or seelct 'Enter a coupon code' from the ⋮ menu"
                    )
                else:
                    raise CredentialsError("B96593 invalid account kind")
        # FIXME: verify pubkey limit
        return result


###
### DB table 'base' - VPN base device
###


class Base(SQLModel, table=True):
    id: Optional[int] = Field(primary_key=True, default=None)
    account_id: int = Field(index=True, foreign_key='account.id')  # device admin--manager
    comment: str = ""

    @staticmethod
    def new(account_id):  # create a new base and return its id
        base = Base()
        base.account_id = account_id
        id = base.update()
        logger.info(f"Created new base {id}")
        return id

    def update(self):
        with Session(engine) as session:
            session.add(self)
            session.commit()
            return self.id


###
### DB table 'netif' - WireGuard network interface
###

wgif_prefix = 'fdfb'
reserved_ips = 38


class Netif(SQLModel, table=True):
    id: Optional[int] = Field(primary_key=True, default=None)
    base_id: Optional[int] = Field(index=True, foreign_key='base.id')  # base this netif is on
    ipv4_base: str
    ipv6_base: str
    privkey: str
    pubkey: str
    listening_port: int  # on LAN
    # use JSON because lists are not yet supported: https://github.com/tiangolo/sqlmodel/issues/178
    public_ports: list[int] = Field(sa_column=Column(JSON))  # on base's public IP
    comment: str = ""
    model_config = ConfigDict(arbitrary_types_allowed=True)  # for Column(JSON)

    def __init__(self):
        self.base_id = None
        # IPv4 base is 10. + random xx.xx. + 0
        self.ipv4_base = str(ipaddress.ip_address('10.0.0.0') + secrets.randbelow(2**16) * 2**8)
        # IPv6 base is prefix + 2 random groups + 5 0000 groups
        seven_groups = secrets.randbelow(2**32) * 2**80
        self.ipv6_base = str(ipaddress.ip_address(f'{wgif_prefix}::') + seven_groups)
        self.privkey = net.sudo_wg(['genkey'])
        self.pubkey = net.sudo_wg(['pubkey'], input=self.privkey)
        self.listening_port = 123
        self.public_ports = [123]

    def iface(self):
        return f'{wgif_prefix}{self.id}'  # interface name and Netif.id match

    def ipv4(self):
        # ending in '/32' feels cleaner but client can't ping, even if client uses
        # `ip address add dev wg0 10.110.169.40 peer 10.110.169.1`
        # fix seems to be `ip -4 route add .../18 dev wg0` on base or use '/18' below
        return str(ipaddress.ip_address(self.ipv4_base) + 1) + '/18'  # max 16000 clients

    def ipv6(self):
        return str(ipaddress.ip_address(self.ipv6_base) + 1) + '/114'  # max 16000 clients

    @staticmethod
    def startup():
        net.sudo_sysctl('net.ipv4.ip_forward=1')
        net.sudo_sysctl('net.ipv6.conf.all.forwarding=1')
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
        net.sudo_ip(['link', 'add', 'dev', wgif, 'type', 'wireguard'])
        net.sudo_ip(['link', 'set', 'mtu', '1420', 'up', 'dev', wgif])
        net.sudo_ip(['-4', 'address', 'add', 'dev', wgif, i.ipv4()])
        net.sudo_ip(['-6', 'address', 'add', 'dev', wgif, i.ipv6()])
        net.sudo_wg(['set', wgif, 'private-key', f'!FILE!{i.privkey}'])
        net.sudo_wg(['set', wgif, 'listen-port', str(i.listening_port)])
        net.sudo_iptables(
            '--append FORWARD'.split(' ')
            + f'--in-interface {wgif}'.split(' ')
            + '--jump ACCEPT'.split(' ')
        )
        net.sudo_iptables(
            '--table nat'.split(' ')
            + '--append POSTROUTING'.split(' ')
            + ['--out-interface', net.default_route_interface()]
            + '--jump MASQUERADE'.split(' ')
        )
        return i

    @staticmethod
    def delete_our_wgif(isShutdown):  # clean up wg network interfaces
        for s in re.split(r'(?:^|\n)interface:\s*', net.sudo_wg()):
            if s == '' or s == '\n':
                continue
            if_name = re.match(r'\S+', s).group(0)
            if if_name.startswith(wgif_prefix):  # if it was ours, it's safe to delete
                if isShutdown:
                    logger.debug(f"Removing wg interface {if_name}")
                else:
                    logger.warning(f"Removing abandoned wg interface {if_name}")
                net.sudo_ip(['link', 'del', 'dev', if_name])

    @staticmethod
    def shutdown():
        net.sudo_undo_iptables()
        Netif.delete_our_wgif(isShutdown=True)


###
### DB table 'client' - VPN client device
###


class Client(SQLModel, table=True):
    __table_args__ = (sqlalchemy.UniqueConstraint('pubkey'),)  # no 2 clients may share a key
    id: Optional[int] = Field(primary_key=True, default=None)
    netif_id: int = Field(foreign_key='netif.id')  # the base interface this client connects to
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
        net.sudo_wg(  # see https://www.man7.org/linux/man-pages/man8/wg.8.html
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
            raise CredentialsError("B64879 invalid pubkey length")
        if re.search(r'[^A-Za-z0-9/+=]', k):
            raise CredentialsError("B16042 invalid pubkey characters")

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


def simplify(obj):
    """Return a serializable version of a SQLModel class or structure containing a class.

    Example usage: json.dumps(simplify(data_structure))
    """
    if isinstance(obj, SQLModel):
        return simplify(obj.model_dump())
    if isinstance(obj, list):
        return [simplify(item) for item in obj]
    if isinstance(obj, dict):
        return {k: simplify(v) for k, v in obj.items()}
    return obj
