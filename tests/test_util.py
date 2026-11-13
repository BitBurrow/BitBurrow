from pathlib import Path
import subprocess
import pytest
import hub.util as util


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_adopt5c_code_prefixes(scheme):
    code = util.adopt5c_shell_code('x' * 44, 'test-ott', f'{scheme}://hub.example.org/5l/test')
    lua_path = Path(util.project_root_path) / 'hub/base.lua'
    in_prefixes = False
    prefixes = []
    for line in lua_path.read_text(encoding='utf-8').splitlines():
        line = line.split('--', 1)[0].strip()
        if not in_prefixes:
            in_prefixes = line == 'local prefixes = {'
            continue
        if line == '}':
            break
        assert line.endswith(','), f"Malformed Lua prefix line: {line!r}"
        value = line[:-1].strip()
        assert len(value) >= 2 and value[0] == value[-1] == "'", value
        prefixes.append(value[1:-1])
    else:
        pytest.fail("Missing or unterminated Lua prefixes table")
    assert prefixes
    lines = code.splitlines()
    assert len(lines) == len(prefixes)
    for line_num, (line, prefix) in enumerate(zip(lines, prefixes), start=1):
        assert line.startswith(prefix), f"Line {line_num}: {line!r}, expected prefix {prefix!r}"


@pytest.mark.parametrize('shell', ['bash', 'sh'])
@pytest.mark.parametrize(
    'ip_out,expected',
    [
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
    ],
)
def test_fix_lan_overlap_shell_code(shell, ip_out, expected):
    code = util.fix_lan_overlap_shell_code()
    prefix = 'if ip -4 addr |{\n'
    suffix = '\n}; then\n'
    start = code.index(prefix) + len(prefix)
    end = code.index(suffix, start)
    # Execute only the detector using captured ip output.
    script = 'if (\n' + code[start:end] + '\n); then echo overlap; else echo no-overlap; fi\n'
    result = subprocess.run(
        [shell, '-c', script],
        input=ip_out,
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    assert result.stdout.strip() == expected, result.stderr
