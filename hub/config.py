import os
import secrets
import tempfile
import textwrap
import yaml
import hub.net as net

config = None


class ConfError(Exception):
    pass


def get(cpath: str):
    s = cpath.split('.')
    try:
        if len(s) == 1:
            return config[s[0]]
        if len(s) == 2:
            return config[s[0]][s[1]]
        if len(s) == 3:
            return config[s[0]][s[1]][s[2]]
        if len(s) == 4:
            return config[s[0]][s[1]][s[2]][s[3]]
    except TypeError:
        if config == None:
            raise ConfError(f"B69102 config not loaded getting: {cpath}")
        raise ConfError(f"B21331 invalid get: {cpath}")
    except KeyError:
        raise ConfError(f"B62808 invalid cpath: {cpath}")
    raise ConfError(f"B99402 invalid cpath: {cpath}")


def set(cpath: str, new_value):  # do not use outside of migrate()
    s = cpath.split('.')
    try:
        if len(s) == 1:
            config[s[0]] = new_value
        elif len(s) == 2:
            config[s[0]][s[1]] = new_value
        elif len(s) == 3:
            config[s[0]][s[1]][s[2]] = new_value
        elif len(s) == 4:
            config[s[0]][s[1]][s[2]][s[3]] = new_value
        else:
            raise ConfError(f"B14141 invalid cpath: {cpath}")
    except TypeError:
        raise ConfError(f"B92390 config not loaded getting: {cpath}")


def load(path: str):
    global config
    try:
        with open(path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfError(
            f"B22006 cannot find configuration file (try 'bbhub generate-config'): {path}"
        )
    except PermissionError:
        raise ConfError(f"B76167 cannot read configuration file: {path}")
    except (yaml.parser.ParserError, yaml.scanner.ScannerError) as e:
        raise ConfError(f"B60933 cannot parse configuration file: {e}")
    if (cfv := get('advanced.config_file_version')) < 5076:
        raise ConfError(f"B79322 invalid config_file_version: {cfv}")
    if cfv != 5076:
        raise ConfError(f"B87851 invalid config_file_version: {cfv}")


def is_loaded():
    try:
        return get('advanced.config_file_version') >= 5076
    except Exception:
        return False


def generate(path, domain, public_ip):
    digits = '23456789bcdfghjklmnpqrstvwxz'
    site_code = ''.join(secrets.choice(digits) for i in range(7))
    wg_port = net.random_free_port(use_udp=True, avoid=[5353])
    config_file_template = textwrap.dedent(
        f'''
            config_help: This is the BitBurrow configuration file. Edit this file to configure
              the BitBurrow hub. Yaml comments may be deleted on upgrade, but other changes
              made here will persist. Items ending in '_help' are documentation.
            common:
              db_file_help: Database file path. Can be absolute or relative to the directory
                of this config file. Use "-" (YAML requires the quotes) for memory-only.
              db_file: data.sqlite
              log_path: /var/log/bitburrow
              log_level_help: Levels are 0 (critical only), 1 (errors), 2 (warnings), 3
                (normal), 4 or 5 for debug. Log level can be temporarily overwritten via
                CLI options.
              log_level: 3
              site_code: {site_code}
              domain_help: Domain to access hub and for VPN client subdomains, e.g.
                vxm.example.org
              domain: {domain}
              public_ips_help: Public IP address(es).
              public_ips:
              - {public_ip}
            wireguard:
              ports_help: UDP port(s) used by VPN bases.
              ports:
              - {wg_port}
            advanced:
              config_file_version_help: Do not edit this item! It is used in config file
                upgrade process.
              config_file_version: 5076 
        '''
    ).lstrip()
    try:
        old_umask = os.umask(0o077)  # create a file with 0600 permissions
        with open(path, "x", encoding="utf-8") as f:
            f.write(config_file_template)
    except FileExistsError:
        raise ConfError(f"B88926 file already exists: {path}")
    except Exception:
        raise ConfError(f"B26104 cannot create: {path}")
    finally:
        os.umask(old_umask)


def insert_item_after(existing_item: str, new_key: str, new_value, before_not_after=False):
    if before_not_after:
        insert_section, insert_before = existing_item.rsplit('.', 1)
        insert_after = None  # here we assume that no keys are None
    else:
        insert_section, insert_after = existing_item.rsplit('.', 1)
        insert_before = None
    old_dict = get(insert_section)
    if not isinstance(old_dict, dict):
        raise ConfError(f"B62854 existing_item is not a dict member: {existing_item}")
    new_dict = dict()
    if new_key in old_dict:
        raise ConfError(f"B80342 new_key already exists: {insert_section}.{new_key}")
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
        raise ConfError(f"B07330 existing_item not found: {existing_item}")
    set(insert_section, new_dict)


def insert_item_before(existing_item: str, new_key: str, new_value: str):
    insert_item_after(existing_item, new_key, new_value, before_not_after=True)


def rotate_backups(path: str, new_path: str, max_versions: int = 9):
    base, ext = os.path.splitext(path)
    for v in range(max_versions, 0, -1):
        dst = f'{base}.{v}{ext}'
        if v > 1:
            src = f'{base}.{v-1}{ext}'
            if os.path.exists(src):
                try:
                    os.replace(src, dst)  # mv config.8.yaml config.9.yaml
                except Exception:
                    pass
        else:
            os.link(path, dst)  # ln config.yaml config.1.yaml  # hard link so migration is atomic
            os.replace(new_path, path)  # mv config-EWIL.yaml config.yaml


def save(path: str):
    if not os.path.exists(path):
        raise ConfError(f"B69061 config file must already exist; use generate(): {path}")
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
    rotate_backups(path, tmp_path)
