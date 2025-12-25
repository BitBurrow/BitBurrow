import base64
import gzip
import logging
import importlib.metadata
import io
import os
import re
import textwrap
import yaml
import hub.config as conf
import hub.net as net

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


class Berror(Exception):
    """Raise for probably-fatal errors (calling method can decide). Include a Berror code
    (https://bitburrow.com/hub/#berror-codes) and any potentially useful details.
    """

    pass


def rotate_backups(file_path: str, prefile_path: str, max_versions: int = 9):
    """Rotate file backups e.g. name.1.txt → name.2.txt and then name.txt → name.1.txt (via
    hard link for atomic replacement) and finally pre.txt → name.txt. The file_path and
    prefile_path must both exist and be in the same directory."""
    assert os.path.dirname(file_path) == os.path.dirname(prefile_path)
    assert max_versions >= 1
    base, ext = os.path.splitext(file_path)
    for v in range(max_versions, 0, -1):
        dst = f'{base}.{v}{ext}'
        if v > 1:
            src = f'{base}.{v-1}{ext}'
            try:
                os.replace(src, dst)  # mv name.8.txt name.9.txt
            except FileNotFoundError:
                pass
        else:  # last pair: carefully move prefile into its new place
            try:
                os.remove(dst)  # should be gone, but let's be sure
            except FileNotFoundError:
                pass
            os.link(file_path, dst)  # ln name.txt name.1.txt  # hard link so migration is atomic
            os.replace(prefile_path, file_path)  # mv name-EBBWIL.txt name.txt


def app_version() -> str:
    try:
        return importlib.metadata.version("bitburrow")
    except importlib.metadata.PackageNotFoundError:
        return '(unknown)'


integrity_tests_yaml = r'''
# note: the ending "|keep_only 'regex'" is similar to CLI "|grep -o 'regex'" with Python's re
# note: needs r-triplle-quoted string for regex after keep_only
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
# note: below, `dig A {domain} +short` works too (for most set-ups), but going directly
#     to the nameserver feels better and works for a server behind NAT connected via VPN
# note: we assume that the parent domain *is* the nameserver
- id: global_a
  cmd: dig @{parent_domain} A {domain} +short
  expected: '{public_ip}'
# global NS record
- id: global_ns
  cmd: dig NS {domain} +short
  expected: '{domain}.'
# ensure BIND recursive resolver is disabled
- id: bind_recursive_off
  cmd: dig @{public_ip} A google.com +short
  expected: ''
# ensure zone transfer is disabled (list of VPN bases should not be public)
- id: bind_axfr_off
  cmd: dig @{public_ip} {domain} AXFR +short
  expected: '; Transfer failed.'
## Pytest code tests--must be done with full source because 'tests/' doesn't get installed
#- id: pytest
#  cmd: pytest
#  expected: '=====================...'

'''
integrity_tests = yaml.safe_load(integrity_tests_yaml)


def integrity_test(test):
    domain = conf.get('frontend.domain')
    public_ip = conf.get('frontend.ips')[0]
    cmd = test['cmd'].format(  # substitute {domain} for the actual domain, etc.
        domain=domain,
        parent_domain=domain.partition('.')[2],
        public_ip=public_ip,
    )
    keep_only = None  # support syntax at end of cmd similar to `grep -o`
    keep_only_search = re.search(r'^(.*?)\s*\|\s*keep_only\s+["\']([^"\']+)["\']$', cmd)
    if keep_only_search != None:
        cmd = keep_only_search.group(1)
        keep_only = keep_only_search.group(2)
    expected = test['expected'].format(domain=domain, public_ip=public_ip)
    try:
        result = net.run_external(cmd.split(' '))
    except RuntimeError as e:
        # result = str(e).replace('\n', ' ¶ ')
        result = str(e)
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


def integrity_test_by_id(test_id):
    # returns True only if there are no failed tests
    pass_count = 0
    failed_count = 0
    for t in integrity_tests:
        if (
            test_id == 'all'
            or test_id == t['id']
            or (test_id == 'dig' and t['cmd'].startswith('dig '))
        ):
            if integrity_test(t):
                pass_count += 1
            else:
                failed_count += 1
    if pass_count + failed_count == 0:
        logger.warning(f"B37855 nonexistent test ID {test_id}")
    else:
        logger.info(f"B28874 {pass_count} of {pass_count + failed_count} tests passed")
    return failed_count == 0


def gzip_base64(s: str, wrap: int = 76, prefix: str = '', postfix: str = '\n') -> str:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9) as f:
        f.write(s.encode("utf-8"))
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return ''.join([f'{prefix}{b64[i:i+wrap]}{postfix}' for i in range(0, len(b64), wrap)])


def slugify(s: str, *, max_len: int = 32) -> str:
    s = re.sub(r'[&+@]', lambda m: {'&': ' and ', '+': ' plus ', '@': ' at '}[m[0]], s)
    s = re.sub(r'[^a-z0-9]+', '-', s.lower())
    s = re.sub(r'-{2,}', '-', s)
    return s[:max_len].strip('-')


def port_forward_script():  # called from preinstall.sh
    using_tls_proxy = (
        conf.get('frontend.web_proto') == 'https' and conf.get('backend.web_proto') == 'http'
    )
    script = f'''
        #!/bin/bash
        ##
        vmname={net.run_external('hostname', '--short')}
        using_tls_proxy={'true' if using_tls_proxy else 'false'}
        ##
        ## Configure port forwarding from host to container for BitBurrow hub
        ##
        if [[ $using_tls_proxy != true ]]; do
            lxc config device add $vmname web_port proxy \\
                listen=tcp:0.0.0.0:{conf.get('frontend.web_port')} \\
                connect=tcp:127.0.0.1:{conf.get('backend.web_port')}
        fi
        lxc config device add $vmname wgport proxy \\
            listen=udp:0.0.0.0:{conf.get('frontend.wg_port')} \\
            connect=udp:127.0.0.1:{conf.get('backend.wg_port')}
        lxc config device add $vmname udpdns proxy \\
            listen=udp:{conf.get('frontend.ips')[0]}:53 connect=udp:127.0.0.1:53
        lxc config device add $vmname tcpdns proxy \\
            listen=tcp:{conf.get('frontend.ips')[0]}:53 connect=tcp:127.0.0.1:53
        ##
        ## Allow tracking of WireGuard connection IPs for DDNS (also logging of client IP
        ## addresses; otherwise all connections appear to be from 127.0.0.1
        ##
        # from https://discuss.linuxcontainers.org/t/making-sure-that-ips-connected-to-the-containers-gameserver-proxy-shows-users-real-ip/8032/5
        vmip=$(lxc list $vmname -c4 --format=csv |grep -o '^\S*')
        lxc stop $vmname
        lxc config device override $vmname eth0 ipv4.address=$vmip
        if [[ $using_tls_proxy != true ]]; do
            lxc config device set $vmname web_port nat=true \\
                listen=tcp:{conf.get('frontend.ips')[0]}:{conf.get('frontend.web_port')} \\
                connect=tcp:0.0.0.0:{conf.get('backend.web_port')}
        fi
        lxc config device set $vmname wg_port nat=true \\
            listen=udp:{conf.get('frontend.ips')[0]}:{conf.get('frontend.wg_port')} \\
            connect=tcp:0.0.0.0:{conf.get('backend.wg_port')}
        lxc start $vmname
        ##
        ## Configure port forwarding from host to container for BIND
        ##
        ##
    '''
    print(textwrap.dedent(script).strip())
