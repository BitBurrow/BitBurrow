import logging
import os
import secrets
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
config_fv = 5079  # version of the config file key structure


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


def migrate(domain='', public_ip=''):  # update config data to current format
    if (cfv := get('programmatic_use_only')) < 5078:
        raise Berror(f"B79322 invalid config_file_version: {cfv}")
    if cfv != config_fv:
        logger.debug(f"B93350 migrate() from {cfv} to {config_fv}")
    if cfv == 5078:  # migrate in-memory to next version
        digits = '23456789bcdfghjklmnpqrstvwxz'
        site_code = ''.join(secrets.choice(digits) for i in range(7))
        wg_port = net.random_free_port(use_udp=True, avoid=[5353])
        new_branch = yaml.safe_load(
            textwrap.dedent(  # section: help
                f'''
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
                      site_code: Optional string prepended to all URL paths to make identification
                        of BitBurrow hubs more difficult.
                      web_port: Public-facing TCP port, normally 443.
                      web_proto: Must be 'http' or 'https'.
                      wg_port: Frontend Wireguard UDP port. Don't change this once a base router
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
                '''
            ).lstrip()
        )
        insert_item_before('programmatic_use_only', 'help', new_branch)
        new_branch = yaml.safe_load(
            textwrap.dedent(  # section: frontend
                f'''
                    domain: {domain}
                    site_code: {site_code}
                    web_port: 8443
                    web_proto: https
                    wg_port: {wg_port}
                    ips:
                    - {public_ip}
                '''
            ).lstrip()
        )
        insert_item_before('programmatic_use_only', 'frontend', new_branch)
        new_branch = yaml.safe_load(
            textwrap.dedent(  # section: backend
                f'''
                    web_port: 8443
                    web_proto: https
                    wg_port: {wg_port}
                    ip: ''
                '''
            ).lstrip()
        )
        insert_item_before('programmatic_use_only', 'backend', new_branch)
        new_branch = yaml.safe_load(
            textwrap.dedent(  # section: path
                f'''
                    db: data.sqlite
                    log: ~/.cache/bitburrow/
                    log_level: 3
                    log_level_uvicorn: 2
                    tls_key: ''
                    tls_cert: ''
                    restart_on_change:
                    - config.yaml
                    restart_on_change_interval: 60
                '''
            ).lstrip()
        )
        insert_item_before('programmatic_use_only', 'path', new_branch)
        cfv += 1
        set('programmatic_use_only', cfv)
    # if cfv == 5079:  # migrate in-memory to next version
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


def generate(path, domain, public_ip):
    global config
    config_file_template = 'programmatic_use_only: 5078'
    # note we safe_load() and then dump() the YAML so we can migrate() and also to
    # ... minimize the actual file changes on next migration
    try:
        config = yaml.safe_load(config_file_template)
    except (yaml.parser.ParserError, yaml.scanner.ScannerError) as e:
        raise Berror(f"B06494 cannot parse YAML data: {e}")
    migrate(domain, public_ip)
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
