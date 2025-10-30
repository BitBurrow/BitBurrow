import os
import secrets
import textwrap
import yaml
import hub.net as net

config = None
config_fv = 5075  # version of the config file key structure


class ConfError(Exception):
    pass


def get(cpath):
    s = cpath.split('.')
    try:
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
    raise ConfError(f"B99402 invalid cpath: {cpath}")


def load(path):
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
    if (cfv := config['advanced']['config_file_version']) != config_fv:
        raise ConfError(f"B79322 invalid config_file_version: {cfv}")


def loaded():
    return get('advanced.config_file_version') == config_fv


def generate(path, domain, public_ip):
    digits = '23456789bcdfghjklmnpqrstvwxz'
    site_code = ''.join(secrets.choice(digits) for i in range(7))
    wg_port = net.random_free_port(use_udp=True, avoid=[5353])
    config_file_template = textwrap.dedent(
        f'''
            ### BitBurrow configuration file
            #
            # Edit this file to configure the BitBurrow hub.
            #
            common:
              # Database file path. Can be absolute or relative to the directory of this config
              # file. Use "-" (YAML requires the quotes) for memory-only.
              db_file: data.sqlite
              log_path: /var/log/bitburrow
              # Log verbosity: 0=critical only; 1=errors; 2=warnings; 3=info; 4=debug
              # Log level can be overwritten via CLI options.
              log_level: 3
              site_code: {site_code}
              # Domain to access hub and for VPN client subdomains, e.g. vxm.example.org
              domain: {domain}
              # Public IP address(es).
              public_ips:
              - {public_ip}
            http:
              enable_https: false
            wireguard:
              # UDP port(s) used by VPN bases.
              ports:
              - {wg_port}
            advanced:
              config_file_version: {config_fv}
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
