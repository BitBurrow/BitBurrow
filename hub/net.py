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


def has_file_changed(path, begin_watching=False):
    """Check if a file's time, size, etc. is the same as before.

    The first call for any given file should set begin_watching=True and will return
    None iff there is any error. Subsequent calls will return True iff any of the file's
    metadata has changed. For symlinks, the target file is used.
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
    return has_file_changed.stats[path] != path_stat


def connected_inbound_list(local_port):
    # net_connections docs: https://psutil.readthedocs.io/en/latest/index.html#psutil.net_connections
    conn_list = psutil.net_connections(kind='tcp')
    return [
        c.raddr.ip
        for c in conn_list
        if c.status == psutil.CONN_ESTABLISHED and c.laddr.port == local_port
    ]
