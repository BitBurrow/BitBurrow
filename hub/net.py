import asyncio
import urllib.request
from dateutil import parser
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
import logging
import os
import psutil
import re
import secrets
import socket
import subprocess
import tempfile
import hub.util as util

Berror = util.Berror

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


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


def ip_route_get(item: str):  # get default route
    droute = ip(['route', 'get', '1.0.0.0'])
    # example output: 1.0.0.0 via 192.168.8.1 dev wlp58s0 src 192.168.8.101 uid 1000
    value_portion = re.search(r'\s' + item + r'\s(\S+)', droute)
    assert value_portion is not None, f"ip route returned: {droute}"
    return value_portion[1]


def default_route_interface():  # network interface of default route
    # via CLI: ip route get 1.0.0.0 |sed -n 's/.* dev \([^ ]*\).*/\1/p'
    return ip_route_get('dev')


def default_route_local_ip():  # local IP address of default route
    return ip_route_get('src')


def default_route_gateway():  # default gateway IP adddress
    return ip_route_get('via')


def all_local_ips(wildcard_address, ipv6_enclosure='[]', include_link_local=False):
    # set of IPs for '0.0.0.0', '::0'
    if wildcard_address == '':
        ip_versions = ['-4', '-6']
    elif wildcard_address == '0.0.0.0':
        ip_versions = ['-4']
    elif wildcard_address == '::0':
        ip_versions = ['-6']
    else:
        return {wildcard_address}
    address_set = set()
    for ip_version in ip_versions:
        ip_stdout = ip([ip_version, '-oneline', 'addr', 'show', 'up'])
        for line in ip_stdout.splitlines():
            if ' lo ' in line:
                continue
            if any(flag in line for flag in ('tentative', 'dadfailed', 'deprecated')):
                continue
            m = re.search(r'\sinet6?\s([0-9a-fA-F\.:]+)/(?:\d+)', line)
            if not m:
                continue
            address = m.group(1)
            if address.startswith('127.'):
                continue
            if address == '::1':
                continue
            if ':' in address and not include_link_local and address.lower().startswith('fe80:'):
                continue
            if ip_version == '-6' and len(ipv6_enclosure) == 2:
                address_set.add(ipv6_enclosure[0] + address + ipv6_enclosure[1])
            else:
                address_set.add(address)
    return address_set


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


def run_external(args: list[str], input: str | None = None):
    """Run an external executable, capturing output. Searches standard system directories.

    Return stdout or raises RuntimeError, depending on the return code."""
    logger.debug(f"running: {arg_string(args)}")
    exec_count = 2 if args[0] == 'sudo' else 1
    for i, a in enumerate(args[:exec_count]):  # e.g. expand 'wg' to '/usr/bin/wg'
        for p in '/usr/sbin:/usr/bin:/sbin:/bin:~/.local/bin'.split(':'):
            joined = os.path.join(p.replace('~', os.path.expanduser('~')), a)
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
        error = proc.stderr.decode().rstrip() or proc.stdout.decode().rstrip()
        if '' in args:
            raise Berror(f"B74063 {error} probably caused by empty arg in {arg_string(args)}")
        raise Berror(f"B57012 {arg_string(args)} failed: {error}")
    return proc.stdout.decode().rstrip()


async def run_external_async(args: list[str]):
    """Run an external executable, capturing output. Searches standard system directories.

    Return stdout or raises RuntimeError, depending on the return code."""
    exec_count = 2 if args[0] == 'sudo' else 1
    for i, a in enumerate(args[:exec_count]):  # e.g. expand 'wg' to '/usr/bin/wg'
        for p in '/usr/sbin:/usr/bin:/sbin:/bin:~/.local/bin'.split(':'):
            joined = os.path.join(p.replace('~', os.path.expanduser('~')), a)
            if os.path.isfile(joined):
                args[i] = joined
                break
    # docs: https://docs.python.org/3/library/asyncio-subprocess.html#creating-subprocesses
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = f"{arg_string(args)}\n"
    output += ''.join([f"    1: {line}\n" for line in stdout.decode().splitlines()])
    output += ''.join([f"    2: {line}\n" for line in stderr.decode().splitlines()])
    if proc.returncode != 0:
        raise RuntimeError(output).rstrip()
    return output.rstrip()


def arg_string(args):
    joined = '␣'.join(args)  # alternatives: ␣⋄∘•⁕⁔⁃–
    return joined if len(joined) < 170 else joined[:168] + "…"


async def check_tls_cert(external: str, internal: str = None):
    """Check the TLS certificate for the given website; log warnings as needed.

    external is {host}:{port} for to check, e.g. example.org:443
    internal is an alternate way to reach the same site to verify it is the same cert"""
    try:
        e = external.split(':', 1)  # 'example.org:443'
        ectx = urllib.request.ssl.create_default_context()
        ereader, ewriter = await asyncio.wait_for(
            asyncio.open_connection(e[0], e[1], ssl=ectx, server_hostname=e[0]),
            timeout=5.0,
        )
        essock = ewriter.get_extra_info("ssl_object")
        external_sn = essock.getpeercert()['serialNumber']
        expires_on = parser.parse(essock.getpeercert()["notAfter"])
        remaining = expires_on - DateTime.now(TimeZone.utc)
        days_remaining = int(remaining.total_seconds()) // (60 * 60 * 24)
        message = f"B44051 TLS certificate expires in {days_remaining} days"
        # cert should auto-renew 30 days (https://community.letsencrypt.org/t/-/184567)
        # before expiration, so under 27 means it has failed multiple times
        if days_remaining < 27:
            logger.warning("B41229 cert failed to renew; see logs in /var/log/letsencrypt/")
            logger.warning(message)
            # FIXME: alert administrator if < 20 days
        elif days_remaining < 40:
            logger.info(message)
            logger.info("B72030 TLS certificate should automatically renew at 30 days")
        else:
            logger.debug(message)
        if internal:
            try:
                i = internal.split(':', 1)
                ictx = urllib.request.ssl.create_default_context()
                ireader, iwriter = await asyncio.wait_for(
                    asyncio.open_connection(i[0], i[1], ssl=ictx, server_hostname=e[0]),
                    timeout=5.0,
                )
                issock = iwriter.get_extra_info("ssl_object")
                internal_sn = issock.getpeercert()['serialNumber']
                if internal_sn != external_sn:
                    logger.error(f"B32321 TLS certs at {external} and {internal} are different")
            except urllib.request.ssl.SSLCertVerificationError:
                logger.error(f"B25688 TLS certificate at {internal} is not valid for {e[0]}")
        return
    except TimeoutError as e:
        logger.warning("B43166 connection timed out in check_tls_cert()")
    except urllib.request.ssl.SSLCertVerificationError:
        logger.error(f"B93900 TLS certificate at {external} is not valid for {e[0]}")
    except Exception as e:
        # socket.gaierror, ConnectionRefusedError, ConnectionResetError, ssl.SSLCertVerificationError, etc.
        logger.error(f"B44182 {str(e)} ({type(e)}) while checking TLS cert")


def watch_file(path):
    return has_file_changed(path, begin_watching=True)


def has_file_changed(path, begin_watching=False, max_items=9) -> bool | None:
    """Check if a file's last-modified time and size are the same as before.

    The first call for any given file should set begin_watching=True and will return
    None iff there is any error. Subsequent calls will return diffs if any of the file's
    metadata has changed, otherwise False. The diffs returned is a human-readable string
    which only lists metadata that has changed. For symlinks, the target file is used.
    """
    if not hasattr(has_file_changed, 'stats'):
        has_file_changed.stats = dict()
    try:
        path_stat = os.stat(path)
    except FileNotFoundError:
        logger.error(f"B65354 File {path} is missing.")
        return None
    if not os.access(path, os.R_OK):
        logger.error(f"B36638 File {path} is unreadable.")
        return None
    if begin_watching:
        has_file_changed.stats[path] = path_stat
        return False
    if (
        has_file_changed.stats[path].st_size == path_stat.st_size
        and has_file_changed.stats[path].st_mtime == path_stat.st_mtime
    ):
        return False
    else:
        return stat_diff(has_file_changed.stats[path], path_stat, max_items)


def stat_diff(a, b, max_items, size_mtime_only=True):
    diff = list()
    if a.st_size != b.st_size:
        diff.append(f"size {a.st_size} → {b.st_size}")
    if a.st_mtime != b.st_mtime:
        diff.append(f"mtime {time_string(a.st_mtime)} → {time_string(b.st_mtime)}")
    if size_mtime_only == False:
        if a.st_mode != b.st_mode:
            diff.append(f"mode {oct(a.st_mode)} → {oct(b.st_mode)}")
        if a.st_atime != b.st_atime:
            diff.append(f"atime {time_string(a.st_atime)} → {time_string(b.st_atime)}")
        if a.st_ctime != b.st_ctime:
            diff.append(f"ctime {time_string(a.st_ctime)} → {time_string(b.st_ctime)}")
    if len(diff) > max_items:
        return "; ".join(diff[0:max_items])
    else:
        return "; ".join(diff)


def time_string(t):
    return DateTime.utcfromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def connected_inbound_list(local_port):
    # net_connections docs: https://psutil.readthedocs.io/en/latest/index.html#psutil.net_connections
    conn_list = psutil.net_connections(kind='tcp')
    return [
        c.raddr.ip
        for c in conn_list
        if c.status == psutil.CONN_ESTABLISHED and c.laddr.port == local_port
    ]


def default_listen_address(ip: str) -> str:
    if ip == '':
        return 'localhost'
    if ip == '0.0.0.0':
        return '127.0.0.1'
    if ip == '::0' or ip == '::':
        return '::1'
    return ip
