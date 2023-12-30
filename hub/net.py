import urllib.request
from dateutil import parser
from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
import logging
import os
import psutil

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


def check_tls_cert(site, port):
    """Checks the TLS certificate for the given website; logs warnings as needed.

    Returns the number of days validity remaining, or -1 for errors.
    """
    # based on https://stackoverflow.com/a/52575489
    context = urllib.request.ssl.create_default_context()
    try:
        with urllib.request.socket.create_connection((site, port)) as sock:
            with context.wrap_socket(sock, server_hostname=site) as ssock:
                expires_on = parser.parse(ssock.getpeercert()["notAfter"])
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
        return days_remaining
    except TimeoutError as e:
        return -1  # 'Connection timed out', e.g. after `sudo iptables -A OUTPUT -j DROP`
    except Exception as e:
        # socket.gaierror, ConnectionRefusedError, ConnectionResetError, ssl.SSLCertVerificationError, etc.
        logger.error(f"B44182 {str(e)} ({type(e)}) while checking TLS cert")
        return -1


def watch_file(path):
    return has_file_changed(path, begin_watching=True)


def has_file_changed(path, begin_watching=False, max_items=9):
    """Check if a file's time, size, etc. is the same as before.

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
    if has_file_changed.stats[path] == path_stat:
        return False
    else:
        return stat_diff(has_file_changed.stats[path], path_stat, max_items)


def stat_diff(a, b, max_items):
    diff = list()
    if a.st_size != b.st_size:
        diff.append(f"size {a.st_size} → {b.st_size}")
    if a.st_mode != b.st_mode:
        diff.append(f"mode {oct(a.st_mode)} → {oct(b.st_mode)}")
    if a.st_mtime != b.st_mtime:
        diff.append(f"mtime {time_string(a.st_mtime)} → {time_string(b.st_mtime)}")
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
