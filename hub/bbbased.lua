#!/usr/bin/lua

local nixio = require('nixio')
local fs = require('nixio.fs')

local config_dir = '/etc/bitburrow/'
local log_path = '/tmp/bitburrow.log'
local lock_dir = '/tmp/bbbased.lock/'
local lock_pid_path = lock_dir .. 'pid'
local api_url_path = config_dir .. 'api_url'
local subd_path = config_dir .. 'subd'
local token_path = config_dir .. 'token'
local auth_privkey_path = config_dir .. 'client_rsapss.pem'
local auth_pubkey_path = config_dir .. 'client_rsapss_pub.pem'
local wg_privkey_path = config_dir .. 'wgbb1_private.key'
local wg_pubkey_path = config_dir .. 'wgbb1_public.key'
local pubkeys_uploaded_path = config_dir .. 'pubkeys_uploaded'
local log_handle = nil

local function fail_early(message)
    io.stderr:write(message .. '\n')
    os.exit(1)
end

local function command_succeeded(ok, why, code)
    -- normalize return values for os.execute() and pipe:close() across Lua versions
    if ok == true then
        return true
    end
    if why == 'exit' and code == 0 then
        return true
    end
    if type(ok) == 'number' and why == nil and code == nil and ok == 0 then
        return true
    end
    return nil
end

local function open_log()
    local handle = io.open(log_path, 'w')
    if not handle then
        fail_early("B62762 cannot create: " .. log_path)
    end
    handle:close()
    fs.chmod(log_path, '0600')
    log_handle = io.open(log_path, 'a')
    if not log_handle then
        fail_early("B71356 cannot open for append: " .. log_path)
    end
end

local function close_log()
    if log_handle then
        log_handle:close()
    end
    log_handle = nil
end

local function log_line(line)
    if not log_handle then  -- use stderr when log file is closed
        io.stderr:write(line .. '\n')
        return
    end
    log_handle:write(os.date('!%Y-%m-%dT%H:%M:%SZ') .. ' ' .. line .. '\n')
    log_handle:flush()
end

local function shell_quote(value)
    return "'" .. tostring(value):gsub("'", "'\\''") .. "'"
end

local function http_quoted_string_escape(value)
    value = tostring(value)
    value = value:gsub('\\', '\\\\')
    value = value:gsub('"', '\\"')
    value = value:gsub('[%z\1-\31\127]', '')
    return value
end

local function run_command(command, merge_stderr)
    -- returns the captured output after stripping trailing whitespace, or nil on failure
    local pipe = io.popen(command .. (merge_stderr and ' 2>&1' or ''), 'r')
    if not pipe then
        log_line("B12747 unable to run: " .. command)
        return nil
    end
    local output = pipe:read('*a') or ''
    local ok, why, code = pipe:close()
    if command_succeeded(ok, why, code) then
        output = output:gsub('%s+$', '')
        return output
    end
    local exit_code = code
    if type(ok) == 'number' and why == nil and code == nil then
        exit_code = ok
    end
    if output ~= '' then
        output = output:gsub('%s+$', ''):gsub('\n', '\\n')
        log_line("B11840 running " .. command .. " failed: " .. output)
    else
        log_line("B11545 running " .. command .. " failed with exit code " .. tostring(exit_code))
    end
    return nil
end

local function read_text_file(path, empty_if_unreadable)
    -- returns file contents, or nil on failure
    local handle = io.open(path, 'r')
    if not handle then
        if empty_if_unreadable then
            return ''
        end
        log_line("B41834 cannot read file: " .. path)
        return nil
    end
    local content, read_err = handle:read('*a')
    local close_ok, close_err = handle:close()
    if content == nil then
        log_line("B21409 cannot read " .. path .. ": " .. tostring(read_err))
        return nil
    end
    if close_ok == nil then
        log_line("B55281 cannot close " .. path .. ": " .. tostring(close_err))
        return nil
    end
    content = content:gsub('%s+$', '')
    return content
end

local function write_text_file(path, content, mode)
    -- returns true iff successful
    local handle = io.open(path, 'w')
    if not handle then
        log_line("B38727 cannot write file: " .. path)
        return nil
    end
    local write_ok, write_err = handle:write(content)
    local close_ok, close_err = handle:close()
    if not write_ok then
        log_line("B93465 cannot write " .. path .. ": " .. tostring(write_err))
        return nil
    end
    if close_ok == nil then
        log_line("B14993 cannot close " .. path .. ": " .. tostring(close_err))
        return nil
    end
    if mode then
        fs.chmod(path, mode)
    end
    return true
end

local function make_temp_path()
    -- returns new temp path, or nil on failure
    local path = run_command('mktemp /tmp/bitburrow.XXXXXX', true)
    if not path or path == '' then
        log_line("B35286 mktemp failed")
        return nil
    end
    return path
end

local function remove_path(path)
    -- remove the file or empty directory, ignoring all errors
    if path and path ~= '' then
        os.remove(path)
    end
end

local function json_escape(value)
    value = tostring(value)
    value = value:gsub('\\', '\\\\')
    value = value:gsub('"', '\\"')
    value = value:gsub('\b', '\\b')
    value = value:gsub('\f', '\\f')
    value = value:gsub('\n', '\\n')
    value = value:gsub('\r', '\\r')
    value = value:gsub('\t', '\\t')
    value = value:gsub('[%z\1-\31]', function(char)
        return string.format('\\u%04x', char:byte())
    end)
    return value
end

local function json_unescape(value)
    -- minimal fixes, e.g. Unicode becomes '?'
    value = value:gsub('\\u(%x%x%x%x)', function(hex)
        local num = tonumber(hex, 16)
        if num and num < 128 then
            return string.char(num)
        end
        return '?'
    end)
    local placeholder = '\255'
    value = value:gsub('\\\\', placeholder)
    value = value:gsub('\\"', '"')
    value = value:gsub('\\/', '/')
    value = value:gsub('\\b', '\b')
    value = value:gsub('\\f', '\f')
    value = value:gsub('\\n', '\n')
    value = value:gsub('\\r', '\r')
    value = value:gsub('\\t', '\t')
    value = value:gsub(placeholder, '\\')  -- avoid '\\n' becoming a newline
    return value
end

local function file_mtime(path)
    local stat = fs.stat(path)
    if not stat then
        return nil
    end
    return stat.mtime
end

local function sleep_with_jitter(base_seconds, jitter_fraction)
    local min_seconds = math.floor(base_seconds * (1 - jitter_fraction))
    local max_seconds = math.ceil(base_seconds * (1 + jitter_fraction))
    if min_seconds < 0 then
        min_seconds = 0
    end
    if max_seconds < min_seconds then
        max_seconds = min_seconds
    end
    local sleep_seconds = math.random(min_seconds, max_seconds)
    nixio.nanosleep(sleep_seconds, 0)
end

local function ensure_root_and_single_instance()
    if nixio.getuid() ~= 0 then
        fail_early('must run as root')
    end
    local mkdir_ok = fs.mkdir(lock_dir)
    if not mkdir_ok then
        local existing_pid = read_text_file(lock_pid_path, false)
        if existing_pid and existing_pid:match('^%d+$') then
            local kill_ok, why, code =
                os.execute('kill -0 ' .. existing_pid .. ' >/dev/null 2>&1')
            if command_succeeded(kill_ok, why, code) then
                fail_early("B49131 another instance is already running, pid: " .. existing_pid)
            end
        end
        remove_path(lock_pid_path)
        fs.rmdir(lock_dir)
        mkdir_ok = fs.mkdir(lock_dir)
        if not mkdir_ok then
            fail_early("B36202 unable to acquire lock directory")
        end
    end
    local pid_written = write_text_file(lock_pid_path, tostring(nixio.getpid()) .. '\n', '0600')
    if not pid_written then
        fs.rmdir(lock_dir)
        fail_early("B79005 unable to acquire lock file")
    end
end

local function ensure_auth_keys()
    if fs.access(auth_privkey_path) and fs.access(auth_pubkey_path) then
        return true
    end
    remove_path(auth_pubkey_path)
    -- note: OpenSSL 1.1.1 found on test routers can't sign with Ed25519 keys
    local output = run_command(
        'openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out '
            .. shell_quote(auth_privkey_path),
        true
    )
    if not output then
        remove_path(auth_privkey_path)
        remove_path(auth_pubkey_path)
        return nil
    end
    fs.chmod(auth_privkey_path, '0600')
    output = run_command(
        'openssl pkey -in '
            .. shell_quote(auth_privkey_path)
            .. ' -pubout -out '
            .. shell_quote(auth_pubkey_path),
        true
    )
    if not output then
        remove_path(auth_privkey_path)
        remove_path(auth_pubkey_path)
        return nil
    end
    log_line("B04865 created " .. auth_privkey_path)
    return true
end

local function ensure_wg_keys()
    if fs.access(wg_privkey_path) and fs.access(wg_pubkey_path) then
        return true
    end
    remove_path(wg_pubkey_path)
    local output = run_command(
        'wg genkey |tee '
            .. shell_quote(wg_privkey_path)
            .. ' |wg pubkey >'
            .. shell_quote(wg_pubkey_path),
        true
    )
    if not output then
        remove_path(wg_privkey_path)
        remove_path(wg_pubkey_path)
        return nil
    end
    fs.chmod(wg_privkey_path, '0600')
    log_line("B35050 created " .. wg_privkey_path)
    return true
end

local function ensure_pubkeys_are_uploaded(api_url, subd, token)
    -- return true on success; retry forever on communication failure; return nil on permanent failure
    local auth_mtime = file_mtime(auth_privkey_path) or 0
    local wg_mtime = file_mtime(wg_privkey_path) or 0
    local uploaded_mtime = file_mtime(pubkeys_uploaded_path) or 0
    if uploaded_mtime > auth_mtime and uploaded_mtime > wg_mtime then
        return true  -- these public keys were previously uploaded
    end
    local retry_wait = 7
    local retries_left = 2
    local rpc_url = api_url .. 'devices/rpc'
    local auth_pubkey = read_text_file(auth_pubkey_path, false)
    local wg_pubkey = read_text_file(wg_pubkey_path, false)
    if not auth_pubkey or not wg_pubkey then
        return nil
    end
    while true do
        local request_path = make_temp_path()
        local response_path = make_temp_path()
        if not request_path or not response_path then
            remove_path(request_path)
            remove_path(response_path)
            return nil
        end
        local request_body = '{'
            .. '"jsonrpc":"2.0",'
            .. '"id":1,'
            .. '"method":"bootstrap1",'
            .. '"params":{'
            .. '"subd":"'
            .. json_escape(subd)
            .. '",'
            .. '"token":"'
            .. json_escape(token)
            .. '",'
            .. '"auth_pubkey":"'
            .. json_escape(auth_pubkey)
            .. '",'
            .. '"wg_pubkey":"'
            .. json_escape(wg_pubkey)
            .. '"'
            .. '}'
            .. '}'
        local write_ok = write_text_file(request_path, request_body, '0600')
        if not write_ok then
            remove_path(request_path)
            remove_path(response_path)
            return nil
        end
        local curl_command = 'curl --no-progress-meter '
            .. '-X POST '
            .. shell_quote(rpc_url)
            .. ' -H '
            .. shell_quote('Content-Type: application/json')
            .. ' --data-binary @'
            .. shell_quote(request_path)
            .. ' -o '
            .. shell_quote(response_path)
        local curl_output = run_command(curl_command, true)
        local response_body = read_text_file(response_path, true)
        remove_path(request_path)
        remove_path(response_path)
        if curl_output and response_body then
            local has_jsonrpc = response_body:match('"jsonrpc"%s*:%s*"2%.0"') ~= nil
            local has_result = response_body:match('"result"%s*:') ~= nil
            local has_error = response_body:match('"error"%s*:') ~= nil
            if has_jsonrpc and has_result and not has_error then
                local touch_output = run_command('touch ' .. shell_quote(pubkeys_uploaded_path), true)
                if not touch_output then
                    log_line("B04717 bootstrap succeeded but could not touch pubkeys_uploaded")
                end
                return true
            end
            log_line("B23806 bootstrap failed: " .. response_body)
        end
        sleep_with_jitter(retry_wait, 0.5)
        retries_left = retries_left - 1
        if retries_left <= 0 then
            retry_wait = retry_wait * 2
            if retry_wait > 3600 then
                retry_wait = 3600
            end
            retries_left = 2
        end
    end
end

local function build_ping_request(subd)
    -- returns the request body, or nil on failure
    local utc_time = run_command("date -u '+%Y-%m-%dT%H:%M:%SZ'", true)
    local uptime = run_command('uptime', true)
    local nonce = run_command('openssl rand -hex 16', true)
    if not utc_time or not uptime or not nonce then
        return nil
    end
    local request_body = '{'
        .. '"jsonrpc":"2.0",'
        .. '"id":1,'
        .. '"method":"ping",'
        .. '"params":{'
        .. '"subd":"'
        .. json_escape(subd)
        .. '",'
        .. '"time":"'
        .. json_escape(utc_time)
        .. '",'
        .. '"uptime":"'
        .. json_escape(uptime)
        .. '",'
        .. '"nonce":"'
        .. json_escape(nonce)
        .. '"'
        .. '}'
        .. '}'
    return request_body
end

local function do_ping(api_url, subd)
    -- returns the pingback response, or nil on failure
    local request_body = build_ping_request(subd)
    if not request_body then
        return nil
    end
    local result = nil
    local body_path = nil
    local sig_base_path = nil
    local sig_bin_path = nil
    local response_path = nil
    repeat  -- single-pass block to consolidate temp file removal
        body_path = make_temp_path()
        if not body_path then break end
        sig_base_path = make_temp_path()
        if not sig_base_path then break end
        sig_bin_path = make_temp_path()
        if not sig_bin_path then break end
        response_path = make_temp_path()
        if not response_path then break end
        local write_ok = write_text_file(body_path, request_body, '0600')
        if not write_ok then break end
        local content_digest_value = run_command(
            'openssl dgst -sha256 -binary '
                .. shell_quote(body_path)
                .. ' | openssl base64 -A',
            true
        )
        if not content_digest_value then break end
        local content_digest_header = 'sha-256=:' .. content_digest_value .. ':'
        local date_header = run_command("date -u '+%a, %d %b %Y %H:%M:%S GMT'", true)
        if not date_header then break end
        local created_value = run_command("date -u '+%s'", true)
        if not created_value then break end
        local rpc_url = api_url .. 'devices/rpc'
        local authority = rpc_url:match('^https?://([^/]+)')
        if not authority then break end
        local keyid_value = http_quoted_string_escape(subd)
        local signature_input_value = 'sig1=("@method" "@authority" "@target-uri" "content-type" "content-digest" "date");created='
            .. created_value
            .. ';keyid="'
            .. keyid_value
            .. '";alg="rsa-pss-sha256"'
        local signature_base = '"@method": POST\n'
            .. '"@authority": '
            .. authority
            .. '\n'
            .. '"@target-uri": '
            .. rpc_url
            .. '\n'
            .. '"content-type": application/json\n'
            .. '"content-digest": '
            .. content_digest_header
            .. '\n'
            .. '"date": '
            .. date_header
            .. '\n'
            .. '"@signature-params": ("@method" "@authority" "@target-uri" "content-type" "content-digest" "date");created='
            .. created_value
            .. ';keyid="'
            .. keyid_value
            .. '";alg="rsa-pss-sha256"'
        write_ok = write_text_file(sig_base_path, signature_base, '0600')
        if not write_ok then break end
        local sign_output = run_command(
            'openssl dgst -sha256 -sigopt rsa_padding_mode:pss -sigopt rsa_pss_saltlen:-1 '
                .. '-sign '
                .. shell_quote(auth_privkey_path)
                .. ' -binary -out '
                .. shell_quote(sig_bin_path)
                .. ' '
                .. shell_quote(sig_base_path),
            true
        )
        if not sign_output then break end
        -- verify: openssl dgst -sha256 -sigopt rsa_padding_mode:pss -sigopt rsa_pss_saltlen:-1 -verify /tmp/client_rsapss_pub.pem -signature /tmp/api_data.sig /tmp/api_data
        local signature_b64 = run_command(
            'openssl base64 -A -in ' .. shell_quote(sig_bin_path),
            true
        )
        if not signature_b64 then break end
        local curl_command = 'curl --no-progress-meter '
            .. '-X POST '
            .. shell_quote(rpc_url)
            .. ' -H '
            .. shell_quote('Content-Type: application/json')
            .. ' -H '
            .. shell_quote('Date: ' .. date_header)
            .. ' -H '
            .. shell_quote('Content-Digest: ' .. content_digest_header)
            .. ' -H '
            .. shell_quote('Signature-Input: ' .. signature_input_value)
            .. ' -H '
            .. shell_quote('Signature: sig1=:' .. signature_b64 .. ':')
            .. ' --data-binary @'
            .. shell_quote(body_path)
            .. ' -o '
            .. shell_quote(response_path)
        local curl_output = run_command(curl_command, true)
        if not curl_output then break end
        local response_body = read_text_file(response_path, true)
        if not response_body then break end
        local has_jsonrpc = response_body:match('"jsonrpc"%s*:%s*"2%.0"') ~= nil
        local has_error = response_body:match('"error"%s*:') ~= nil
        local pingback = response_body:match('"pingback"%s*:%s*"(([^"\\]|\\.)*)"')
        if has_jsonrpc and not has_error and pingback then
            result = json_unescape(pingback)
        end
    until true
    remove_path(body_path)
    remove_path(sig_base_path)
    remove_path(sig_bin_path)
    remove_path(response_path)
    return result
end

local function cleanup_and_exit(message)
    if message then
        log_line(message)
    end
    close_log()
    os.remove(lock_pid_path)
    fs.rmdir(lock_dir)
    os.exit(1)
end

ensure_root_and_single_instance()
math.randomseed(os.time() + nixio.getpid())
local api_url = read_text_file(api_url_path, false)
local subd = read_text_file(subd_path, false)
local token = read_text_file(token_path, true)
if not api_url or not subd then
    fail_early("B87328 required files are missing; exiting")
end
open_log()
if not ensure_auth_keys() or not ensure_wg_keys() then
    cleanup_and_exit("B60585 cannot continue without key files; exiting")
end
if not ensure_pubkeys_are_uploaded(api_url, subd, token) then
    cleanup_and_exit("B36017 cannot continue with uploading keys")
end
local retry_wait = 7
local retries_left = 2
while true do
    local ok = do_ping(api_url, subd)
    if ok then
        retry_wait = 7
        retries_left = 2
        sleep_with_jitter(60, 0.2)
    else
        sleep_with_jitter(retry_wait, 0.5)
        retries_left = retries_left - 1
        if retries_left <= 0 then
            retry_wait = retry_wait * 2
            if retry_wait > 3600 then
                retry_wait = 3600
            end
            retries_left = 2
        end
    end
end

