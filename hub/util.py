import asyncio
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

shutdown_event = asyncio.Event()
project_root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ui_path = os.path.join(project_root_path, 'hub', 'ui')


class Berror(Exception):
    """Raise for probably-fatal errors (calling method can decide). Include a Berror code
    (https://bitburrow.com/hub/#berror-codes) and any potentially useful details.
    """

    pass


def front_berror_code(e: Exception, subd: str, ip: str = None) -> str:
    disp = str(e)
    id_string = f"base {subd} at {ip}" if ip else f"base {subd}"
    if re.match(r'^B[0-9]{5} ', disp):  # front the Berror code
        return f"{disp[0:7]}{id_string} {disp[7:]}"
    else:
        return f"{disp} ({id_string})"


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
- id: bind_a_via_localhost
  cmd: dig @127.0.0.1 A {domain} +short
  expected: '{public_ip}'
# localhost domain NS record
- id: bind_ns_via_localhost
  cmd: dig @127.0.0.1 NS {domain} +short
  expected: '{domain}.'
# domain A record
- id: bind_a_via_ip
  cmd: dig @{public_ip} A {domain} +short
  expected: '{public_ip}'
# domain NS record
- id: bind_ns_via_ip
  cmd: dig @{public_ip} NS {domain} +short
  expected: '{domain}.'
# domain A record over TCP
- id: bind_tcp_a
  cmd: dig @{public_ip} A {domain} +tcp +short
  expected: '{public_ip}'
# global A via Cloudflare resolver
- id: global_cf_a
  cmd: dig @1.1.1.1 A {domain} +short
  expected: '{public_ip}'
# global A via Google resolver
- id: global_google_a
  cmd: dig @8.8.8.8 A {domain} +short
  expected: '{public_ip}'
# domain SOA record
- id: bind_soa
  cmd: dig @{public_ip} SOA {domain} +short |keep_only '^\S*'
  expected: '{domain}.'
# global SOA record
- id: global_soa
  cmd: dig SOA {domain} +short |keep_only '^\S*'
  expected: '{domain}.'
# global A record
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
# ensure BIND recursive resolver has REFUSED status
- id: bind_recursive_status
  cmd: >-  # use YAML folded block scalar for colon and backslash below
    dig @{public_ip} A google.com |keep_only 'status: [^\s,]*'
  expected: 'status: REFUSED'
# ensure zone transfer is disabled (list of VPN bases should not be public)
- id: bind_axfr_off
  cmd: dig @{public_ip} {domain} AXFR +short
  expected: '; Transfer failed.'
# EDNS support (OPT pseudo-section present)
- id: bind_edns_opt
  cmd: dig @{public_ip} {domain} +dnssec |keep_only '^;; OPT PSEUDOSECTION:'
  expected: ';; OPT PSEUDOSECTION:'
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
        first_match = re.search(keep_only, result, flags=re.MULTILINE)
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


def fix_lan_overlap_shell_code() -> str:
    """Return Ash shell code that checks for and tries to fix overlapping LAN subnets.

    This can happen if the router's WAN port is connected downstream of another
    router with the same LAN subnet, e.g. 192.168.8.0/24."""
    return (
        # should be mirrored in delete_adopt5c_code(); search: tag_adopt5c_code
        """if ip -4 addr |{\n"""
        + """  s=,\n"""
        + """  while read -r x y z; do\n"""
        + """    [ "$x" = inet ] || continue\n"""
        + """    case "$y" in\n"""
        + """      */24) ;;\n"""
        + """      *) continue;;\n"""
        + """    esac\n"""
        + """    a=${y%/*}\n"""
        + """    p=${a%.*}\n"""
        + """    case "$s" in\n"""
        + """      *,"$p",*) exit 0;;\n"""
        + """    esac\n"""
        + """    s="$s$p,"\n"""
        + """  done\n"""
        + """  exit 1\n"""
        + """}; then\n"""
        # choose a random subnet; avoid 192.168.100.x famously associated with cable modems, etc.
        + """  r=srand\\(\\)\\;print\\ 104+\n"""  # careful backslashing
        + """  r=$r'int(rand()*137)'\n"""
        + """  OCTET3=$(awk "BEGIN{$r}")\n"""
        + """  u(){ uci set network.lan.$1=$2;}\n"""
        + """  u ipaddr 192.168.$OCTET3.1\n"""  # hoping this subnet is unused
        + """  u netmask 255.255.255.0\n"""
        + """  u proto static\n"""
        + """  uci commit network\n"""
        + """  /etc/init.d/network restart\n"""
        + """  /etc/init.d/dnsmasq restart\n"""
        + """  rm -f /tmp/dhcp.leases\n"""
        + """  killall -HUP dnsmasq\n"""
        + """fi\n"""
    )


async def test_fix_lan_overlap_shell_code() -> None:
    def lan_overlap_if_body() -> str:
        code = fix_lan_overlap_shell_code()
        prefix = 'if ip -4 addr |{\n'
        suffix = '\n}; then\n'
        start = code.index(prefix) + len(prefix)
        end = code.index(suffix, start)
        return code[start:end]

    cases = [
        (
            """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536
    inet 127.0.0.1/8 scope host lo
2: wan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 192.168.8.20/24 brd 192.168.8.255 scope global wan
3: lan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 192.168.8.1/24 brd 192.168.8.255 scope global lan
""",
            'overlap',
        ),
        (
            """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536
    inet 127.0.0.1/8 scope host lo
2: wan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 10.0.5.22/24 brd 10.0.5.255 scope global wan
3: lan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 192.168.1.1/24 brd 192.168.1.255 scope global lan
4: guest: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 10.0.5.1/24 brd 10.0.5.255 scope global guest
""",
            'overlap',
        ),
        (
            """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536
    inet 127.0.0.1/8 scope host lo
2: wan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 192.168.8.20/24 brd 192.168.8.255 scope global wan
3: lan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 192.168.1.1/24 brd 192.168.1.255 scope global lan
""",
            'no-overlap',
        ),
        (
            """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536
    inet 127.0.0.1/8 scope host lo
2: lan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 192.168.1.1/24 brd 192.168.1.255 scope global lan
""",
            'no-overlap',
        ),
        (
            """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536
    inet 127.0.0.1/8 scope host lo
2: wan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 192.168.8.120/24 brd 192.168.8.255 scope global wan
3: lan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 192.168.8.1/24 brd 192.168.8.255 scope global lan
""",
            'overlap',
        ),
        (
            """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536
    inet 127.0.0.1/8 scope host lo
2: vpn: <POINTOPOINT,UP,LOWER_UP> mtu 1420
    inet 127.0.0.2/8 scope global vpn
""",
            'no-overlap',
        ),
        (
            '',
            'no-overlap',
        ),
        (
            """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
11: br-lan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000
    inet 192.168.196.1/24 brd 192.168.196.255 scope global br-lan
       valid_lft forever preferred_lft forever
13: eth0.2@eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1600 qdisc noqueue state UP group default qlen 1000
    inet 192.168.1.101/24 brd 192.168.1.255 scope global eth0.2
       valid_lft forever preferred_lft forever
16: wgbb1: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1420 qdisc noqueue state UNKNOWN group default qlen 1000
    inet 172.24.188.39/32 scope global wgbb1
       valid_lft forever preferred_lft forever
""",
            'no-overlap',
        ),
        (
            """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
    inet 192.168.1.66/24 brd 192.168.1.255 scope global eth0
       valid_lft forever preferred_lft forever
5: br-lan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000
    inet 192.168.8.1/24 brd 192.168.8.255 scope global br-lan
       valid_lft forever preferred_lft forever
26: wg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1420 qdisc noqueue state UNKNOWN group default qlen 1000
    inet 10.79.112.222/32 brd 255.255.255.255 scope global wg0
       valid_lft forever preferred_lft forever
""",
            'no-overlap',
        ),
        (
            """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
    inet 192.168.8.51/24 brd 192.168.1.255 scope global eth0
       valid_lft forever preferred_lft forever
5: br-lan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000
    inet 192.168.8.1/24 brd 192.168.8.255 scope global br-lan
       valid_lft forever preferred_lft forever
""",
            'overlap',
        ),
    ]
    body = lan_overlap_if_body()
    for test_num, (ip_out, expected) in enumerate(cases):
        script = (
            'if (\n'
            + body
            + '\n); then\n'
            + "  echo overlap\n"
            + 'else\n'
            + "  echo no-overlap\n"
            + 'fi\n'
        )
        for shell in ['bash', 'sh']:
            args = [shell, '-c', script]
            result = await net.run_external_async(args, input=ip_out)
            if expected != result:
                logger.error(
                    f"B65449 fix_lan_overlap test {test_num} failed: "
                    + f"expected '{expected}', got '{result}' (using {shell})"
                )


def verify_adopt5c_code_prefixes(adopt5c_code):
    """Return true iff Lua prefixes match the start of adopt5c_code lines."""
    lua_path = os.path.join(project_root_path, 'hub/bbbased.lua')
    adopt5c_lines = adopt5c_code.splitlines()
    in_prefixes = False
    with open(lua_path, encoding='utf-8') as f:
        for line_num, line in enumerate(f):
            line = line.split('--', 1)[0].strip()
            if not in_prefixes:
                if line == 'local prefixes = {':
                    in_prefixes = True
                    line_offset = line_num + 1  # file offset of first prefix line
                continue
            if line == '}':
                break
            if not line or not line.endswith(','):
                logger.error(f"B27184 malformed prefixes line in {lua_path}: {line}")
                return False
            value = line[:-1].strip()
            if len(value) < 2 or value[0] != "'" or value[-1] != "'":
                logger.error(f"B88758 malformed prefixes line in {lua_path}: {line}")
                return False
            try:
                rline = line_num - line_offset
                have = adopt5c_lines[rline][0 : len(value) - 2]
                if have != value[1:-1]:
                    logger.error(
                        f"B70722 adopt5c_code line {rline}"
                        + f" begins '{have}', expecting '{value[1:-1]}'"
                    )
            except IndexError:
                logger.error(f"B67971 {len(adopt5c_lines)=}, {rline=}")
                return False
        if not in_prefixes:
            logger.error(f"B61331 no prefixes found in {lua_path}")
            return False
    return True


def gzip_base64(s: str, wrap: int = 76, prefix: str = '', postfix: str = '\n') -> str:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9) as f:
        f.write(s.encode("utf-8"))
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return ''.join([f'{prefix}{b64[i:i+wrap]}{postfix}' for i in range(0, len(b64), wrap)])


def gzbify(input: str, max_width=33) -> str:
    """gzip, then base64-encode, then wrap in extraction code"""
    return (
        '''T=$(mktemp)\n'''
        + gzip_base64(input, max_width, 'echo ', '>>$T\n').rstrip()
        + '''\nsh -c "$(cat $T|openssl base64 -d|gunzip)"\n'''  # 33 matches this width
        + '''rm -f $T\n'''
    )


def slugify(s: str, *, max_len: int = 32) -> str:
    s = re.sub(r'[&+@]', lambda m: {'&': ' and ', '+': ' plus ', '@': ' at '}[m[0]], s)
    s = re.sub(r'[^a-z0-9]+', '-', s.lower())
    s = re.sub(r'-{2,}', '-', s)
    return s[:max_len].strip('-')


def mkdir_r(path):  # like Linux `mkdir --parents`
    if path == '':
        return
    base_dir = os.path.dirname(path)
    if not os.path.exists(base_dir):
        mkdir_r(base_dir)
    try:
        os.makedirs(path, exist_ok=True)
    except (PermissionError, FileNotFoundError, NotADirectoryError):
        raise Berror(f"B19340 cannot create directory: {path}")


def port_forward_script():
    frontp = conf.get('frontend.web_proto')
    backp = conf.get('frontend.web_proto')
    # note: all backslashes and non-f-string {} must be doubled
    script = f'''
        #!/bin/bash
        ##
        vmname={net.run_external('hostname', '--short')}
        using_tls_proxy={'true' if (frontp == 'https' and backp == 'http') else 'false'}
        ##
        ## Configure port forwarding from host to container for BitBurrow hub
        ##
        if [ $using_tls_proxy != true ]; then
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
        ## Allow tracking of WireGuard connection IPs for DDNS and logging of client IP
        ## addresses; otherwise all connections appear to be from 127.0.0.1; from
        ## https://discuss.linuxcontainers.org/t/making-sure-that-ips-connected-to-the-containers-gameserver-proxy-shows-users-real-ip/8032/5
        ##
        vmipv4=$(lxc exec $vmname -- ip -4 -o addr show dev eth0 |awk '{{print $4}}' |cut -d/ -f1)
        lxc stop $vmname
        lxc config device override $vmname eth0 ipv4.address=$vmipv4
        if [ $using_tls_proxy != true ]; then
            lxc config device set $vmname web_port nat=true \\
                listen=tcp:{conf.get('frontend.ips')[0]}:{conf.get('frontend.web_port')} \\
                connect=tcp:0.0.0.0:{conf.get('backend.web_port')}
        fi
        lxc config device set $vmname wgport nat=true \\
            listen=udp:{conf.get('frontend.ips')[0]}:{conf.get('frontend.wg_port')} \\
            connect=udp:0.0.0.0:{conf.get('backend.wg_port')}
        lxc start $vmname
    '''
    print(textwrap.dedent(script).strip())


def tls_cert_script():  # script to run certbot for wildcard TLS cert
    if conf.get('backend.web_proto') != 'https':
        print("# TLS is not enabled ('backend.web_proto' in config file must be 'https')")
        return
    # note: backslashes are not escaped
    script = r'''
        #!/bin/bash
        set -e
        sudo apt-get install -y acl  # see `setfacl` below and https://stackoverflow.com/a/56379678
        sudo snap install --classic certbot
        # debugging: bbhub test all
        # debugging: sudo certbot renew --dry-run
        # debugging: sudo systemctl list-timers snap.certbot.renew.timer
        # to delete an old domain: sudo certbot delete --cert-name vxm.example.org -n
        cat <<"_EOF9981_" |sudo -u bind tee /opt/certbot_hook.sh  # hook file
        #!/bin/bash
        DNS_ZONE={domain}
        HOST='_acme-challenge'
        sudo -u bind /usr/bin/nsupdate -l <<EOM
        zone ${DNS_ZONE}
        update delete ${HOST}.${CERTBOT_DOMAIN} A
        update add ${HOST}.${CERTBOT_DOMAIN} 300 TXT "${CERTBOT_VALIDATION}"
        send
        EOM
        sleep 5
        _EOF9981_
        sudo chmod 770 /opt/certbot_hook.sh  # 550 will cause tee to fail on next run
        if ! [ -f /etc/letsencrypt/{domain}.registered ]; then  # once it completes successfully, never run again
            sudo certbot certonly -n --agree-tos \
                --manual --manual-auth-hook=/opt/certbot_hook.sh \
                --preferred-challenge=dns \
                --register-unsafely-without-email \
                -d '*.'{domain} -d {domain} \
                --server https://acme-v02.api.letsencrypt.org/directory \
                && sudo touch /etc/letsencrypt/{domain}.registered
        fi
        # fix permissions so bbhub can read cert
        sudo setfacl -Rm d:user:bitburrow:rx,user:bitburrow:rx /etc/letsencrypt/
    '''
    print(textwrap.dedent(script).strip().replace('{domain}', conf.get('frontend.domain')))


def read_versions_file() -> dict:
    versions = dict()
    versions_path = os.path.join(project_root_path, 'versions')  # updated in git_hooks/pre-commit
    try:
        with open(versions_path, encoding='utf-8') as f:
            for line in f:
                line = line.rstrip('\n')
                if not line or line.startswith('#'):
                    continue
                parts = line.split(' ', 1)  # path can begin with or contain spaces
                if len(parts) == 2:
                    versions[parts[1]] = parts[0]
                else:
                    logger.error(f"B21888 invalid line in 'versions': {line}")
    except OSError:
        return versions
    return versions
