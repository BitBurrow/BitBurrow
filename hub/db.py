import argon2
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
import enum
import fastapi
import hashlib
import ipaddress
import logging
import re
import secrets
import sqlalchemy
import sqlalchemy.engine
import sqlite3
from sqlmodel import Field, Session, SQLModel, select, JSON, Column, Relationship
from typing import List, Optional
import hub.login_key as lk
import hub.net as net
from pydantic import ConfigDict

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)
engine = None


@sqlalchemy.event.listens_for(sqlalchemy.engine.Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Set 'PRAGMA foreign_keys=ON' for, e.g. LoginSession.account"""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()


class CredentialsError(Exception):
    pass


###
### DB table Account - an administrative login, coupon code, manager, or user
###


class AccountKind(enum.Enum):
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
            0: "disabled account",
        }
        try:
            return str_map[self.value]
        except:
            pass
        return f"kind_{self.value}"


admin_or_manager = {AccountKind.ADMIN, AccountKind.MANAGER}
coupon = {AccountKind.COUPON}
admin_manager_or_coupon = {AccountKind.ADMIN, AccountKind.MANAGER, AccountKind.COUPON}
lkocc_string = '__login_key_or_coupon_code__'


class Account(SQLModel, table=True):
    __table_args__ = (sqlalchemy.UniqueConstraint('login'),)  # must have a unique login
    id: Optional[int] = Field(primary_key=True, default=None)
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
        default=DateTime(1970, 1, 1, tzinfo=TimeZone.utc),  # Unix epoc
    )
    kind: AccountKind = AccountKind.NONE
    parent_id: int = 0  # id of Account that created this one, e.g. coupon code
    netif_id: Optional[int] = Field(foreign_key='netif.id')  # used only if kind == USER
    email: str = ""  # optional, allow login key reset
    comment: str = ""
    login_sessions: List['LoginSession'] = Relationship(back_populates='account')


def account_count(account_kind=AccountKind.NONE):
    with Session(engine) as session:
        if account_kind == AccountKind.NONE:
            return session.query(Account).count()
        return session.query(Account).filter(Account.kind == account_kind).count()


def new_account(kind: AccountKind, valid_for=TimeDelta(days=10950), parent_account_id=0):
    """Create a new account and return its login key."""
    account = Account()
    key = lk.generate_login_key(lk.key_len)
    hasher = argon2.PasswordHasher()
    account.key_hash = hasher.hash(key)
    if kind == AccountKind.ADMIN:
        account.clients_max = 0  # admins cannot create VPN clients
    account.kind = kind
    account.valid_until = DateTime.now(TimeZone.utc) + valid_for
    account.parent_id = parent_account_id
    retry_max = 50
    with Session(engine) as session:
        for attempt in range(retry_max):
            try:
                session.add(account)
                session.commit()
            except sqlalchemy.exc.IntegrityError:
                if attempt > 20:
                    logger.warning(f"B09974 duplicate login {account.login} (retry {attempt})")
                account.login = lk.generate_login_key(lk.login_len)
                continue
            else:
                break
        else:
            raise RuntimeError(f"B00995 duplicate login {account.login} after {retry_max} attempts")
        login_key = account.login + key
    logger.info(f"Created new {kind.token_name()} {login_key}")
    return login_key


def update_account(
    login: str = None,  # look up account by login
    key: str = None,
    kind: AccountKind | None = None,
    valid_for: TimeDelta | None = None,
) -> int:
    """Update specified account fields. Return the account id."""
    with Session(engine) as session:
        statement = select(Account).where(Account.login == login_portion(login))
        account = session.exec(statement).one_or_none()
        if not account:
            raise CredentialsError(f"B33092 cannot find account {login_portion(login)}")
        if key != None:
            hasher = argon2.PasswordHasher()
            account.key_hash = hasher.hash(key)
        if kind != None:
            account.kind = kind
        if valid_for != None:
            account.valid_until = DateTime.now(TimeZone.utc) + valid_for
        session.add(account)
        session.commit()
        return account.id


def login_portion(login_key):
    return login_key[0 : lk.login_len]


def key_portion(login_key):
    return login_key[lk.login_len :]


def validate_login_key(login_key, allowed_kinds=None) -> int:
    """Verify the login key. Return the account.id or raise CredentialsError."""
    if len(login_key) != lk.login_key_len:
        raise CredentialsError(f"B64292 {lkocc_string} length must be {lk.login_key_len}")
    if not set(lk.base28_digits).issuperset(login_key):
        raise CredentialsError(f"B51850 invalid {lkocc_string} characters")
    with Session(engine) as session:
        statement = select(Account).where(Account.login == login_portion(login_key))
        account = session.exec(statement).one_or_none()
        if account is None:
            # attempt near constant-time key checking whether login exsists or not
            # https://chatgpt.com/share/68812be3-5f14-800d-ba89-55d5914881d9
            key_hash_to_test = '$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        else:
            key_hash_to_test = account.key_hash
        key = key_portion(login_key)
        hasher = argon2.PasswordHasher()
        try:
            hasher.verify(key_hash_to_test, key)
        except argon2.exceptions.VerifyMismatchError:
            raise CredentialsError(
                f"B54441 {lkocc_string} not found; " "make sure it was entered correctly"
            )
        if hasher.check_needs_rehash(key_hash_to_test):
            account.key_hash = hasher.hash(key)  # FIXME: untested
            session.add(account)
            session.commit()
            logger.info("B74657 rehashed {login_key}")
        if account.valid_until.replace(tzinfo=TimeZone.utc) < DateTime.now(TimeZone.utc):
            raise CredentialsError(f"B18952 {lkocc_string} expired")
        if allowed_kinds is not None:
            if account.kind not in allowed_kinds:
                if account.kind in admin_or_manager and allowed_kinds == coupon:
                    raise CredentialsError(
                        "B10052 this is a login key; please enter a coupon code "
                        "or select 'Sign in' from the ⋮ menu"
                    )
                elif account.kind in coupon and allowed_kinds == admin_or_manager:
                    raise CredentialsError(
                        "B20900 this is a coupon code; please enter a login key "
                        " or seelct 'Enter a coupon code' from the ⋮ menu"
                    )
                else:
                    raise CredentialsError("B96593 invalid account kind")
        # FIXME: verify pubkey limit
        return account.id


def get_account_by_token(token: str | None, log_out=False) -> tuple[int, int, AccountKind]:
    """Validate a client token.

    Optionally invalidate the login session. Return the LoginSession.id
    and the account.id and the associated account.kind. Raises CredentialsError
    if validation fails."""
    if not token:
        raise CredentialsError(f"B73962 missing token")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with Session(engine) as session:
        login_session = session.exec(
            select(LoginSession).where(LoginSession.token_hash == token_hash)
        ).one_or_none()
        if not login_session:
            raise CredentialsError(f"B05076 login session not found")
        now = DateTime.now(TimeZone.utc)
        if login_session.valid_until.replace(tzinfo=TimeZone.utc) < now:
            raise CredentialsError(f"B90836 login session no longer valid")
        login_session.last_activity = now
        if log_out:
            login_session.valid_until = now
        session.add(login_session)
        session.commit()
        return login_session.id, login_session.account_id, login_session.account.kind


###
### DB table LoginSession - user log-in sessions
###


class LoginSession(SQLModel, table=True):
    __table_args__ = (
        sqlalchemy.Index("idx_sessions_valid_until", "valid_until"),
        sqlalchemy.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    id: Optional[int] = Field(primary_key=True, default=None)
    account_id: int = Field(foreign_key='account.id')
    token_hash: str = ''
    created_at: DateTime = Field(
        sa_column=Column(sqlalchemy.DateTime(timezone=True)),
        default_factory=lambda: DateTime.now(TimeZone.utc),
    )
    last_activity: DateTime = Field(
        sa_column=Column(sqlalchemy.DateTime(timezone=True)),
        default_factory=lambda: DateTime.now(TimeZone.utc),
    )
    valid_until: DateTime = Field(
        sa_column=Column(sqlalchemy.DateTime(timezone=True)),
        default_factory=lambda: DateTime.now(TimeZone.utc) + TimeDelta(days=1),
    )
    account: Optional[Account] = Relationship(back_populates="login_sessions")
    ip: str
    user_agent: str


def new_login_session(aid: int, request: fastapi.Request, valid_for: TimeDelta) -> str:
    """Create a new session and return its token."""
    ls = LoginSession()
    ls.account_id = aid  # account.id
    token = secrets.token_urlsafe(32)  # 256-bit random token
    ls.token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    ls.valid_until = DateTime.now(TimeZone.utc) + valid_for
    ls.ip = request.client.host if request.client else '0.0.0.0'
    ls.user_agent = request.headers.get('User-Agent', '')
    with Session(engine) as session:
        session.add(ls)
        session.commit()
    logger.info(f"B81232 created new login session for account {aid}")
    return token


def log_out(lsid: int) -> None:
    """Log out (invalidate) the given login session."""
    with Session(engine) as session:
        ls = session.exec(select(LoginSession).where(LoginSession.id == lsid)).first()
        if ls:
            now = DateTime.now(TimeZone.utc)
            ls.last_activity = now
            ls.valid_until = now
            session.add(ls)
            session.commit()


def iter_get_login_session_by_account_id(aid: int | None):
    """Yield each login_session for account aid."""
    with Session(engine) as session:
        if aid is None:
            statement = select(LoginSession)
        else:
            statement = select(LoginSession).where(LoginSession.account_id == aid)
        for row in session.exec(statement):
            yield row


###
### DB table Base - VPN base device
###


class Base(SQLModel, table=True):
    id: Optional[int] = Field(primary_key=True, default=None)
    account_id: int = Field(index=True, foreign_key='account.id')  # device admin--manager
    comment: str = ""


def new_base(account_id):  # create a new base and return its id
    base = Base()
    base.account_id = account_id
    with Session(engine) as session:
        session.add(base)
        session.commit()
    logger.info(f"Created new base {id}")
    return id


###
### DB table Netif - WireGuard network interface
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


def startup_netif():
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
    delete_our_wgif(isShutdown=False)
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


def shutdown_netif():
    net.sudo_undo_iptables()
    delete_our_wgif(isShutdown=True)


###
### DB table Client - VPN client device
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


def validate_pubkey(k):
    if not (42 <= len(k) < 72):
        raise CredentialsError("B64879 invalid pubkey length")
    if re.search(r'[^A-Za-z0-9/+=]', k):
        raise CredentialsError("B16042 invalid pubkey characters")


def startup_client(wgif):
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
