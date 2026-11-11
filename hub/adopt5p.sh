#!/bin/sh

hub_config_data='
api_url={api_url}
download_url={download_url}
log_err_route={log_err_route}
ott_filename={ott_filename}
subd={subd}
'

hub_config() {
    while IFS='=' read -r hub_config_key hub_config_value; do
        if [ "$hub_config_key" = "$1" ]; then
            printf '%s\n' "$hub_config_value"
            return 0
        fi
    done <<HUB_CONFIG_EOF
$hub_config_data
HUB_CONFIG_EOF
    return 1
}

packager_exe=''
temp_path=''
bbbased_path=''
header_path=''
lua_exe=''

log_error() {
    echo "log_error $1" >&2  # the redirect to stderr fixes buffering issue on some systems
    if command -v curl >/dev/null 2>&1; then
        curl -f --max-time 60 -X POST --data "$1" "$(hub_config log_err_route)" >/dev/null 2>&1 || true
        return 0
    fi
    wget -q -T 60 --post-data="$1" "$(hub_config log_err_route)" >/dev/null 2>&1 || true
    return 0
}

can_download() {
    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1
}

download() {
    echo "downloading $1 $2" >&2
    if command -v curl >/dev/null 2>&1; then
        curl -f --max-time 120 --dump-header "$header_path" -o "$2" "$1" >/dev/null 2>&1 && return 0
        rm -f "$header_path"
        curl -f --max-time 120 -o "$2" "$1" >/dev/null 2>&1 && return 0
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -S -T 120 -O "$2" "$1" >/dev/null 2>"$header_path" && return 0
        rm -f "$header_path"
        wget -q -T 120 -O "$2" "$1" >/dev/null 2>&1 && return 0
    fi
    return 1
}

find_packager_exe() {
    packager_exe=''
    for cmd in apt-get apk opkg dnf yum pacman zypper; do
        if command -v "$cmd" >/dev/null 2>&1; then
            packager_exe=$cmd
            return 0
        fi
    done
    log_error "B96632 cannot find package manager"
    return 1
}

shell_quote() {
    printf "'"
    printf '%s' "$1" | sed "s/'/'\\\\''/g"
    printf "'"
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
        root_command=''
        for root_arg do
            quoted_arg=$(shell_quote "$root_arg") || return 1
            root_command="${root_command}${root_command:+ }${quoted_arg}"
        done
        su -c "$root_command"
        return $?
    fi
    log_error "B24773 cannot run as root; trying as current user"
    "$@"
    return $?
}

find_lua_exe() {
    lua_exe=''
    for cmd in lua luajit lua5.5 lua55 lua5.4 lua54 lua5.3 lua53 lua5.2 lua52 lua5.1 lua51; do
        if lua_exe=$(command -v "$cmd" 2>/dev/null); then
            case "$lua_exe" in
                /*) ;;
                *) lua_exe="$(pwd)/$lua_exe" ;;
            esac
            return 0
        fi
    done
    return 1
}

packager() {
    echo "packager $1 $2" >&2
    case "$packager_exe:$1" in
        apt-get:update)
            run_as_root apt-get update
            ;;
        apt-get:install)
            run_as_root apt-get install -y "$2"
            ;;
        apt-get:fix-broken)
            run_as_root apt-get install --fix-broken -y
            ;;
        apk:update)
            run_as_root apk update
            ;;
        apk:install)
            run_as_root apk add "$2"
            ;;
        opkg:update)
            run_as_root opkg update
            ;;
        opkg:install)
            run_as_root opkg install "$2"
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
    # temp directory is later removed in cleanup_temp_path()
    if temp_path=$(mktemp -d "${TMPDIR:-/tmp}/bbbased.XXXXXX" 2>/dev/null) &&
            [ -d "$temp_path" ]; then
        return 0
    fi
    temp_path=''
    for attempt in 1 2 3; do
        if IFS= read -r suffix 2>/dev/null < /proc/sys/kernel/random/uuid; then
            candidate="${TMPDIR:-/tmp}/bbbased.$suffix"
        else
            break
        fi
        if mkdir -m 700 "$candidate" 2>/dev/null; then
            temp_path=$candidate
            log_error "B58172 mktemp failed; using: $temp_path" || :
            return 0
        fi
    done
    log_error "B29784 cannot create a secure temporary directory"
    return 1
}

cleanup_temp_path() {
    if [ -n "$bbbased_path" ]; then
        rm -f "$bbbased_path" "$header_path" 2>/dev/null || true
    fi
    if [ -n "$temp_path" ]; then
        rmdir "$temp_path" 2>/dev/null || true
    fi
}

### set-up
sleep 1; echo "" >&2  # clean separation from curl output
echo "" >&2  # blank line
echo "installing BitBurow ..." >&2
make_temp_path || exit 1
bbbased_path="$temp_path/bbbased.lua"
header_path="$temp_path/headers"
trap cleanup_temp_path 0
find_packager_exe || true

### install curl or wget; response-header support is best-effort
echo "installing required packages" >&2
loop_count=0
while [ "$loop_count" -le 3 ]; do
    if can_download; then break; fi
    if [ "$loop_count" -gt 1 ]; then
        echo "error installing curl and wget; waiting 75 seconds to retry" >&2
        sleep 75
    fi
    if [ "$loop_count" -gt 0 ]; then
        packager update >/dev/null 2>&1 || true
    fi
    if [ "$loop_count" -gt 1 ]; then
        packager fix-broken >/dev/null 2>&1 || true
    fi
    packager install curl >/dev/null 2>&1 || true
    if can_download; then break; fi
    packager install wget >/dev/null 2>&1 || true
    if can_download; then break; fi
    loop_count=$((loop_count + 1))
done
if ! can_download; then
    log_error "B78473 cannot install curl or wget; trying the download anyway"
fi

### install Lua
loop_count=0
while [ "$loop_count" -le 3 ]; do
    if find_lua_exe; then break; fi
    if [ "$loop_count" -gt 1 ]; then
        echo "error installing lua; waiting 75 seconds to retry" >&2
        sleep 75
    fi
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
    loop_count=$((loop_count + 1))
done
if [ "$loop_count" -gt 3 ]; then
    log_error "B59241 cannot install lua"
fi

### download Lua script
loop_count=0
file_xver=''
while [ "$loop_count" -le 10 ]; do
    if download "$(hub_config download_url)" "$bbbased_path" && [ -s "$bbbased_path" ]; then
        file_xver=$(awk '
            $1 ~ /^HTTP\// { version = "" }
            tolower($1) == "x-bitburrow-file-version:" { version = $2 }
            END { sub(/\r$/, "", version); print version }
        ' "$header_path" 2>/dev/null) || file_xver=''
        break  # a successful download is enough; version metadata is optional
    fi
    rm -f "$bbbased_path"
    sleep 75
    loop_count=$((loop_count + 1))
done

### run Lua script
if [ -s "$bbbased_path" ]; then
    hub_config_data="$hub_config_data
file_xver=$file_xver
lua_exe=$lua_exe
"
    echo "running $lua_exe $bbbased_path install" >&2
    run_as_root env HUBCONF="$hub_config_data" TMPDIR="${TMPDIR:-/tmp}" \
        "$lua_exe" "$bbbased_path" install
else
    log_error "B29909 cannot download $(hub_config download_url)"
    exit 1
fi
