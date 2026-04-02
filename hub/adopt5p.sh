#!/bin/sh

errors=''
pkg_mgr=''
install_target=''
download_url='{api_url}/bbbased.lua'
temp_dir=''
temp_lua=''

find_pkg_mgr() {
    pkg_mgr=''
    for cmd in opkg apt-get apk dnf yum pacman zypper; do
        if command -v "$cmd" >/dev/null 2>&1; then
            pkg_mgr=$cmd
            return 0
        fi
    done
    errors="B96632 $errors"  # cannot find package manager
}

run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
        return $?
    fi
    if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
        sudo "$@"
        return $?
    fi
    if command -v doas >/dev/null 2>&1 && doas -n true >/dev/null 2>&1; then
        doas "$@"
        return $?
    fi
    if command -v su >/dev/null 2>&1; then
        su -c "$(printf '%s ' "$@")"
        return $?
    fi
    echo "ERROR: cannot run as root" >&2
    return 1
}

cleanup() {
    if [ -n "$temp_lua" ] && [ -f "$temp_lua" ]; then
        rm -f "$temp_lua"
    fi
    if [ -n "$temp_dir" ] && [ -d "$temp_dir" ]; then
        rmdir "$temp_dir" >/dev/null 2>&1 || true
    fi
}

check_lua_stdout() {
    check_output="$(lua -e 'print("IAWYSIAMFSFF")' 2>/dev/null || true)"
    [ "$check_output" = 'IAWYSIAMFSFF' ]
}

set_install_target() {
    case $(( ($1 - 1) % 4 )) in
        0) install_target='lua' ;;
        1) install_target='lua5.1' ;;
        2) install_target='lua5.2' ;;
        3) install_target='lua5.3' ;;
    esac
}

pkg_mgr_do() {
    action=$1
    case "$pkg_mgr:$action" in
        opkg:update)
            run_as_root opkg update
            ;;
        opkg:install)
            run_as_root opkg install "$install_target"
            ;;
        apt-get:update)
            run_as_root apt-get update
            ;;
        apt-get:install)
            run_as_root apt-get install -y "$install_target"
            ;;
        apk:update)
            run_as_root apk update
            ;;
        apk:install)
            run_as_root apk add "$install_target"
            ;;
        dnf:update)
            run_as_root dnf makecache
            ;;
        dnf:install)
            run_as_root dnf install -y "$install_target"
            ;;
        yum:update)
            run_as_root yum makecache
            ;;
        yum:install)
            run_as_root yum install -y "$install_target"
            ;;
        pacman:update)
            run_as_root pacman -Sy --noconfirm
            ;;
        pacman:install)
            run_as_root pacman -S --noconfirm "$install_target"
            ;;
        zypper:update)
            run_as_root zypper --non-interactive refresh
            ;;
        zypper:install)
            run_as_root zypper --non-interactive install "$install_target"
            ;;
        *)
            return 1
            ;;
    esac
}

try_install_and_check() {
    pkg_mgr_do install >/dev/null 2>&1 || true
    check_lua_stdout
}

trap cleanup EXIT HUP INT TERM

temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/bbbased.XXXXXX" 2>/dev/null)" || {
    echo "ERROR: unable to create temp directory" >&2
    exit 1
}
temp_lua="$temp_dir/bbbased.lua"

install_pass=1
while [ "$install_pass" -le 20 ]; do
    set_install_target "$install_pass"

    if check_lua_stdout; then
        break
    fi
    find_pkg_mgr || true
    if try_install_and_check; then
        break
    fi
    pkg_mgr_do update >/dev/null 2>&1 || true
    if try_install_and_check; then
        break
    fi
    if try_install_and_check; then
        break
    fi
    if [ "$install_pass" -lt 20 ]; then
        sleep 75
    fi
    install_pass=$((install_pass + 1))
done

if ! check_lua_stdout; then
    echo "ERROR: unable to get a working lua command after 20 attempts" >&2
    exit 1
fi

download_pass=1
while [ "$download_pass" -le 10 ]; do
    if curl -fsSL "$download_url" -o "$temp_lua"; then
        break
    fi
    if [ "$download_pass" -lt 10 ]; then
        sleep 75
    fi
    download_pass=$((download_pass + 1))
done

if [ ! -s "$temp_lua" ]; then
    echo "ERROR: unable to download $download_url after 10 attempts" >&2
    exit 1
fi

exec lua "$temp_lua"
lua "$temp_lua"
exit $?