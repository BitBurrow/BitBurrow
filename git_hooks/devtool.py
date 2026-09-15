#!/usr/bin/env python3

import base64
import collections
from datetime import datetime as DateTime, timezone as TimeZone
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import yaml  # sudo apt install python3-yaml

bbbased_path = Path('hub/bbbased.lua')
versions_path = Path('versions.yaml')
watched_paths = ('hub/bbbased.lua', 'hub/adopt5p.sh', 'hub/')
keys_path = Path.home() / '.config/openssl/bitburrow'  # contains a subdir for each key
key_state_path = keys_path / 'key_state.yaml'


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


def represent_versions_string(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    return dumper.represent_scalar(
        'tag:yaml.org,2002:str', value, style='|' if '\n' in value else None
    )


Dumper.add_representer(str, represent_versions_string)


def stdout_from(args: str | list[str], *, input: str | None = None) -> str:
    '''Return command output unchanged; raise Berror if the command fails.'''
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


def git_text(*args: str, input: str | None = None) -> str:
    return stdout_from(['git', *args], input=input)


def git_blob(revision: str) -> str | None:
    '''Read a committed blob, allowing absent history or an absent file during bootstrap.'''
    result = subprocess.run(
        ['git', 'show', revision], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode == 128:
        return None
    result.check_returncode()
    return result.stdout.decode('utf-8', errors='surrogateescape')


def git_files(*args: str) -> set[str]:
    '''Return filenames from a Git command whose arguments include -z.'''
    return {name for name in git_text(*args).split('\0') if name}


def staged_files() -> set[str]:
    return git_files('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z')


def read_indexed_file(path: Path) -> tuple[str, str] | None:
    '''Return the index mode and contents, including files which are unchanged from HEAD.'''
    records = git_text('ls-files', '--stage', '-z', '--', str(path)).split('\0')
    records = [record for record in records if record]
    if not records:
        return None
    if len(records) != 1:
        raise Berror(f"B64531 resolve the index conflict for {path} before signing")
    metadata, _ = records[0].split('\t', 1)
    mode, object_id, stage = metadata.split()
    if stage != '0' or mode not in ('100644', '100755'):
        raise Berror(f"B81277 expected a regular, conflict-free indexed file at {path}")
    return mode, git_text('cat-file', 'blob', object_id)


def stage_contents(files: dict[Path, tuple[str, str]]) -> None:
    '''Install prepared blobs together, without reading or staging worktree contents.'''
    entries = []
    for path, (mode, content) in files.items():
        object_id = git_text('hash-object', '-w', '--stdin', input=content).strip()
        entries.append(f'{mode} {object_id}\t{path}\0')
    git_text('update-index', '-z', '--index-info', input=''.join(entries))


def atomic_write(path: Path, content: str, mode: int | None = None) -> None:
    '''Replace path atomically, retaining its permissions or defaulting to owner-only.'''
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
    '''Read text without translating line endings or losing non-UTF-8 source bytes.'''
    return path.read_bytes().decode('utf-8', errors='surrogateescape')


def parse_yaml(content: str, path: Path, *, allow_empty: bool = False) -> dict:
    try:
        value = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise Berror(f"B33898 unable to parse {path}: {error}")
    if value is None and allow_empty:
        return dict()
    if not isinstance(value, dict):
        raise Berror(f"B40581 expected a YAML mapping in {path}")
    return value


def b36datetime() -> str:
    '''Return Unix time in base 36, padded to seven characters (one-second resolution).'''
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    now = int(DateTime.now(TimeZone.utc).timestamp())
    result = ''
    while now:
        result = digits[now % 36] + result
        now //= 36
    return result.rjust(7, '0')


def entry_public_key(entry: re.Match) -> str:
    payload = ''.join(re.findall(r"'([^'\r\n]*)'", entry['pubkey']))
    return ''.join(payload.split())


def mask_lua_noncode(code: str, keep_strings: bool = False) -> str:
    '''Blank comments and optionally strings while retaining offsets and line endings.'''
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
        # Skip whole strings before searching again: PEM delimiters contain '--', and string
        # literals can contain examples of key entries or even an entire table declaration.
        if is_comment or not keep_strings:
            masked[start:position] = re.sub(r'[^\r\n]', ' ', code[start:position])
    return ''.join(masked)


def file_pubkey_entries(code: str) -> list[re.Match]:
    table = file_pubkeys_table(code)
    # Mask the whole file first: a block comment can begin on the table's declaration line.
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
    directory = keys_path / key_id
    for filename in ('privkey.pem', 'pubkey.pem'):
        if not (directory / filename).is_file():
            raise Berror(f"B53611 missing signing-key file: {directory / filename}")
    args = ['openssl', 'pkey', '-pubin', '-in', str(directory / 'pubkey.pem'), '-pubout']
    return Key(key_id, directory, stdout_from(args))


def validate_key_state(state: dict) -> dict:
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
    if not key_state_path.is_file():
        raise Berror(f"B26681 missing signing-key state file: {key_state_path}")
    return validate_key_state(parse_yaml(read_text(key_state_path), key_state_path))


def write_key_state(state: dict) -> None:
    text = yaml.safe_dump(validate_key_state(state), default_flow_style=False, sort_keys=False)
    atomic_write(key_state_path, text)


def dump_versions(versions: dict) -> str:
    return yaml.dump(
        versions,
        Dumper=Dumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def parse_versions(content: str) -> dict:
    versions = parse_yaml(content, versions_path, allow_empty=True)
    paths = versions.get('paths')
    if paths is None:
        paths = list()
        versions['paths'] = paths
    if not isinstance(paths, list) or any(not isinstance(entry, dict) for entry in paths):
        raise Berror(f"B33166 expected 'paths' in {versions_path} to be a list of mappings")
    return versions


def matching_watched_paths(files: set[str]) -> list[str]:
    return [
        watched
        for watched in watched_paths
        if watched in files
        or (watched.endswith('/') and any(name.startswith(watched) for name in files))
    ]


def set_bbbased_commit_date(code: str, new_commit_date: str) -> str:
    masked = mask_lua_noncode(code)
    matches = [  # ignore comments, strings
        match
        for match in re.finditer(
            r'''^local[ \t]+commit_date[ \t]*=[ \t]*(['"])(?P<value>[^'"\r\n]*)\1''',
            code,
            re.MULTILINE,
        )
        if masked.startswith('local', match.start())
    ]
    if len(matches) != 1:
        raise Berror(f"B49932 expected 1 'commit_date', found {len(matches)} in {bbbased_path}")
    match = matches[0]
    return code[: match.start('value')] + new_commit_date + code[match.end('value') :]


def verify_black() -> None:
    # Black can fail with parse errors or warnings before its formatting summary
    stdout_from('poetry run black --check --line-length 100 --skip-string-normalization .')


def find_berror_dups() -> None:
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
    '''Return the active key and the key that must sign the next release.'''
    active_key = read_key(state['active_key_id'])
    parent_code = git_blob(f'HEAD~:{bbbased_path}') or ''
    if state.get('previous_key_id') and not code_has_key(parent_code, active_key):
        # HEAD might be amended; its parent must already know the new signer; otherwise use
        # the key that signed its introduction, also for the first ordinary follow-up commit
        return active_key, read_key(state['previous_key_id'])
    # with no previous key, the active key is also the bootstrap signing key
    return active_key, active_key


def prepare_key(key_id: str) -> Key:
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
            subprocess.run(
                [
                    'openssl',
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
                check=True,
            )
            generated_path.chmod(0o600)
            generated_path.replace(private_path)
    # an interrupted export may leave only the private key; reuse it rather than generating another
    if not public_path.exists():
        print(f"Generating new public key from private key {key_id} ...")
        public_key = stdout_from(['openssl', 'pkey', '-in', str(private_path), '-pubout'])
        if not public_key.startswith('-----BEGIN PUBLIC KEY-----\n'):
            raise Berror("B09240 OpenSSL did not export a PEM public key")
        atomic_write(public_path, public_key)
    return read_key(key_id)


def read_or_create_key_state(commit_date: str) -> dict:
    if key_state_path.is_file():
        state = read_key_state()
    else:
        keys_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Do not mistake a deleted state file on an existing system for a fresh installation.
        if any(
            not item.name.startswith(f'.{key_state_path.name}.') for item in keys_path.iterdir()
        ):
            raise Berror(f"B51396 {key_state_path} is missing, but {keys_path} is not empty")
        keys_path.chmod(0o700)
        state = {
            'active_key_id': commit_date,
            'bootstrap_pending': True,
        }
        write_key_state(state)  # write to file so, if interrupted, key generation uses same dir
    if state.get('bootstrap_pending'):
        prepare_key(state['active_key_id'])
    return state


def get_pending_key(state: dict, commit_date: str) -> Key | None:
    pending_id = state.get('pending_key_id')
    if pending_id is None:
        rotation_requested = (keys_path / 'rotate_keys_after_signing').exists()
        if not rotation_requested:
            return None
        pending_id = commit_date
        if (keys_path / pending_id).exists():  # long-term location for this key
            raise Berror(f"B42088 pending key {keys_path / pending_id} already exists")
        state['pending_key_id'] = pending_id  # write to key_state in case of interruption
        write_key_state(state)
    return prepare_key(pending_id)


def file_pubkeys_table(code: str) -> re.Match:
    pubkeys_table_re = re.compile(
        r'^local file_pubkeys[ \t]*=[ \t]*\{[^\r\n]*(?P<newline>\r?\n)(?P<body>.*?)^\}',
        re.MULTILINE | re.DOTALL,
    )
    tables = list(pubkeys_table_re.finditer(mask_lua_noncode(code)))
    if len(tables) != 1:
        raise Berror(f"B45802 expected 1 'file_pubkeys', found {len(tables)} in {bbbased_path}")
    # locate live Lua syntax, then return the original text so generated edits preserve comments
    table = pubkeys_table_re.fullmatch(code, tables[0].start(), tables[0].end())
    if table is None:
        raise Berror(f"B29342 unsupported 'file_pubkeys' declaration in {bbbased_path}")
    return table


def prepend_file_pubkey(code: str, key: Key) -> str:
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


def mirror_file_pubkeys(worktree_code: str, staged_code: str) -> str:
    table = file_pubkeys_table(worktree_code)
    body = file_pubkeys_table(staged_code)['body']
    # preserve the worktree's surrounding code and declaration comments, even during partial
    # staging; the generated table body is deliberately replaced with the staged value
    return worktree_code[: table.start('body')] + body + worktree_code[table.end('body') :]


def sign_code(code: str, signing_key: Key) -> str:
    rsa_pss_options = (
        '-sigopt',
        'rsa_padding_mode:pss',
        '-sigopt',
        'rsa_pss_saltlen:64',
        '-sigopt',
        'rsa_mgf1_md:sha512',
    )
    privkey_path = str(signing_key.private_path)
    print(f"Signing code with private key {signing_key.key_id} ...")
    args = ['openssl', 'dgst', '-sha512', '-sign', privkey_path, *rsa_pss_options, '-']
    # only the raw OpenSSL signature is binary; expose its base64 representation as text
    signature = subprocess.run(
        args,
        input=code.encode('utf-8', errors='surrogateescape'),
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    return textwrap.fill(base64.b64encode(signature).decode('ascii'), width=64)


def sign(commit_date: str | None = None) -> None:
    '''Prepare staged release metadata, then mirror generated values into the worktree.'''
    if commit_date is None:
        commit_date = b36datetime()
    if not re.fullmatch('[0-9a-z]{7}', commit_date):
        raise Berror(f"B34290 expected a seven-character base-36 commit date: {commit_date!r}")
    modified = staged_files()
    indexed_versions = read_indexed_file(versions_path)
    if indexed_versions is None:
        template = read_text(versions_path)  # a missing worktree template is an error
        versions_mode = '100755' if versions_path.stat().st_mode & stat.S_IXUSR else '100644'
    else:
        versions_mode, template = indexed_versions
    versions = parse_versions(template)
    matched = matching_watched_paths(modified)
    versions['paths'] = [entry for entry in versions['paths'] if entry.get('path') not in matched]
    versions['paths'].extend({'path': path, 'version': commit_date} for path in matched)
    # unaffected entries retain their template values, even after an abandoned commit;
    # repairing stale metadata after changing the staged selection remains a manual operation
    prepared = {}
    worktree_code = None
    pending_key = None
    if str(bbbased_path) in modified:
        indexed = read_indexed_file(bbbased_path)
        if indexed is None:
            raise Berror(f"B50280 missing indexed file {bbbased_path}")
        code_mode, code = indexed
        code = set_bbbased_commit_date(code, commit_date)
        worktree_code = set_bbbased_commit_date(read_text(bbbased_path), commit_date)
        # Check both editable regions before generating keys or changing repository files.
        file_pubkeys_table(code)
        file_pubkeys_table(worktree_code)
        if shutil.which('openssl') is None:
            raise Berror("B71974 missing OpenSSL; install via: sudo apt install openssl")
        state = read_or_create_key_state(commit_date)
        active_key, signing_key = select_signing_keys(state)
        if state.get('bootstrap_pending'):
            # The initial trust root is self-signed; a later release can introduce a pending key.
            code = prepend_file_pubkey(code, active_key)
        else:
            pending_key = get_pending_key(state, commit_date)
        if pending_key is not None:
            if ''.join(pending_key.public_key.split()) == ''.join(active_key.public_key.split()):
                raise Berror("B16393 the pending public key is already the active public key")
            code = prepend_file_pubkey(code, pending_key)
        if not code_has_key(code, active_key):
            raise Berror("B48906 the release must retain the active public key and its key ID")
        worktree_code = mirror_file_pubkeys(worktree_code, code)
        entry = next(entry for entry in versions['paths'] if entry.get('path') == str(bbbased_path))
        entry.update(signature=sign_code(code, signing_key), key_id=signing_key.key_id)
        prepared[bbbased_path] = (code_mode, code)
    prepared[versions_path] = (versions_mode, dump_versions(versions))
    # all parsing and signing have succeeded; write the prepared index blobs in one operation,
    # then mirror generated values; interruptions during these writes need to be repaired manually
    stage_contents(prepared)
    atomic_write(versions_path, prepared[versions_path][1])
    if worktree_code is not None:
        atomic_write(bbbased_path, worktree_code)
    if pending_key is not None:
        # the saved pending ID preserves this rotation request across abandoned commits
        (keys_path / 'rotate_keys_after_signing').unlink(missing_ok=True)


def rotate_keys() -> None:
    if not key_state_path.exists():
        return  # this commit did not use the signing hooks
    state = read_key_state()
    pending_id = state.get('pending_key_id')
    if pending_id is None and not state.get('bootstrap_pending'):
        return
    committed_code = git_blob(f'HEAD:{bbbased_path}')
    committed_versions = git_blob(f'HEAD:{versions_path}')
    if committed_code is None or committed_versions is None:
        return
    key = read_key(pending_id or state['active_key_id'])
    if not code_has_key(committed_code, key):
        return
    versions = parse_versions(committed_versions)
    entry = next((e for e in versions['paths'] if e.get('path') == str(bbbased_path)), {})
    signing_id = entry.get('key_id')
    signing_ids = {state['active_key_id'], state.get('previous_key_id')} - {None}
    signature = entry.get('signature')
    if (
        not isinstance(signing_id, str)
        or signing_id not in signing_ids
        or not isinstance(signature, str)
        or not signature.strip()
    ):
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
    'sign': (sign, (0, 1)),
    'rotate-keys': (rotate_keys, (0,)),
}


def main() -> None:
    os.chdir(git_text('rev-parse', '--show-toplevel').removesuffix('\n'))
    operation = sys.argv[1] if len(sys.argv) > 1 else ''
    if operation not in commands:
        raise Berror(f"B61791 unknown operation {operation!r} (expected: {', '.join(commands)})")
    function, argument_counts = commands[operation]
    arguments = sys.argv[2:]
    if len(arguments) not in argument_counts:
        expected = ' or '.join(str(count) for count in argument_counts)
        raise Berror(f"B86683 {operation} expects {expected} argument(s)")
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
