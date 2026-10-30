import logging
import ipaddress
import os
import re
import secrets
import string
import tempfile
import textwrap
import yaml
import hub.net as net
import hub.util as util

Berror = util.Berror
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)
config = None

### How to make a new version of the config file
# 1. add an 'if cfv == ...' section near the end of migrate() to transform the config file; finish
#        the section with the 2 lines `cfv += 1` and `set('programmatic_use_only', cfv)`.
# 2. increment config_fv below
# 3. test
config_fv = 5081  # version of the config file key structure


def get(cpath: str):  # parse config path, e.g. get('frontend.ips')[0]
    base = config
    try:
        for c in cpath.split('.'):
            base = base[c]
        return base
    except TypeError as e:
        if config == None:
            raise Berror(f"B69102 config not loaded getting: {cpath}")
        raise Berror(f"B21331 invalid get ({e}): {cpath}")
    except KeyError:
        raise Berror(f"B62808 invalid cpath: {cpath}")


def base_url():  # e.g. https://vxm.example.org/229xGAfr (no trailing slash)
    public_port = get('frontend.web_port')
    port_spec = '' if public_port == 443 else ':' + str(public_port)
    site_code = get('frontend.site_code')
    return f"{get('frontend.web_proto')}://{get('frontend.domain')}{port_spec}{site_code}"


# use only in migrate(); changes are not saved
def set(cpath: str, new_value):
    s = cpath.split('.')
    base = config
    try:
        for c in s[:-1]:
            base = base[c]
        base[s[-1]] = new_value
    except TypeError:
        raise Berror(f"B92390 config not loaded getting: {cpath}")


# use only in migrate(); changes are not saved
def insert_item_after(existing_item: str, new_key: str, new_value, before_not_after=False):
    global config
    dotsplit = existing_item.rsplit('.', 1)
    if len(dotsplit) == 1:  # special case for modifying root
        insert_anchor_key = existing_item
        old_dict = config
    else:
        insert_section, insert_anchor_key = dotsplit
        old_dict = get(insert_section)
    if before_not_after:
        insert_before = insert_anchor_key
        insert_after = None  # here we assume that no keys are None
    else:
        insert_after = insert_anchor_key
        insert_before = None
    if not isinstance(old_dict, dict):
        raise Berror(f"B62854 existing_item is not a dict member: {existing_item}")
    new_dict = dict()
    if new_key in old_dict:
        raise Berror(f"B80342 new_key already exists: {insert_section}.{new_key}")
    found = False
    for k, v in old_dict.items():
        if k == insert_before:
            new_dict[new_key] = new_value
            found = True
        new_dict[k] = v
        if k == insert_after:
            new_dict[new_key] = new_value
            found = True
    if not found:
        raise Berror(f"B07330 existing_item not found: {existing_item}")
    if len(dotsplit) == 1:  # special case for modifying root
        config = new_dict
    else:
        set(insert_section, new_dict)


# use only in migrate(); changes are not saved
def insert_item_before(existing_item: str, new_key: str, new_value: str):
    insert_item_after(existing_item, new_key, new_value, before_not_after=True)


def migrate():  # update config data to current format
    if (cfv := get('programmatic_use_only')) < 5078:
        raise Berror(f"B79322 invalid config_file_version: {cfv}")
    if cfv != config_fv:
        logger.debug(f"B93350 migrate() from {cfv} to {config_fv}")
    if cfv == 5078:  # migrate in-memory to next version
        base62_digits = string.ascii_uppercase + string.ascii_lowercase + string.digits
        site_code = '/' + ''.join(secrets.choice(base62_digits) for _ in range(8))
        wg_port = net.random_free_port(use_udp=True, avoid=[5353])
        # section: help
        new_branch = yaml.safe_load(textwrap.dedent(f'''
                    notes:
                    - This is the BitBurrow configuration file. Edit this file to configure
                      the BitBurrow hub.
                    - Yaml comments may be deleted on upgrade, but other changes made
                      here will persist.
                    - This 'help:' section at the top is documentation.
                    - File and directory paths can be absolute (beginning with '/'), relative to
                      the BitBurrow user home directory (beginning with '~/'), or relative to
                      the directory of this config file (all others).
                    - The 'frontend' section is for the public-facing server, which may or may
                      not be a reverse proxy, while 'backend' refers to bbhub itself.
                    frontend:
                      domain: Domain to access hub and for VPN client subdomains, e.g.
                        "vxm.example.org".
                      site_code: Optional string prepended to all URL paths to make identifying
                        and scanning BitBurrow hubs more difficult. Must begin with a slash and
                        use URL-safe characters, e.g. '/a_44muitz/s'. Use '' to disable.
                      web_port: Public-facing TCP port, normally 443.
                      web_proto: Must be 'http' or 'https'.
                      wg_port: Frontend WireGuard UDP port. Don't change this once a base router
                        has been created because the DB and router endpoint and won't get updated.
                      ips: List of IPv4 and IPv6 addresses that 'domain' should point to.
                    backend:
                      web_port: TCP port to listen on.
                      web_proto: Must be 'http' or 'https'.
                      wg_port: UDP port used by VPN bases.
                      ip: Address to listen on, '' for all, '0.0.0.0' for IPv4 only, '::0' for
                        IPv6 only, or a specific IP.
                    path:
                      db: Database file path. Use '-' (YAML requires the quotes) for memory-only.
                      log: Log files directory.
                      log_level: Levels are 0 (critical only), 1 (errors), 2 (warnings), 3 (normal),
                        4 or 5 for debug. Log level can be temporarily overwritten via CLI options.
                      log_level_uvicorn: Log level to set for Uvicorn.
                      tls_key: TLS key file, e.g. 'privkey.pem'. Leave empty to have bbhub manage
                        TLS keys with Certbot.
                      tls_cert: Cert file, e.g. 'fullchain.pem'.
                      restart_on_change: If any file listed here, such as a TLS cert file, is
                        modified, the hub will wait until no connections are active and then
                        restart.
                      restart_on_change_interval: The interval, in seconds, to check for changed
                        files.
                    programmatic_use_only: Don't change this. It is used internally by bbhub to
                      update this config file.
                ''').lstrip())
        insert_item_before('programmatic_use_only', 'help', new_branch)
        # section: frontend
        new_branch = yaml.safe_load(textwrap.dedent(f'''
                    domain: vxm.example.org
                    site_code: {site_code}
                    web_port: 8443
                    web_proto: https
                    wg_port: {wg_port}
                    ips:
                    - 203.0.113.9
                ''').lstrip())
        insert_item_before('programmatic_use_only', 'frontend', new_branch)
        # section: backend
        new_branch = yaml.safe_load(textwrap.dedent(f'''
                    web_port: 8443
                    web_proto: https
                    wg_port: {wg_port}
                    ip: ''
                ''').lstrip())
        insert_item_before('programmatic_use_only', 'backend', new_branch)
        # section: path
        new_branch = yaml.safe_load(textwrap.dedent(f'''
                    db: data.sqlite
                    log: ~/.cache/bitburrow/
                    log_level: 3
                    log_level_uvicorn: 2
                    tls_key: ''
                    tls_cert: ''
                    restart_on_change:
                    - config.yaml
                    restart_on_change_interval: 60
                ''').lstrip())
        insert_item_before('programmatic_use_only', 'path', new_branch)
        cfv += 1
        set('programmatic_use_only', cfv)
    if cfv == 5079:  # migrate in-memory to next version
        # new config: hub_bloc
        hub_bloc = secrets.randbelow(16)  # could use: random.randint(0, 15)
        new_branch = yaml.safe_load(textwrap.dedent(f'''
                    Integer 0-15 used to calculate base router wg addresses. If you manage multiple
                    hubs, each hub should have a unique hub_bloc to permit multiple bbbased.lua
                    instances on one base router.
                ''').lstrip())
        insert_item_after('help.backend.ip', 'hub_bloc', new_branch)
        insert_item_after('backend.ip', 'hub_bloc', hub_bloc)
        # new config: host_bits
        host_bits = 12
        new_branch = yaml.safe_load(textwrap.dedent(f'''
                    Number of address bits reserved for the host for IPv4. Only 12 has been tested
                    (e.g. 172.18.0.0/20). For IPv6, 16 (a /112 network) is always used.
                ''').lstrip())
        insert_item_after('help.backend.ip', 'host_bits', new_branch)
        insert_item_after('backend.ip', 'host_bits', host_bits)
        cfv += 1
        set('programmatic_use_only', cfv)
    if cfv == 5080:  # migrate in-memory to next version
        insert_item_before(
            'help.backend',
            'midtier',
            {
                'ip': "Internet-facing IP of BitBurrow's container's host; often same as "
                + "frontend.ips[0]; if different, be sure to set up forwarding for specified "
                + "ports from the public IP to the container host"
            },
        )
        insert_item_before('backend', 'midtier', {'ip': get('frontend.ips')[0]})
        cfv += 1
        set('programmatic_use_only', cfv)
    # if cfv == 5081:  # migrate in-memory to next version
    #     ...
    #     cfv += 1
    #     set('programmatic_use_only', cfv)
    if cfv != config_fv:
        raise Berror(f"B87851 invalid config_file_version: {cfv}")


def load(path: str):
    global config
    try:
        with open(path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        raise Berror(f"B22006 cannot find configuration file (try 'bbhub generate-config'): {path}")
    except PermissionError:
        raise Berror(f"B76167 cannot read configuration file: {path}")
    except (yaml.parser.ParserError, yaml.scanner.ScannerError) as e:
        raise Berror(f"B60933 cannot parse configuration file: {e}")
    migrate()

    def validate_domain(domain: str, cpath: str) -> None:
        if domain != (clean_domain := domain.lower().rstrip('.')):
            raise Berror(f"B38018 invalid {cpath} '{domain}'; try '{clean_domain}'")
        labels = domain.split('.')
        if len(domain) > 253:
            raise Berror(f"B76960 {cpath} '{domain}' too long")
        if any(
            not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels
        ):
            raise Berror(f"B55948 invalid characters in {cpath} '{domain}'")

    def validate_ip(ip: str, cpath: str) -> None:
        if not isinstance(ip, str):
            raise Berror(f"B67218 invalid {cpath} address '{ip}' in {path}")
        try:
            a = ipaddress.ip_address(ip)
            if a == ipaddress.IPv4Address('203.0.113.9'):
                raise Berror(f"B10034 {cpath} address '{ip}' in {path} is the example address")
            if a.version != 4:  # IPv6 support has not been investigated
                raise Berror(f"B00319 unsupported IPv6 address '{ip}' in {cpath} in {path}")
            if isinstance(a, ipaddress.IPv6Address) and a.scope_id is not None:
                if not re.fullmatch(r'[a-zA-Z0-9_.-]+', a.scope_id):  # no shell command injection
                    raise Berror(f"B66639 invalid {cpath} scope_id '{a.scope_id}' in {path}")
        except ValueError:
            raise Berror(f"B12550 invalid {cpath} address '{ip}' in {path}")

    def validate_ips(ips: list, cpath: str):
        if not isinstance(ips, list) or not ips:
            raise Berror(f"B34603 {cpath} must be a nonempty list in {path}")
        for ip in ips:
            validate_ip(ip, cpath)

    def validate_port(port: int, cpath: str) -> None:
        if not isinstance(port, int) or port < 2 or port > 65535:  # 'True' parses to 1
            raise Berror(f"B98953 invalid {cpath} port '{port}' in {path}")

    def validate_site_code(site_code: str, cpath: str):
        if not isinstance(site_code, str):
            raise Berror(f"B85729 invalid {cpath} '{site_code}'")
        if not re.fullmatch(r'[/A-Za-z0-9%!$&\'\(\)\*\+,;=:@_~-]*', site_code):
            raise Berror(f"B58427 invalid {cpath} characters in '{site_code}'")
        if site_code and (not site_code.startswith('/') or site_code.endswith('/')):
            raise Berror(f"B27489 '{cpath} must begin with '/' and not end with '/'")

    validate_domain(get('frontend.domain'), 'frontend.domain')
    validate_ips(get('frontend.ips'), 'frontend.ips')
    validate_ip(get('midtier.ip'), 'midtier.ip')
    validate_port(get('frontend.web_port'), 'frontend.web_port')
    validate_port(get('frontend.wg_port'), 'frontend.wg_port')
    validate_port(get('backend.web_port'), 'backend.web_port')
    validate_port(get('backend.wg_port'), 'backend.wg_port')
    validate_site_code(get('frontend.site_code'), 'fontend.site_code')


def is_loaded():
    try:
        return get('programmatic_use_only') >= 5076
    except Exception:
        return False


def save(path: str):
    if not os.path.exists(path):
        raise Berror(f"B69061 config file must already exist; use generate(): {path}")
    try:
        old_umask = os.umask(0o077)  # create a file with 0600 permissions
        with tempfile.NamedTemporaryFile(
            dir=os.path.dirname(path),  # in the same directory
            prefix='config-',
            suffix=".yaml",
            delete=False,
            mode="w",
            encoding="utf-8",
        ) as f:
            yaml.dump(config, f, sort_keys=False, allow_unicode=True)
            tmp_path = f.name
    finally:
        os.umask(old_umask)
    util.rotate_backups(path, tmp_path)


def generate(path):
    global config
    config_file_template = 'programmatic_use_only: 5078'
    # note we safe_load() and then dump() the YAML so we can migrate() and also to
    # ... minimize the actual file changes on next migration
    try:
        config = yaml.safe_load(config_file_template)
    except (yaml.parser.ParserError, yaml.scanner.ScannerError) as e:
        raise Berror(f"B06494 cannot parse YAML data: {e}")
    migrate()
    try:
        old_umask = os.umask(0o077)  # create a file with 0600 permissions
        util.mkdir_r(os.path.dirname(path))
        with open(path, "x", encoding="utf-8") as f:
            yaml.dump(config, f, sort_keys=False, allow_unicode=True)
    except FileExistsError:
        logger.warning(f"B88926 not modifying existing file: {path}")
    except Exception:
        raise Berror(f"B26104 cannot create: {path}")
    finally:
        os.umask(old_umask)
