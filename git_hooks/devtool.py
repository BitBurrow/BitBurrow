#!/usr/bin/env python3

import base64
import collections
from datetime import datetime as DateTime, timezone as TimeZone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import yaml  # sudo apt install python3-yaml

base_lua_path = Path('base/base.lua')
base_tree_path = base_lua_path.with_name('tree.json')
signature_placeholder = 'y5Tf2ghvd2jTi9m21geAP'
keys_path = Path.home() / '.config/openssl/bitburrow'  # contains a subdir for each key
key_state_path = keys_path / 'key_state.yaml'
key_passphrases: dict[Path, str] = dict()  # keep each key's passphrase in memory


class Berror(Exception):
    pass


class Key:
    def __init__(self, key_id: str, directory: Path, public_key: str):
        self.key_id = key_id
        self.directory = directory
        self.public_key = public_key

    @property
    def private_path(self) -> Path:
        return self.directory / 'privkey.pem'


class Dumper(yaml.SafeDumper):  # fix YAML output formatting
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def represent_yaml_string(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    return dumper.represent_scalar(
        'tag:yaml.org,2002:str', value, style='|' if '\n' in value else None
    )


Dumper.add_representer(str, represent_yaml_string)


def run_external(args: str | list[str], *, input: str | None = None) -> str:
    """Return command output unchanged; raise Berror if the command fails."""
    if isinstance(args, str):  # can take a list or simple space-separated string
        args = args.split()  # caution: not using shlex.split(args)
    proc = subprocess.run(  # avoid subprocess text mode (would break signed Lua code)
        args,
        input=None if input is None else input.encode('utf-8', errors='surrogateescape'),
        capture_output=True,
    )
    if proc.returncode != 0:
        e = (proc.stderr.strip() or proc.stdout.strip()).decode('utf-8', errors='replace')
        raise Berror(f"B76779 running {args} failed: {e}")
    return proc.stdout.decode('utf-8', errors='surrogateescape')


def run_openssl(
    args: list[str], private_path: Path, *, input: bytes | None = None, new_key: bool = False
) -> bytes:
    """Return OpenSSL output bytes using cached passphrases, confirming new ones."""
    private_path = private_path.resolve()
    passphrase = key_passphrases.get(private_path)
    if passphrase is None or new_key:
        passphrase = ''
        if new_key or b'ENCRYPTED' in private_path.read_bytes():
            action = 'New private key' if new_key else 'Private key'
            try:
                with sys.modules['warnings'].catch_warnings(  # reuse warnings loaded by getpass
                    action='error', category=getpass.GetPassWarning
                ):
                    passphrase = getpass.getpass(
                        f"{action} passphrase ({private_path.parent.name}): "
                    )
                    if new_key:
                        if len(passphrase) < 8:
                            raise Berror("B75324 new key passphrase must be at least 8 characters")
                        if getpass.getpass("Confirm private key passphrase: ") != passphrase:
                            raise Berror("B29041 private key passphrases do not match")
            except (getpass.GetPassWarning, EOFError):
                raise Berror("B51308 read passphrase from the terminal without echo")
    reader, writer = os.pipe()
    with os.fdopen(reader, 'rb') as passin, os.fdopen(writer, 'wb') as passout:
        # signed output uses stdout; password on separate pipe
        passout.write((passphrase + '\n').encode('utf-8'))
        passout.close()
        option = '-pass' if new_key else '-passin'
        output = subprocess.run(
            ['openssl', *args, option, f'fd:{passin.fileno()}'],
            input=input,
            check=True,
            stdout=subprocess.PIPE,
            pass_fds=(passin.fileno(),),
        ).stdout
    key_passphrases[private_path] = passphrase
    return output


def git_text(*args: str, input: str | None = None) -> str:
    return run_external(['git', *args], input=input)


def git_blob(revision: str) -> str | None:
    """Read a committed blob, allowing absent history or an absent file during bootstrap."""
    args = ['git', 'show', revision]
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 128:
        return None
    result.check_returncode()
    return result.stdout.decode('utf-8', errors='surrogateescape')


def git_files(*args: str) -> set[str]:
    """Return filenames from a Git command whose arguments include -z."""
    return {name for name in git_text(*args).split('\0') if name}


def indexed_blobs() -> dict[Path, tuple[str, str]]:
    """Snapshot the commit index, including modes and blob IDs and excluding intent-to-add."""
    tree_id = git_text('write-tree').strip()
    blobs = {}
    for record in git_text('ls-tree', '-rz', tree_id).split('\0'):
        if record:
            metadata, name = record.split('\t', 1)
            mode, kind, oid = metadata.split()
            if kind == 'blob':
                blobs[Path(name)] = (mode, oid)
    return blobs


def stage_contents(files: dict[Path, tuple[str, str]]) -> None:
    """Install prepared blobs together, without reading or staging worktree contents."""
    entries = list()
    for path, (mode, content) in files.items():
        object_id = git_text('hash-object', '-w', '--stdin', input=content).strip()
        entries.append(f'{mode} {object_id}\t{path}\0')
    git_text('update-index', '-z', '--index-info', input=''.join(entries))


def atomic_write(path: Path, content: str, mode: int | None = None) -> None:
    """Replace path atomically, retaining its permissions or defaulting to owner-only."""
    temporary = tempfile.NamedTemporaryFile(dir=path.parent, prefix=f'.{path.name}.', delete=False)
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            temporary.write(content.encode('utf-8', errors='surrogateescape'))
        if mode is None:
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
        temporary_path.chmod(mode)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_text(path: Path) -> str:
    return path.read_bytes().decode('utf-8', errors='surrogateescape')


def parse_yaml(content: str, path: Path) -> dict:
    try:
        value = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise Berror(f"B33898 unable to parse {path}: {error}")
    if not isinstance(value, dict):
        raise Berror(f"B40581 expected a YAML mapping in {path}")
    return value


def b36datetime() -> str:
    """Return Unix time in base 36, padded to seven characters (one-second resolution)."""
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    now = int(DateTime.now(TimeZone.utc).timestamp())
    result = ''
    while now:
        result = digits[now % 36] + result
        now //= 36
    return result.rjust(7, '0')


def entry_public_key(entry: re.Match) -> str:
    """Extract public key from a matched Lua key entry; strip whitespace."""
    payload = ''.join(re.findall(r"'([^'\r\n]*)'", entry['pubkey']))
    return ''.join(payload.split())


def mask_lua_noncode(code: str, keep_strings: bool = False) -> str:
    """Blank comments and optionally strings while retaining offsets and line endings."""
    masked = list(code)
    position = 0
    lua_token_re = re.compile(r'''--|\[(=*)\[|['"]''')
    lua_long_open_re = re.compile(r'\[(=*)\[')
    lua_short_string_re = re.compile(
        r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', re.DOTALL | re.VERBOSE
    )
    while token := lua_token_re.search(code, position):
        start = token.start()
        is_comment = token[0] == '--'
        opening = lua_long_open_re.match(code, start + 2 if is_comment else start)
        if opening is not None:
            closing = ']' + opening[1] + ']'
            end = code.find(closing, opening.end())
            position = len(code) if end < 0 else end + len(closing)
        elif is_comment:
            ending = re.search(r'[\r\n]', code[token.end() :])
            position = len(code) if ending is None else token.end() + ending.start()
        else:
            string = lua_short_string_re.match(code, start)
            position = len(code) if string is None else string.end()
        # skip whole strings before searching again: PEM delimiters contain '--', and string
        # literals can contain examples of key entries or even an entire table declaration
        if is_comment or not keep_strings:
            masked[start:position] = re.sub(r'[^\r\n]', ' ', code[start:position])
    return ''.join(masked)


def file_pubkey_entries(code: str) -> list[re.Match]:
    """Return validated Lua key-entry matches with offsets relative to the table body."""
    table = file_pubkeys_table(code)
    # mask the whole file first: a block comment can begin on the table's declaration line
    body = mask_lua_noncode(code, keep_strings=True)[table.start('body') : table.end('body')]
    pubkey_entry_re = re.compile(
        r'''^[ \t]*\{[ \t]*\r?\n
        [ \t]*key_id[ \t]*=[ \t]*'(?P<key_id>[0-9a-z]{7})',[ \t]*\r?\n
        [ \t]*pubkey[ \t]*=[ \t]*table\.concat\(\{[ \t]*\r?\n
        (?P<pubkey>(?:[ \t]*'[^'\r\n]*',[ \t]*\r?\n)*)
        [ \t]*\},[ \t]*'\\n'\),[ \t]*\r?\n
        [ \t]*\},[ \t]*(?:\r?\n|$)''',
        re.MULTILINE | re.VERBOSE,
    )
    entries = list(pubkey_entry_re.finditer(body))
    if pubkey_entry_re.sub('', body).strip():
        raise Berror("B34495 unexpected entry in file_pubkeys")
    return entries


def code_has_key(code: str, key: Key) -> bool:
    """Return whether the Lua trust table maps the key ID to the given public key."""
    try:
        entries = file_pubkey_entries(code)
    except Berror:
        return False  # older commits may have no table or use an unsupported table layout
    normalized = ''.join(key.public_key.split())
    for entry in entries:
        if entry['key_id'] == key.key_id:
            return entry_public_key(entry) == normalized
    return False


def read_key(key_id: str) -> Key:
    """Load key metadata and public PEM text, requiring both key files to exist."""
    directory = keys_path / key_id
    for filename in ('privkey.pem', 'pubkey.pem'):
        if not (directory / filename).is_file():
            raise Berror(f"B53611 missing signing-key file: {directory / filename}")
    args = ['openssl', 'pkey', '-pubin', '-in', str(directory / 'pubkey.pem'), '-pubout']
    return Key(key_id, directory, run_external(args))


def validate_key_state(state: dict) -> dict:
    """Validate key IDs and bootstrap or rotation state, then return the mapping."""
    for name in ('active_key_id', 'pending_key_id', 'previous_key_id'):
        key_id = state.get(name)
        if name != 'active_key_id' and key_id is None:
            continue
        if not isinstance(key_id, str) or not re.fullmatch('[0-9a-z]{7}', key_id):
            raise Berror(f"B28016 invalid {name} in {key_state_path}: {key_id!r}")
    active_id = state.get('active_key_id')
    pending_id = state.get('pending_key_id')
    if pending_id is not None and pending_id == active_id:
        raise Berror(f"B84646 active and pending key IDs are identical in {key_state_path}")
    bootstrap_pending = state.get('bootstrap_pending', False)
    if not isinstance(bootstrap_pending, bool):
        raise Berror(f"B72376 invalid bootstrap state in {key_state_path}")
    previous_id = state.get('previous_key_id')
    if bootstrap_pending and (pending_id is not None or previous_id is not None):
        raise Berror(f"B75212 bootstrap state cannot include key-rotation in {key_state_path}")
    return state


def read_key_state() -> dict:
    """Read and validate the signing-key state file."""
    if not key_state_path.is_file():
        raise Berror(f"B26681 missing signing-key state file: {key_state_path}")
    return validate_key_state(parse_yaml(read_text(key_state_path), key_state_path))


def write_key_state(state: dict) -> None:
    """Validate and atomically write the signing-key state as YAML."""
    text = yaml.safe_dump(validate_key_state(state), default_flow_style=False, sort_keys=False)
    atomic_write(key_state_path, text)


def dump_yaml(versions: dict) -> str:
    """Serialize metadata as YAML with indented lists and readable multiline strings."""
    return yaml.dump(
        versions,
        Dumper=Dumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def verify_black() -> None:
    """Check repository formatting with the configured Black options."""
    # Black can fail with parse errors or warnings before its formatting summary
    run_external('poetry run black --check --line-length 100 --skip-string-normalization .')


def find_berror_dups() -> None:
    """Reject duplicate Berror codes in tracked Python/Dart files and staged paths."""
    to_scan = git_files('ls-files', '-z', '--', '*.py', '*.dart') | git_files(
        'diff', '--name-only', '--cached', '-z'
    )
    code_counts = collections.Counter()
    for filename in sorted(to_scan):
        path = Path(filename)
        if path.is_file() and not path.is_symlink():
            code_counts.update(re.findall(r'B[0-9]{5} ', read_text(path)))
    duplicates = sorted(code for code, count in code_counts.items() if count > 1)
    if duplicates:
        raise Berror(f"B29348 please remove duplicate Berror code(s): {''.join(duplicates)}")


def select_signing_keys(state: dict) -> tuple[Key, Key]:
    """Return the active key and the key that must sign the next release."""
    active_key = read_key(state['active_key_id'])
    if state.get('previous_key_id'):
        # continue using the old signer through the first follow-up base release
        recent = git_text(
            'log', '--first-parent', '-2', '--format=%H', '--', str(base_tree_path)
        ).splitlines()
        older_code = git_blob(f'{recent[1]}:{base_lua_path}') if len(recent) == 2 else None
        if not code_has_key(older_code or '', active_key):
            return active_key, read_key(state['previous_key_id'])
    return active_key, active_key


def prepare_key(key_id: str) -> Key:
    """Create missing key files and return the key, reusing any existing private key."""
    key_path = keys_path / key_id
    key_path.mkdir(mode=0o700, exist_ok=True)
    private_path = key_path / 'privkey.pem'
    public_path = key_path / 'pubkey.pem'
    if not private_path.exists():
        if public_path.exists():
            raise Berror(f"B06477 the public key in {key_path} has no private key")
        print(f"Generating new private key {key_id} ...")
        with tempfile.TemporaryDirectory(prefix='.keygen-', dir=key_path) as temporary:
            generated_path = Path(temporary) / 'privkey.pem'
            run_openssl(
                [
                    'genpkey',
                    '-algorithm',
                    'RSA-PSS',
                    '-pkeyopt',
                    'rsa_keygen_bits:3072',
                    '-pkeyopt',
                    'rsa_pss_keygen_md:sha512',
                    '-pkeyopt',
                    'rsa_pss_keygen_mgf1_md:sha512',
                    '-pkeyopt',
                    'rsa_pss_keygen_saltlen:64',
                    '-aes-256-cbc',
                    '-out',
                    str(generated_path),
                ],
                private_path,
                new_key=True,
            )
            generated_path.chmod(0o600)
            generated_path.replace(private_path)
    # an interrupted export may leave only the private key; reuse it rather than generating another
    if not public_path.exists():
        print(f"Generating new public key from private key {key_id} ...")
        args = ['pkey', '-in', str(private_path), '-pubout']
        public_key = run_openssl(args, private_path).decode('utf-8')
        if not public_key.startswith('-----BEGIN PUBLIC KEY-----\n'):
            raise Berror("B09240 OpenSSL did not export a PEM public key")
        atomic_write(public_path, public_key)
    return read_key(key_id)


def read_or_create_key_state(file_cver: str) -> dict:
    """Load or initialize the local machine key state, preparing the bootstrap key if needed."""
    if key_state_path.is_file():
        state = read_key_state()
    else:
        keys_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        # do not mistake a deleted state file on an existing system for a fresh installation
        if any(
            not item.name.startswith(f'.{key_state_path.name}.') for item in keys_path.iterdir()
        ):
            raise Berror(f"B51396 {key_state_path} is missing, but {keys_path} is not empty")
        keys_path.chmod(0o700)
        state = {
            'active_key_id': file_cver,
            'bootstrap_pending': True,
        }
        write_key_state(state)  # write to file so, if interrupted, key generation uses same dir
    if state.get('bootstrap_pending'):
        prepare_key(state['active_key_id'])
    return state


def get_pending_key(state: dict, file_cver: str) -> Key | None:
    """Return or create the pending rotation key, or None if no rotation is requested."""
    pending_id = state.get('pending_key_id')
    if pending_id is None:
        rotation_requested = (keys_path / 'rotate_keys_after_signing').exists()
        if not rotation_requested:
            return None
        pending_id = file_cver
        if (keys_path / pending_id).exists():  # long-term location for this key
            raise Berror(f"B42088 pending key {keys_path / pending_id} already exists")
        state['pending_key_id'] = pending_id  # write to key_state in case of interruption
        write_key_state(state)
    return prepare_key(pending_id)


def file_pubkeys_table(code: str) -> re.Match:
    """Locate the single live Lua file_pubkeys table and return its source match."""
    pubkeys_table_re = re.compile(
        r'^local file_pubkeys[ \t]*=[ \t]*\{[^\r\n]*(?P<newline>\r?\n)(?P<body>.*?)^\}',
        re.MULTILINE | re.DOTALL,
    )
    tables = list(pubkeys_table_re.finditer(mask_lua_noncode(code)))
    if len(tables) != 1:
        raise Berror(f"B45802 expected 1 'file_pubkeys', found {len(tables)} in {base_lua_path}")
    # locate live Lua syntax, then return the original text so generated edits preserve comments
    table = pubkeys_table_re.fullmatch(code, tables[0].start(), tables[0].end())
    if table is None:
        raise Berror(f"B29342 unsupported 'file_pubkeys' declaration in {base_lua_path}")
    return table


def prepend_file_pubkey(code: str, key: Key) -> str:
    """Prepend a Lua public key, removing duplicates and rejecting conflicting IDs."""
    table = file_pubkeys_table(code)
    body = table['body']
    normalized = ''.join(key.public_key.split())
    # a retry replaces an earlier entry for this same pending key instead of duplicating it
    for entry in reversed(file_pubkey_entries(code)):
        if entry_public_key(entry) == normalized:
            body = body[: entry.start()] + body[entry.end() :]
        elif entry['key_id'] == key.key_id:
            raise Berror(f"B54827 public-key ID {key.key_id} belongs to a different key")
    lines = [
        '    {',
        f"        key_id = '{key.key_id}',",
        '        pubkey = table.concat({',
        *(f"            '{line}'," for line in key.public_key.splitlines()),
        "        }, '\\n'),",
        '    },',
    ]
    newline = table['newline']
    entry = newline.join(lines) + newline
    return code[: table.start('body')] + entry + body + code[table.end('body') :]


def sign_string(code: str, signing_key: Key) -> str:
    """Return a wrapped base64 RSA-PSS/SHA-512 signature of the supplied source text."""
    rsa_pss_options = (
        '-sigopt',
        'rsa_padding_mode:pss',
        '-sigopt',
        'rsa_pss_saltlen:64',
        '-sigopt',
        'rsa_mgf1_md:sha512',
    )
    privkey_path = str(signing_key.private_path)
    # # use stderr so we don't corrput YAML on stdout
    # print(f"Signing code with private key {signing_key.key_id} ...", file=sys.stderr)
    signature = run_openssl(
        ['dgst', '-sha512', '-sign', privkey_path, *rsa_pss_options],
        signing_key.private_path,
        input=code.encode('utf-8', errors='surrogateescape'),
    )
    return textwrap.fill(base64.b64encode(signature).decode('ascii'), width=64)


def sign_doc(subd: str, filename: str) -> None:
    """Sign shell content for one or more bases and write the task batch as YAML to stdout."""
    subds = subd.split()
    if not subds or any(not re.fullmatch('[a-z0-9]{1,63}', item) for item in subds):
        raise Berror(f"B79243 invalid subd: {subd!r}")
    if len(subds) != len(set(subds)):
        raise Berror("B81852 duplicate subd in signed task batch")
    content = (sys.stdin.buffer.read() if filename == '-' else Path(filename).read_bytes()).decode(
        'utf-8'
    )
    if '\0' in content:
        raise Berror("B48731 signed shell content cannot contain NUL bytes")
    signing_key = read_key(read_key_state()['active_key_id'])
    sequence_id = b36datetime()
    tasks = list()
    for task_id, subd in enumerate(subds, 1):
        signature_base = 'sabozt45m\n' + subd + '\n' + sequence_id + '\n' + content
        tasks.append(
            dict(
                id=task_id,
                subd=subd,
                content=content,
                sequence_id=sequence_id,
                signature=sign_string(signature_base, signing_key),
                key_id=signing_key.key_id,
            )
        )
    sys.stdout.write(dump_yaml(dict(tasks=tasks)))


def tree_object(pairs: list) -> dict:
    """Reject duplicate keys and signed children as the JSON decoder builds their parents."""
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("duplicate JSON key")
    children = list(obj.values())
    while children:
        child = children.pop()
        if isinstance(child, list):
            children.extend(child)
        elif isinstance(child, dict) and 'signature' in child:
            raise ValueError("signature is allowed only at the root")
    return obj


def updated_trees(
    blobs: dict[Path, tuple[str, str]], modified: set[Path], file_cver: str
) -> dict[Path, dict]:
    modified = modified.copy()
    if base_tree_path not in blobs and any(base_tree_path.parent in p.parents for p in modified):
        raise Berror(f"B50280 missing indexed file {base_tree_path}")
    directories = {directory for path in blobs for directory in path.parents}
    manifests = sorted(
        (p for p in blobs if p.name == 'tree.json'), key=lambda p: (-len(p.parts), p)
    )
    trees = {}
    for manifest in manifests:  # a generated child tree is a staged change for its ancestors
        changes = {p for p in modified if manifest.parent in p.parents}
        if not changes:
            continue
        mode, oid = blobs[manifest]
        if mode not in ('100644', '100755'):
            raise Berror(f"B81277 expected a regular indexed file at {manifest}")
        try:
            tree = json.loads(git_text('cat-file', 'blob', oid), object_pairs_hook=tree_object)
        except ValueError as e:
            raise Berror(f"B45386 invalid {manifest}: {e}") from e
        if not isinstance(tree, dict):
            raise Berror(f"B32015 expected a JSON object in {manifest}")
        if 'signature' in tree and 'key_id' not in tree:
            raise Berror(f"B57232 signature without key_id in {manifest}")
        changed_dirs = {d for p in changes - {manifest} for d in p.parents}
        entries = tree.get('paths', [])
        if not isinstance(entries, list) or any(
            not isinstance(e, dict) or not isinstance(e.get('path'), str) for e in entries
        ):
            raise Berror(f"B30478 expected path entries with string names in {manifest}")
        self_entries = [e for e in entries if e['path'] == 'tree.json']
        if manifest == base_tree_path and (not self_entries or 'cver' not in self_entries[-1]):
            raise Berror(f"B29520 missing tree.json cver in {manifest}")
        dirty = 'signature' in tree  # signing alone changes the manifest's own cver
        names = {'tree.json'}  # base payloads are installed in a single directory
        for entry in entries:
            name = entry['path']
            path = manifest.parent / name
            directory = name == '' or name.endswith('/')
            if name == '/' or path not in (directories if directory else blobs):
                raise Berror(f"B64531 invalid indexed path {name!r} in {manifest}")
            changed = path in (changed_dirs if directory else changes)
            if 'sha256sum' in entry:
                if directory or path.name == 'tree.json':
                    raise Berror(f"B12773 sha256sum not allowed for {name!r} in {manifest}")
                mode, oid = blobs[path]
                if manifest == base_tree_path:
                    if (
                        mode == '120000'
                        or name != Path(name).as_posix()  # curl would normalize this before request
                        or re.search(r'[^A-Za-z0-9_./-]', name)
                    ):
                        raise Berror(f"B80402 expected canonical, URL-safe regular file: {name!r}")
                    if path.name in names:
                        raise Berror(f"B80403 conflicting flattened filename {path.name!r}")
                    names.add(path.name)
                    if int(git_text('cat-file', '-s', oid)) > 654321:  # tag_base_max_dl_bytes
                        raise Berror(f"B80447 base file exceeds the download limit: {path}")
                if changed:
                    data = subprocess.check_output(['git', 'cat-file', 'blob', oid])
                    digest = hashlib.sha256(data).hexdigest()
                    if entry['sha256sum'] != digest:
                        entry['sha256sum'] = digest
                        dirty = True
            if changed and 'cver' in entry and entry['cver'] != file_cver:
                entry['cver'] = file_cver
                dirty = True
        if dirty:
            for entry in self_entries:
                if 'cver' in entry:
                    entry['cver'] = file_cver
            trees[manifest] = tree
            modified.add(manifest)
    return trees


def sign_code(file_cver: str | None = None) -> None:
    """Update affected indexed manifests using a single timestamp and signer."""
    file_cver = file_cver or b36datetime()
    if not re.fullmatch('[0-9a-z]{7}', file_cver):
        raise Berror(f"B34290 expected a seven-character base-36 commit date: {file_cver!r}")
    modified = {Path(p) for p in git_files('diff', '--cached', '--name-only', '--no-renames', '-z')}
    if not modified:
        return
    blobs = indexed_blobs()
    trees = updated_trees(blobs, modified, file_cver)
    prepared = {}
    bootstrap = False
    pending_key = None
    if any('signature' in tree for tree in trees.values()):
        state = read_or_create_key_state(file_cver)
        active_key, signing_key = select_signing_keys(state)
        bootstrap = state.get('bootstrap_pending', False)
        if not bootstrap:
            pending_key = get_pending_key(state, file_cver)
    if bootstrap or pending_key is not None or 'signature' in trees.get(base_tree_path, {}):
        if base_lua_path not in blobs:
            raise Berror(f"B39524 missing indexed file {base_lua_path}")
        mode, oid = blobs[base_lua_path]
        original = code = git_text('cat-file', 'blob', oid)
        if bootstrap:
            code = prepend_file_pubkey(code, active_key)
        if pending_key is not None:
            if ''.join(pending_key.public_key.split()) == ''.join(active_key.public_key.split()):
                raise Berror("B16393 the pending public key is already the active public key")
            code = prepend_file_pubkey(code, pending_key)
        if not code_has_key(code, active_key):
            raise Berror("B48906 the release must retain the active public key and its key ID")
        if signing_key is not active_key and not code_has_key(code, signing_key):
            raise Berror("B80446 the release must retain the public key used to sign it")
        if code != original:
            prepared[base_lua_path] = (mode, code)
            # reprocess affected tree.json files with the rotated base.lua before staging them
            oid = git_text('hash-object', '-w', '--stdin', input=code).strip()
            rotated = blobs | {base_lua_path: (mode, oid)}
            trees = updated_trees(rotated, modified | {base_lua_path}, file_cver)
    for path, tree in trees.items():
        if 'signature' in tree:
            # keep the serialized prefix in sync with verify_tree() in base.lua
            tree = {'signature': None, 'key_id': None, **tree}
            tree.update(signature=signature_placeholder, key_id=signing_key.key_id)
        content = json.dumps(tree, indent=2) + '\n'
        if 'signature' in tree:
            signature = ''.join(sign_string(content, signing_key).split())
            content = content.replace(signature_placeholder, signature, 1)
            if path == base_tree_path:
                prefix = f'{{\n  "signature": "{signature}",\n  "key_id": "{signing_key.key_id}"'
                if not content[:1024].startswith(prefix) or len(content) > 654321:
                    # search: tag_base_max_dl_bytes
                    raise Berror("B80444 signed tree exceeds base.lua format or size limits")
        prepared[path] = (blobs[path][0], content)
    if prepared:
        stage_contents(prepared)
        for path, (_, content) in prepared.items():
            # Leave partial staging, unstaged deletions and symlink replacements intact.
            if path.is_file() and path.resolve() == Path.cwd() / path:
                if read_text(path) == git_text('cat-file', 'blob', blobs[path][1]):
                    atomic_write(path, content)
    if pending_key is not None:
        (keys_path / 'rotate_keys_after_signing').unlink(missing_ok=True)


def rotate_keys() -> None:
    """Activate a bootstrap or pending key once committed code and metadata include it."""
    if not key_state_path.exists():
        return  # this commit did not use the signing hooks
    state = read_key_state()
    pending_id = state.get('pending_key_id')
    if pending_id is None and not state.get('bootstrap_pending'):
        return
    committed_code = git_blob(f'HEAD:{base_lua_path}')
    committed_tree = git_blob(f'HEAD:{base_tree_path}')
    if committed_code is None or committed_tree is None:
        return
    key = read_key(pending_id or state['active_key_id'])
    if not code_has_key(committed_code, key):
        return
    tree = json.loads(committed_tree)
    signing_id = tree.get('key_id')
    signing_ids = {state['active_key_id'], state.get('previous_key_id')} - {None}
    signature = tree.get('signature')
    if signing_id not in signing_ids or not signature or signature == signature_placeholder:
        return
    if pending_id is not None:
        state['previous_key_id'] = signing_id
        state['active_key_id'] = pending_id
        state.pop('pending_key_id')
    state.pop('bootstrap_pending', None)
    write_key_state(state)


commands = {
    'verify-black': (verify_black, (0,)),
    'find-berror-dups': (find_berror_dups, (0,)),
    'sign-code': (sign_code, (0, 1)),
    'sign-doc': (sign_doc, (2,)),
    'rotate-keys': (rotate_keys, (0,)),
}


def main() -> None:
    operation = sys.argv[1] if len(sys.argv) > 1 else ''
    if operation not in commands:
        raise Berror(f"B61791 unknown operation {operation!r} (expected: {', '.join(commands)})")
    function, argument_counts = commands[operation]
    arguments = sys.argv[2:]
    if len(arguments) not in argument_counts:
        expected = ' or '.join(str(count) for count in argument_counts)
        raise Berror(f"B86683 {operation} expects {expected} argument(s)")
    if operation != 'sign-doc':
        os.chdir(git_text('rev-parse', '--show-toplevel').removesuffix('\n'))
    function(*arguments)


if __name__ == '__main__':
    operation = sys.argv[1] if len(sys.argv) > 1 else ''
    try:
        main()
    except (Berror, OSError, ValueError, subprocess.CalledProcessError) as e:
        if operation == 'rotate-keys':
            message = (
                f"Post-commit key activation failed: {e}. The commit already exists; "
                "repair the key state before the next signing commit."
            )
        else:
            message = f"Git hook operation failed: {e}"
        print(message, file=sys.stderr)
        if isinstance(e, subprocess.CalledProcessError):
            raise SystemExit(e.returncode if e.returncode > 0 else 128 - e.returncode)
        raise SystemExit(1)
