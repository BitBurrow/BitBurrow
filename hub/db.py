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
from sqlmodel import Field, Session, SQLModel, select, JSON, Column, Relationship, func
from typing import Optional
import hub.login_key as lk
import hub.net as net
from pydantic import ConfigDict
import hub.config as conf
import hub.util as util

Berror = util.Berror
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
    USER = 200  # can set up, edit, and delete clients for a specific intf_id on 1 base
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
    login: str = Field(index=True, default='')  # 'login' field is used like a username
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
    intf_id: Optional[int] = Field(foreign_key='intf.id')  # used only if kind == USER
    email: str = ""  # optional, allow login key reset
    comment: str = ""
    login_sessions: list['LoginSession'] = Relationship(back_populates='account')


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
            account.login = lk.generate_login_key(lk.login_len)
            try:
                session.add(account)
                session.commit()
            except sqlalchemy.exc.IntegrityError:
                session.rollback()
                if attempt > 20:
                    logger.warning(f"B09974 duplicate login {account.login} (retry {attempt})")
                continue
            else:
                break
        else:
            raise Berror(f"B00995 duplicate login {account.login} after {retry_max} attempts")
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
### DB table Device - VPN device
###


class Device(SQLModel, table=True):
    id: Optional[int] = Field(primary_key=True, default=None)
    account_id: Optional[int] = Field(index=True, foreign_key='account.id')  # device admin--manager
    name: str = ""
    comment: str = ""


def device_count(account_id: int) -> int:
    """Returns the number of devices associated with this account_id."""
    with Session(engine) as session:
        statement = select(func.count()).select_from(Device).where(Device.account_id == account_id)
        return session.exec(statement).one()


###
### DB table Intf - WireGuard network interface on a BitBurrow base or client
###

wgif_prefix = 'wgbb'


class IntfMethod(enum.Enum):  # method used to configure Wireguard
    NONE = 0  # disabled
    LOCAL = 10  # subprocess.run() commands with `sudo` directly on the machine running Python
    BASH = 20  # create a list of Bash shell commmands
    UCI = 30  # create a list of `uci` commands for OpenWrt
    CONF = 40  # create a WireGuard conf file


class Intf(SQLModel, table=True):
    id: Optional[int] = Field(primary_key=True, default=None)
    device_id: Optional[int] = Field(index=True, foreign_key='device.id')  # device this intf is on
    ipv4_base: str = ''  # ipaddress.ip_network() but without the subnet prefix
    ipv6_base: str = ''
    host_id: int = 0  # host portion of IP address, e.g. 1 for muti-peer; applies to IPv4 and IPv6
    host_bits: int = 0  # network size in bits; for IPv4, host_bits = 32 - subnet_prefix
    allowed_ipv4_subnet: int = 32  # our allowed IPs, i.e. AllowedIPs in Peer section of our peer
    allowed_ipv6_subnet: int = 128
    # 'base_intf_id' is our peer (server) on single-peer interfaces; otherwise None
    base_intf_id: Optional[int] = Field(index=True, foreign_key='intf.id')
    privkey: str
    pubkey: str
    backend_port: int | None = Field(default=None)
    # use JSON because lists are not yet supported: https://github.com/tiangolo/sqlmodel/issues/178
    frontend_ports: list[int] = Field(sa_column=Column(JSON))  # on base's public IP
    # 'other' is a dict of all other config options, official and custom
    other: dict[str, any] = Field(sa_column=Column(JSON), default_factory=dict)
    comment: str = ""
    default_method: IntfMethod = IntfMethod.NONE
    model_config = ConfigDict(arbitrary_types_allowed=True)  # for Column(JSON)

    def iface(self):
        if self.base_intf_id:  # single-peer, i.e. 'client'
            return f'{wgif_prefix}{self.base_intf_id}'  # match remote's interface name
        else:  # multi-peer
            return f'{wgif_prefix}{self.id}'  # interface name and Intf.id match

    def ipv4(self) -> str:  # e.g. 192.168.1.101
        return str(ipaddress.ip_address(self.ipv4_base) + self.host_id)

    def ipv4cidr(self) -> str:  # e.g. 192.168.1.101/24
        return f'{self.ipv4()}/{32-self.host_bits}'

    def ipv6(self) -> str:
        return str(ipaddress.ip_address(self.ipv6_base) + self.host_id)

    def ipv6cidr(self) -> str:
        return f'{self.ipv6()}/{128-self.host_bits}'

    def ipv4allowed(self) -> str:
        return f'{self.ipv4()}/{self.allowed_ipv4_subnet}'

    def ipv6allowed(self) -> str:
        return f'{self.ipv6()}/{self.allowed_ipv6_subnet}'


def new_intf(device_id: int, base_intf_id=None, base_is_hub: bool = False) -> int:
    """Create a new intf and return its id. For clients, set base_intf_id."""
    intf = Intf(device_id=device_id)
    if base_intf_id:  # single-peer, i.e. new 'client'
        assert base_is_hub == False
        with Session(engine) as session:  # copy 'client' network details from base_intf
            statement = select(Intf).where(Intf.id == base_intf_id)
            base_intf = session.exec(statement).one_or_none()
            intf.ipv4_base = base_intf.ipv4_base
            intf.ipv6_base = base_intf.ipv6_base
            intf.host_bits = base_intf.host_bits
        intf.allowed_ipv4_subnet = 32  # for now, don't allow client-to-client
        intf.allowed_ipv6_subnet = 128
    else:  # multi-peer
        intf.host_bits = 12  # default for new Intf rows; FIXME: use conf.get('wireguard.host_bits')
        if base_is_hub:  # this Intf is the very first one, used for the base connections to the hub
            intf.ipv4_base = str(  # 172. address will never conflict with 10. used on bases
                ipaddress.ip_network(
                    f'172.22.199.111/{32-intf.host_bits}', strict=False
                ).network_address
            )
            intf.ipv6_base = str(  # fc00:: address will never conflict with fd00:: used on bases
                ipaddress.ip_network(
                    f'fcbb:ac16:c76f::0/{128-intf.host_bits}', strict=False
                ).network_address
            )
        else:
            # Reserved IP addresses docs: https://en.wikipedia.org/wiki/Reserved_IP_addresses
            intf.ipv4_base = str(
                ipaddress.ip_address('10.0.0.0')
                + secrets.randbelow(2 ** (32 - 8 - intf.host_bits)) * 2**intf.host_bits
            )
            intf.ipv6_base = str(
                ipaddress.ip_address('fd00::')
                + secrets.randbelow(2 ** (128 - 96 - 8 - intf.host_bits))
                * 2 ** (intf.host_bits + 96)
            )
        intf.allowed_ipv4_subnet = 0
        intf.allowed_ipv6_subnet = 0
    intf.base_intf_id = base_intf_id
    intf.privkey = net.sudo_wg(['genkey'])
    intf.pubkey = net.sudo_wg(['pubkey'], input=intf.privkey)
    if base_is_hub:  # on the hub, use ports from config file
        intf.backend_port = conf.get('backend.wg_port')
        intf.frontend_ports = [conf.get('frontend.wg_port')]
        intf.default_method = IntfMethod.LOCAL
    else:
        if not base_intf_id:  # in-bound port needed only on multi-peer interfaces
            intf.backend_port = 123
            intf.frontend_ports = [123]
        intf.default_method = IntfMethod.UCI
    # now find an unused host_id in the network
    host_id_min = 39
    host_id_limit = 2**intf.host_bits - 1
    retry_max = 25
    with Session(engine) as session:
        if base_intf_id:  # single-peer, i.e. new 'client'
            for attempt in range(retry_max):
                try:
                    statement = select(Intf.host_id).where(
                        Intf.host_id >= host_id_min,
                        Intf.host_id < host_id_limit,
                        Intf.base_intf_id == base_intf_id,
                    )
                    used = set(session.exec(statement))
                    free = min(set(range(host_id_min, host_id_limit)) - used, default=None)
                    if free is None:
                        raise Berror(
                            f"B95195 no free IPs in range [{host_id_min}, {host_id_limit})"
                        )
                    intf.host_id = free
                    session.add(intf)
                    session.commit()
                except sqlalchemy.IntegrityError:
                    session.rollback()
                    continue
                else:
                    break
            else:
                raise Berror(f"B73650 failed to allocate unique host_id after {retry_max} retries")
        else:  # multi-peer
            intf.host_id = 1
            session.add(intf)
            session.commit()
        return intf.id


def get_conf(intf_id) -> tuple:
    """Return config details for one WireGuard interface on one device (a .conf file of data)."""
    interface = dict()
    peers = list()
    # WireGuard config file docs: https://git.zx2c4.com/wireguard-tools/about/src/man/wg-quick.8
    with Session(engine) as session:
        intf = session.exec(select(Intf).where(Intf.id == intf_id)).one()
        if intf.backend_port:
            interface['ListenPort'] = str(intf.backend_port)
        interface['PrivateKey'] = intf.privkey
        if intf.base_intf_id:  # single-peer
            interface['Address'] = f'{intf.ipv4allowed()},{intf.ipv6allowed()}'
        else:
            interface['Address'] = f'{intf.ipv4cidr()},{intf.ipv6cidr()}'
        if intf.other.get('DNS', None):
            interface['DNS'] = intf.other['DNS']
        interface['Name'] = intf.iface()  # non-standard conf
        if intf.base_intf_id:  # single-peer
            base = session.exec(select(Intf).where(Intf.id == intf.base_intf_id)).one()
            p = dict()
            p['PublicKey'] = base.pubkey
            aip4 = ipaddress.ip_network(f"{base.ipv4()}/{intf.allowed_ipv4_subnet}", strict=False)
            aip6 = ipaddress.ip_network(f"{base.ipv6()}/{intf.allowed_ipv6_subnet}", strict=False)
            p['AllowedIPs'] = f'{aip4},{aip6}'
            p['Endpoint'] = f'{conf.get('frontend.ips')[0]}:{base.frontend_ports[0]}'
            peers.append(p)
        else:  # for multi-peer, loop through them
            statement = select(Intf).where(Intf.base_intf_id == intf.id)
            for peer in session.exec(statement):
                p = dict()
                p['PublicKey'] = peer.pubkey
                p['AllowedIPs'] = f'{peer.ipv4allowed()},{peer.ipv6allowed()}'
                # Endpoint not needed on multi-peer end
                peers.append(p)
        if intf.id == 1:  # routing on the hub
            # FIXME: consider dict() → defaultdict(list) to allow multiple 'PostUp' entries
            interface['PostUp'] = (
                '''iptables --append FORWARD --in-interface %i --jump ACCEPT;'''
                + ''' iptables --table nat --append POSTROUTING --out-interface'''
                + r''' $(ip route get 1.0.0.0 |sed -n 's/.* dev \([^ ]*\).*/\1/p')'''
                + ''' --jump MASQUERADE'''
            )
        else:  # routing on Linux clients
            # from https://www.wireguard.com/netns/#improved-rule-based-routing
            fwmark = intf.id + 24274090
            table = fwmark + 59452585
            interface['PostUp'] = (
                f'wg set %i fwmark {fwmark};'
                + f''' ip route add default dev %i table {table};'''
                + f''' ip rule add not fwmark {fwmark} table {table};'''
                + f''' ip rule add table main suppress_prefixlength 0'''
            )
    return (interface, peers)


def get_conf_activate_peer(intf_id) -> tuple:
    """Return config details to update base (intf_id's peer) for connecting to intf_id."""
    interface = dict()
    peers = list()
    with Session(engine) as session:
        intf = session.exec(select(Intf).where(Intf.id == intf_id)).one()
        assert intf.base_intf_id is not None
        base = session.exec(select(Intf).where(Intf.id == intf.base_intf_id)).one()
        interface['Name'] = base.iface()
        p = dict()
        p['PublicKey'] = intf.pubkey
        p['AllowedIPs'] = f'{intf.ipv4allowed()},{intf.ipv6allowed()}'
        # Endpoint not needed on multi-peer end
        peers.append(p)
    return (interface, peers)


def methodize(conf: tuple[dict, list[dict]], method: IntfMethod = None) -> str:
    i, peers = conf  # interface, peers
    out = list()
    wgif = i['Name']

    def output(cmd):  # add to 'out' after line-wrapping long lines
        line_prefix = ''
        next_prefix = '#   ' if cmd[0] == '#' else '    '  # full-line comments
        while cmd:  # line-wrap lines over 50 characters where possible
            m = re.match(r'(.{5,50})(?=\s|$)', cmd)
            if not m:
                m = re.match(r'(.+?)(?=\s|$)', cmd)
            cpart = m.group(1)
            cmd = cmd[len(cpart) :].lstrip()
            out.append(f'{line_prefix}{cpart}{" \\" if cmd else ""}')
            line_prefix = next_prefix

    def do(cmd: str):
        if method == IntfMethod.LOCAL:
            cmd = cmd.replace(  # I'm sorry for this, but it works
                r'''$(ip route get 1.0.0.0 |sed -n 's/.* dev \([^ ]*\).*/\1/p')''',
                net.default_route_interface(),
            )
            cmds = cmd.split(' ')
            if cmds[0] == 'sysctl':
                net.sudo_sysctl(cmds[1:])
            elif cmds[0] == 'ip':
                net.sudo_ip(cmds[1:])
            elif cmds[0] == 'wg':
                net.sudo_wg(cmds[1:])
            elif cmds[0] == 'iptables':
                net.sudo_iptables(cmds[1:])
            elif cmds[0] == '#':
                pass
            else:
                assert False, f"B79592 unknown command {cmds[0]}"
        else:
            n = 4  # replace '!FILE!...' args with a temp file (4 is arbitrary)
            for m in re.finditer(r' !FILE!([^\s]+)(?=\s|$)', cmd):
                output(f'''TMPKEY{n}="$(mktemp)"''')
                output(f'''trap 'rm -f "$TMPKEY{n}"' EXIT''')  # for security, clean-up
                output(f'''printf '%s' {m.group(1)} >"$TMPKEY{n}"''')
                cmd = cmd.replace(m.group(0), f''' "$TMPKEY{n}"''')
                n += 1
            output(cmd)

    if method == IntfMethod.CONF:
        out.append('[Interface]')
        for k, v in i.items():
            out.append(f'{k} = {v}')
        for p in peers:
            out.append('')
            out.append('[Peer]')
            for k, v in p.items():
                out.append(f'{k} = {v}')
    elif method == IntfMethod.LOCAL or method == IntfMethod.BASH:
        # configure WireGuard interface; see `systemctl status wg-quick@wg0.service`
        # do(f'sudo apt install -y wireguard')
        if addresses := i.get('Address', None):  # missing when activating peers
            do(f'sysctl net.ipv4.ip_forward=1')
            do(f'sysctl net.ipv6.conf.all.forwarding=1')
            addr = addresses.split(',')
            assert len(addr) == 2
            do(f'ip link add dev {wgif} type wireguard')
            do(f'ip link set mtu 1420 up dev {wgif}')
            do(f'ip -4 address add dev {wgif} {addr[0]}')
            do(f'ip -6 address add dev {wgif} {addr[1]}')
            do(f'wg set {wgif} private-key !FILE!{i['PrivateKey']}')
            if listen_port := i.get('ListenPort', None):
                do(f'wg set {wgif} listen-port {listen_port}')
            # do('''LAN_DEV=$(ip route show default |head -1 |awk '{print $5}')''')
            # do(f'iptables -A FORWARD -i {wgif} -j ACCEPT')
            # do('iptables -t nat -A POSTROUTING -o $LAN_DEV -j MASQUERADE')
        if postup := i.get('PostUp', None):
            for cmd in re.findall(r'[^;\s][^;]*', postup):  # split at semicolons
                # replace each '%i' with wgif
                do(re.sub(r'( )%i(?=\s|$)', lambda m: m.group(1) + wgif, cmd))
        for p in peers:  # configured peers
            # docs: https://www.man7.org/linux/man-pages/man8/wg.8.html
            # consider: + f' preshared-key !FILE!(peer['PresharedKey'])}'
            # consider: + f' persistent-keepalive {peer['PersistentKeepalive']}'
            peer_cmd = f'wg set {wgif} peer {p['PublicKey']} allowed-ips {p['AllowedIPs']}'
            if endpoint := p.get('Endpoint', None):
                peer_cmd += f' endpoint {endpoint}'  # 1 space at start, 0 at end
            do(peer_cmd)
        if addresses:  # when not just activating peers
            do(f'ip link set up dev {wgif}')
            do(
                f'''# DISCONNECT: ip rule del table main suppress_prefixlength 0;'''
                + f''' ip link del dev {wgif}'''
            )
    elif method == IntfMethod.UCI:
        # do(f'opkg update')  # don't do if `wg` is already installed
        # do(f'opkg install wireguard-tools')
        if addresses := i.get('Address', None):  # missing when activating peers
            addr = addresses.split(',')
            assert len(addr) == 2
            do(f'uci set network.{wgif}=interface')
            do(f'uci set network.{wgif}.proto=wireguard')
            do(f'uci set network.{wgif}.private_key={i['PrivateKey']}')
            do(f'uci add_list network.{wgif}.addresses="{addr[0]}"')
            do(f'uci add_list network.{wgif}.addresses="{addr[1]}"')
            if listen_port := i.get('ListenPort', None):
                do(f'uci set network.{wgif}.listen_port={listen_port}')
        for p in peers:  # configured peers
            do(f'PEER_ID="$(uci add network wireguard_{wgif})"')
            do(f'uci set network.$PEER_ID.public_key={p['PublicKey']}')
            do(f'uci add_list network.$PEER_ID.allowed_ips={p['AllowedIPs']}')
            if endpoint := p.get('Endpoint', None):
                host, port = endpoint.rsplit(':', 1)
                do(f'uci set network.$PEER_ID.endpoint_host={host}')
                do(f'uci set network.$PEER_ID.endpoint_port={port}')
            if preshared := p.get('PresharedKey', None):
                do(f'uci set network.$PEER_ID.preshared_key={preshared}')
            if keepalive := p.get('PersistentKeepalive', None):
                do(f'uci set network.$PEER_ID.persistent_keepalive={keepalive}')
        # do('uci commit network')  # no need to write to permanent storage
        do('/etc/init.d/network reload')
        # DISCONNECT:
        # uci delete network.wgbb1
        # for s in $(uci show network |grep "=wireguard_wgbb1" |cut -d. -f2 |cut -d= -f1); do uci delete network.$s; done
        # /etc/init.d/network reload
    else:
        raise Berror(f"B10323 unknown IntfMethod {method}")
    return '\n'.join(out) + '\n'


def new_device(account_id, is_base=True) -> int:
    total_devices = device_count(account_id) + 1
    with Session(engine) as session:
        device = Device(
            account_id=account_id,
            name=f'{"Base" if is_base else "Device"} {total_devices}',
        )
        session.add(device)
        session.commit()
        if is_base:  # create WireGuard connection between the new device and the hub
            hub_peer_id = new_intf(device_id=device.id, base_intf_id=1)
            hub_peer_conf = get_conf_activate_peer(hub_peer_id)
            methodize(hub_peer_conf, IntfMethod.LOCAL)
            logger.info(f"Created new base: {device.name}")
    return device.id


def hub_peer_id(device_id) -> int | None:
    """Returns the id of the intf on the device which connects to the hub, or None if unmanaged."""
    with Session(engine) as session:
        try:
            intf = session.exec(
                select(Intf).where(
                    Intf.device_id == device_id,
                    Intf.base_intf_id == 1,
                )
            ).one()
            return intf.id
        except (sqlalchemy.exc.NoResultFound, sqlalchemy.exc.MultipleResultsFound):
            return None


def on_startup() -> None:
    """If the Device table is empty, create our BitBurrow hub device, WireGuard interface."""
    delete_our_wgif(isShutdown=False)
    with Session(engine) as session:
        hub_device_exists = session.exec(select(sqlalchemy.exists().where(Device.id != None))).one()
        if not hub_device_exists:  # hub Device
            hub_device = Device(account_id=None, name="BitBurrow hub")
            session.add(hub_device)
            session.commit()
            assert hub_device.id == 1, f"B12466 unexpected {hub_device.id=}"
        if not hub_device_exists:  # hub Intf
            hub_intf_id = new_intf(hub_device.id, base_is_hub=True)
            assert hub_intf_id == 1, f"B49296 unexpected {hub_intf_id=}"
    hub_conf = get_conf(intf_id=1)
    methodize(hub_conf, method=IntfMethod.LOCAL)


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


def on_shutdown():
    net.sudo_undo_iptables()
    delete_our_wgif(isShutdown=True)


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
