#!/bin/sh

download_url='{download_url}'
log_err_route='{log_err_route}'
packager_exe=''
temp_path=''
bbbased_path=''
lua_exe=''

log_error() {
    echo ">>>>> log_error $1"
    if command -v curl >/dev/null 2>&1; then
        curl -f --max-time 60 -X POST --data "$1" "$log_err_route" >/dev/null 2>&1 || true
        return 0
    fi
    wget -q -T 60 --post-data="$1" "$log_err_route" >/dev/null 2>&1 || true
    return 0
}

download() {
    echo ">>>>> download $1 $2"
    if command -v curl >/dev/null 2>&1; then
        curl -f --max-time 120 -o "$2" "$1" >/dev/null 2>&1
        return $?
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -q -T 120 -O "$2" "$1" >/dev/null 2>&1
        return $?
    fi
    return 1
}

find_packager_exe() {
    echo ">>>>> find_packager_exe"
    packager_exe=''
    for cmd in opkg apt-get apk dnf yum pacman zypper; do
        if command -v "$cmd" >/dev/null 2>&1; then
            packager_exe=$cmd
            return 0
        fi
    done
    log_error "B96632 cannot find package manager"
    return 1
}

run_as_root() {
    echo ">>>>> run_as_root $1"
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
    log_error "B24773 cannot run as root; trying as current user"
    "$@"
    return $?
}

find_lua_exe() {
    echo ">>>>> find_lua_exe"
    lua_exe=''
    for cmd in lua luajit lua5.5 lua55 lua5.4 lua54 lua5.3 lua53 lua5.2 lua52 lua5.1 lua51; do
        if command -v "$cmd" >/dev/null 2>&1; then
            lua_exe=$cmd
            return 0
        fi
    done
    return 1
}

packager() {
    echo ">>>>> packager $1 $2"
    case "$packager_exe:$1" in
        opkg:update)
            run_as_root opkg update
            ;;
        opkg:install)
            run_as_root opkg install "$2"
            ;;
        apt-get:update)
            run_as_root apt-get update
            ;;
        apt-get:install)
            run_as_root apt-get install -y "$2"
            ;;
        apk:update)
            run_as_root apk update
            ;;
        apk:install)
            run_as_root apk add "$2"
            ;;
        dnf:update)
            run_as_root dnf makecache
            ;;
        dnf:install)
            run_as_root dnf install -y "$2"
            ;;
        yum:update)
            run_as_root yum makecache
            ;;
        yum:install)
            run_as_root yum install -y "$2"
            ;;
        pacman:update)
            run_as_root pacman -Sy --noconfirm
            ;;
        pacman:install)
            run_as_root pacman -S --noconfirm "$2"
            ;;
        zypper:update)
            run_as_root zypper --non-interactive refresh
            ;;
        zypper:install)
            run_as_root zypper --non-interactive install "$2"
            ;;
        *)
            return 1
            ;;
    esac
}

make_temp_path() {
    echo ">>>>> make_temp_path"
    temp_path="$(mktemp -d "${TMPDIR:-/tmp}/bbbased.XXXXXX" 2>/dev/null)" && return 0
    temp_path="/tmp/bbbased.$$"
    mkdir -p "$temp_path"
    log_error "B58172 mktemp failed; using: $temp_path"
}

### set-up
make_temp_path
bbbased_path="$temp_path/bbbased.lua"
find_packager_exe || true

### install curl or wget
loop_count=0
while [ "$loop_count" -le 3 ]; do
    if command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; then break; fi
    if [ "$loop_count" -gt 0 ]; then
        packager update >/dev/null 2>&1 || true
    fi
    packager install curl >/dev/null 2>&1 || true
    if command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; then break; fi
    packager install wget >/dev/null 2>&1 || true
    if command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; then break; fi
    sleep 75
    loop_count=$((loop_count + 1))
done

### install Lua
loop_count=0
while [ "$loop_count" -le 3 ]; do
    if find_lua_exe; then break; fi
    if [ "$loop_count" -gt 0 ]; then
        packager update >/dev/null 2>&1 || true
    fi
    packager install lua >/dev/null 2>&1 || true
    if find_lua_exe; then break; fi
    packager install lua5.5 >/dev/null 2>&1 || true
    if find_lua_exe; then break; fi
    packager install lua55 >/dev/null 2>&1 || true
    if find_lua_exe; then break; fi
    packager install lua5.4 >/dev/null 2>&1 || true
    if find_lua_exe; then break; fi
    packager install lua54 >/dev/null 2>&1 || true
    if find_lua_exe; then break; fi
    packager install lua5.3 >/dev/null 2>&1 || true
    if find_lua_exe; then break; fi
    packager install lua53 >/dev/null 2>&1 || true
    if find_lua_exe; then break; fi
    packager install lua5.2 >/dev/null 2>&1 || true
    if find_lua_exe; then break; fi
    packager install lua52 >/dev/null 2>&1 || true
    if find_lua_exe; then break; fi
    packager install lua5.1 >/dev/null 2>&1 || true
    if find_lua_exe; then break; fi
    packager install lua51 >/dev/null 2>&1 || true
    if find_lua_exe; then break; fi
    sleep 75
    loop_count=$((loop_count + 1))
done
if [ "$loop_count" -gt 3 ]; then
    log_error "B59241 cannot install lua"
fi

### download Lua script
loop_count=0
while [ "$loop_count" -le 10 ]; do
    download "$download_url" "$bbbased_path" || true
    if [ -s "$bbbased_path" ]; then
        break
    fi
    sleep 75
    loop_count=$((loop_count + 1))
done

### run Lua script
if [ -s "$bbbased_path" ]; then
    echo ">>>>> run $lua_exe $bbbased_path"
    exec "$lua_exe" "$bbbased_path"
    log_error "B65151 cannot run: exec $lua_exe $bbbased_path"
    "$lua_exe" "$bbbased_path"
else
    log_error "B29909 cannot download $download_url"
fi
