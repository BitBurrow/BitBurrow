#!/usr/bin/lua

local nixio = require('nixio')
local fs = require('nixio.fs')

local config_dir = '/etc/bitburrow'
local log_path = '/tmp/bitburrow.log'
local lock_dir = '/tmp/bitburrow_agent.lock'
local lock_pid_path = lock_dir .. '/pid'
local api_url_path = config_dir .. '/api_url'
local subd_path = config_dir .. '/subd'
local token_path = config_dir .. '/token'
local rsa_private_key_path = config_dir .. '/client_rsapss.pem'
local rsa_public_key_path = config_dir .. '/client_rsapss_pub.pem'
local wg_private_key_path = config_dir .. '/wgbb1_private.key'
local wg_public_key_path = config_dir .. '/wgbb1_public.key'
local pubkeys_uploaded_path = config_dir .. '/pubkeys_uploaded'

local log_handle = nil

local function shell_quote(value)
    return "'" .. tostring(value):gsub("'", "'\\''") .. "'"
end

local function command_ok(ok, why, code)
    if ok == true and why == 'exit' and code == 0 then
        return true
    end
    if ok == 0 then
        return true
    end
    return false
end

local function run_command(command, merge_stderr)
    local pipe = io.popen(command .. (merge_stderr and ' 2>&1' or ''), 'r')
    if not pipe then
        return false, "unable to start command: " .. command
    end
    local output = pipe:read('*a') or ''
    local ok, why, code = pipe:close()
    if command_ok(ok, why, code) then
        return true, output
    end
    local exit_code = code
    if type(ok) == 'number' and why == nil and code == nil then
        exit_code = ok
    end
    return false, output ~= '' and output or ("command failed: " .. command .. ", exit_code=" .. tostring(exit_code))
end

local function read_text_file(path, empty_if_unreadable)
    local handle = io.open(path, 'r')
    if not handle then
        if empty_if_unreadable then
            return true, ''
        end
        return false, "cannot read file: " .. path
    end
    local content = handle:read('*a') or ''
    handle:close()
    content = content:gsub('%s+$', '')
    return true, content
end

local function write_text_file(path, content, mode)
    local handle = io.open(path, 'w')
    if not handle then
        return false, "cannot write file: " .. path
    end
    handle:write(content)
    handle:close()
    if mode then
        fs.chmod(path, mode)
    end
    return true
end

local function log_line(line)
    if not log_handle then
        return
    end
    log_handle:write(os.date('!%Y-%m-%dT%H:%M:%SZ') .. ' ' .. line .. '\n')
    log_handle:flush()
end

local function fail_early(message)
    io.stderr:write(message .. '\n')
    os.exit(1)
end

local function make_temp_path()
    local ok, output = run_command('mktemp /tmp/bitburrow.XXXXXX', true)
    if not ok then
        return nil, output
    end
    local path = output:gsub('%s+$', '')
    if path == '' then
        return nil, 'mktemp returned an empty path'
    end
    return path
end

local function remove_path(path)
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
    value = value:gsub('\\u(%x%x%x%x)', function(hex)
        local num = tonumber(hex, 16)
        if num and num < 128 then
            return string.char(num)
        end
        return '?'
    end)
    value = value:gsub('\\"', '"')
    value = value:gsub('\\\\', '\\')
    value = value:gsub('\\/', '/')
    value = value:gsub('\\b', '\b')
    value = value:gsub('\\f', '\f')
    value = value:gsub('\\n', '\n')
    value = value:gsub('\\r', '\r')
    value = value:gsub('\\t', '\t')
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
    local sleep_seconds = math.random(min_seconds, max_seconds)
    nixio.nanosleep(sleep_seconds, 0)
end

local function ensure_root_and_single_instance()
    if nixio.geteuid() ~= 0 then
        fail_early('must run as root')
    end
    local ok = fs.mkdir(lock_dir)
    if not ok then
        local existing_pid_ok, existing_pid = read_text_file(lock_pid_path, false)
        if existing_pid_ok and existing_pid:match('^%d+$') then
            local pid_ok = os.execute('kill -0 ' .. existing_pid .. ' >/dev/null 2>&1')
            if command_ok(pid_ok, nil, nil) then
                fail_early('another instance is already running')
            end
        end
        fs.rmdir(lock_dir)
        ok = fs.mkdir(lock_dir)
        if not ok then
            fail_early('unable to acquire lock directory')
        end
    end
    local pid_written, pid_error = write_text_file(lock_pid_path, tostring(nixio.getpid()) .. '\n', 384)
    if not pid_written then
        fs.rmdir(lock_dir)
        fail_early(pid_error)
    end
end

local function create_log()
    local handle = io.open(log_path, 'w')
    if not handle then
        fail_early('cannot create log file: ' .. log_path)
    end
    handle:close()
    fs.chmod(log_path, 384)
    log_handle = io.open(log_path, 'a')
    if not log_handle then
        fail_early('cannot open log file for append: ' .. log_path)
    end
end

local function normalize_api_url(value)
    if value:sub(-1) ~= '/' then
        return value .. '/'
    end
    return value
end

local function ensure_rsa_keys()
    if fs.access(rsa_private_key_path) then
        return true
    end
    os.remove(rsa_public_key_path)
    -- note: OpenSSL 1.1.1 found on test routers can't sign with Ed25519 keys
    local ok, output = run_command(
        'cd '
            .. shell_quote(config_dir)
            .. ' && openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out '
            .. shell_quote(fs.basename(rsa_private_key_path)),
        true
    )
    if not ok then
        log_line("failed to generate RSA private key: " .. output)
        return false
    end
    fs.chmod(rsa_private_key_path, 384)
    ok, output = run_command(
        'cd '
            .. shell_quote(config_dir)
            .. ' && openssl pkey -in '
            .. shell_quote(fs.basename(rsa_private_key_path))
            .. ' -pubout -out '
            .. shell_quote(fs.basename(rsa_public_key_path)),
        true
    )
    if not ok then
        log_line("failed to derive RSA public key: " .. output)
        return false
    end
    return true
end

local function ensure_wg_keys()
    if fs.access(wg_private_key_path) then
        return true
    end
    os.remove(wg_public_key_path)
    local ok, output = run_command(
        'cd '
            .. shell_quote(config_dir)
            .. ' && sh -c '
            .. shell_quote(
                'wg genkey | tee '
                    .. shell_quote(fs.basename(wg_private_key_path))
                    .. ' | wg pubkey > '
                    .. shell_quote(fs.basename(wg_public_key_path))
            ),
        true
    )
    if not ok then
        log_line("failed to generate WireGuard keys: " .. output)
        return false
    end
    fs.chmod(wg_private_key_path, 384)
    return true
end

local function bootstrap_needed()
    local rsa_mtime = file_mtime(rsa_private_key_path) or 0
    local wg_mtime = file_mtime(wg_private_key_path) or 0
    local uploaded_mtime = file_mtime(pubkeys_uploaded_path) or 0
    return rsa_mtime > uploaded_mtime or wg_mtime > uploaded_mtime
end

local function do_bootstrap(api_url, subd, token)
    local retry_wait = 7
    local retries_left = 2
    local rpc_url = api_url .. 'devices/rpc'
    local auth_pubkey_ok, auth_pubkey = read_text_file(rsa_public_key_path, false)
    if not auth_pubkey_ok then
        log_line(auth_pubkey)
        return false
    end
    local wg_pubkey_ok, wg_pubkey = read_text_file(wg_public_key_path, false)
    if not wg_pubkey_ok then
        log_line(wg_pubkey)
        return false
    end
    while true do
        local request_path, request_error = make_temp_path()
        if not request_path then
            log_line(request_error)
            return false
        end
        local response_path, response_error = make_temp_path()
        if not response_path then
            remove_path(request_path)
            log_line(response_error)
            return false
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
        local write_ok, write_error = write_text_file(request_path, request_body, 384)
        if not write_ok then
            remove_path(request_path)
            remove_path(response_path)
            log_line(write_error)
            return false
        end
        local curl_command = 'curl --silent --show-error --fail '
            .. '-X POST '
            .. shell_quote(rpc_url)
            .. ' -H '
            .. shell_quote('Content-Type: application/json')
            .. ' --data-binary @'
            .. shell_quote(request_path)
            .. ' -o '
            .. shell_quote(response_path)
        local curl_ok, curl_output = run_command(curl_command, true)
        local response_ok, response_body = read_text_file(response_path, true)
        remove_path(request_path)
        remove_path(response_path)
        if curl_ok and response_ok then
            local has_jsonrpc = response_body:match('"jsonrpc"%s*:%s*"2%.0"') ~= nil
            local has_result = response_body:match('"result"%s*:') ~= nil
            local has_error = response_body:match('"error"%s*:') ~= nil
            if has_jsonrpc and has_result and not has_error then
                local touch_ok, touch_output = run_command('touch ' .. shell_quote(pubkeys_uploaded_path), true)
                if not touch_ok then
                    log_line("bootstrap succeeded but could not update pubkeys_uploaded timestamp: " .. touch_output)
                    return false
                end
                return true
            end
            log_line("bootstrap failed with non-success JSON-RPC response: " .. response_body)
        else
            local failure_text = curl_output
            if response_ok and response_body ~= '' then
                failure_text = failure_text .. ' | response=' .. response_body
            end
            log_line("bootstrap transport failure: " .. failure_text)
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
    local utc_ok, utc_time = run_command("date -u '+%Y-%m-%dT%H:%M:%SZ'", true)
    if not utc_ok then
        return nil, utc_time
    end
    utc_time = utc_time:gsub('%s+$', '')
    local uptime_ok, uptime_output = run_command('uptime', true)
    if not uptime_ok then
        return nil, uptime_output
    end
    uptime_output = uptime_output:gsub('%s+$', '')
    local nonce_ok, nonce_output = run_command('openssl rand -hex 16', true)
    if not nonce_ok then
        return nil, nonce_output
    end
    nonce_output = nonce_output:gsub('%s+$', '')
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
        .. json_escape(uptime_output)
        .. '",'
        .. '"nonce":"'
        .. json_escape(nonce_output)
        .. '"'
        .. '}'
        .. '}'
    return request_body
end

local function do_ping(api_url, subd)
    local request_body, request_error = build_ping_request(subd)
    if not request_body then
        return false, request_error
    end
    local body_path, body_error = make_temp_path()
    if not body_path then
        return false, body_error
    end
    local sig_base_path, sig_base_error = make_temp_path()
    if not sig_base_path then
        remove_path(body_path)
        return false, sig_base_error
    end
    local sig_bin_path, sig_bin_error = make_temp_path()
    if not sig_bin_path then
        remove_path(body_path)
        remove_path(sig_base_path)
        return false, sig_bin_error
    end
    local response_path, response_error = make_temp_path()
    if not response_path then
        remove_path(body_path)
        remove_path(sig_base_path)
        remove_path(sig_bin_path)
        return false, response_error
    end
    local write_ok, write_error = write_text_file(body_path, request_body, 384)
    if not write_ok then
        remove_path(body_path)
        remove_path(sig_base_path)
        remove_path(sig_bin_path)
        remove_path(response_path)
        return false, write_error
    end
    local content_digest_ok, content_digest_value = run_command(
        'openssl dgst -sha256 -binary '
            .. shell_quote(body_path)
            .. ' | openssl base64 -A',
        true
    )
    if not content_digest_ok then
        remove_path(body_path)
        remove_path(sig_base_path)
        remove_path(sig_bin_path)
        remove_path(response_path)
        return false, content_digest_value
    end
    content_digest_value = content_digest_value:gsub('%s+$', '')
    local content_digest_header = 'sha-256=:' .. content_digest_value .. ':'
    local date_ok, date_header = run_command("date -u '+%a, %d %b %Y %H:%M:%S GMT'", true)
    if not date_ok then
        remove_path(body_path)
        remove_path(sig_base_path)
        remove_path(sig_bin_path)
        remove_path(response_path)
        return false, date_header
    end
    date_header = date_header:gsub('%s+$', '')
    local created_ok, created_value = run_command("date -u '+%s'", true)
    if not created_ok then
        remove_path(body_path)
        remove_path(sig_base_path)
        remove_path(sig_bin_path)
        remove_path(response_path)
        return false, created_value
    end
    created_value = created_value:gsub('%s+$', '')
    local rpc_url = api_url .. 'devices/rpc'
    local authority = rpc_url:match('^https?://([^/]+)')
    if not authority then
        remove_path(body_path)
        remove_path(sig_base_path)
        remove_path(sig_bin_path)
        remove_path(response_path)
        return false, "unable to determine authority from api_url"
    end
    local signature_input_value = 'sig1=("@method" "@authority" "@target-uri" "content-type" "content-digest" "date");created='
        .. created_value
        .. ';keyid="'
        .. json_escape(subd)
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
        .. subd
        .. '";alg="rsa-pss-sha256"'
    write_ok, write_error = write_text_file(sig_base_path, signature_base, 384)
    if not write_ok then
        remove_path(body_path)
        remove_path(sig_base_path)
        remove_path(sig_bin_path)
        remove_path(response_path)
        return false, write_error
    end
    local sign_ok, sign_output = run_command(
        'openssl dgst -sha256 -sigopt rsa_padding_mode:pss -sigopt rsa_pss_saltlen:-1 '
            .. '-sign '
            .. shell_quote(rsa_private_key_path)
            .. ' -binary -out '
            .. shell_quote(sig_bin_path)
            .. ' '
            .. shell_quote(sig_base_path),
        true
    )
    if not sign_ok then
        remove_path(body_path)
        remove_path(sig_base_path)
        remove_path(sig_bin_path)
        remove_path(response_path)
        return false, sign_output
    end
    -- verify: openssl dgst -sha256 -sigopt rsa_padding_mode:pss -sigopt rsa_pss_saltlen:-1 -verify /tmp/client_rsapss_pub.pem -signature /tmp/api_data.sig /tmp/api_data
    local sig_b64_ok, signature_b64 = run_command(
        'openssl base64 -A -in ' .. shell_quote(sig_bin_path),
        true
    )
    if not sig_b64_ok then
        remove_path(body_path)
        remove_path(sig_base_path)
        remove_path(sig_bin_path)
        remove_path(response_path)
        return false, signature_b64
    end
    signature_b64 = signature_b64:gsub('%s+$', '')
    local curl_command = 'curl --silent --show-error --fail '
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
    local curl_ok, curl_output = run_command(curl_command, true)
    local response_ok, response_body = read_text_file(response_path, true)
    remove_path(body_path)
    remove_path(sig_base_path)
    remove_path(sig_bin_path)
    remove_path(response_path)
    if not curl_ok then
        return false, curl_output
    end
    if not response_ok then
        return false, response_body
    end
    local has_jsonrpc = response_body:match('"jsonrpc"%s*:%s*"2%.0"') ~= nil
    local has_error = response_body:match('"error"%s*:') ~= nil
    local pingback = response_body:match('"pingback"%s*:%s*"(([^"\\]|\\.)*)"')
    if has_jsonrpc and not has_error and pingback then
        return true, json_unescape(pingback)
    end
    return false, response_body
end

math.randomseed(os.time() + nixio.getpid())
ensure_root_and_single_instance()

local api_url_ok, api_url = read_text_file(api_url_path, false)
if not api_url_ok then
    fail_early(api_url)
end
api_url = normalize_api_url(api_url)

local subd_ok, subd = read_text_file(subd_path, false)
if not subd_ok then
    fail_early(subd)
end

local token_ok, token = read_text_file(token_path, true)
if not token_ok then
    fail_early(token)
end

create_log()

local function cleanup_and_exit(message)
    if message then
        log_line(message)
    end
    if log_handle then
        log_handle:close()
    end
    os.remove(lock_pid_path)
    fs.rmdir(lock_dir)
    os.exit(1)
end

if not ensure_rsa_keys() then
    cleanup_and_exit('cannot continue without RSA keys')
end

if not ensure_wg_keys() then
    cleanup_and_exit('cannot continue without WireGuard keys')
end

if bootstrap_needed() then
    local ok = do_bootstrap(api_url, subd, token)
    if not ok then
        cleanup_and_exit('bootstrap failed permanently')
    end
end

local retry_wait = 7
local retries_left = 2

while true do
    local ok, result = do_ping(api_url, subd)
    if ok then
        log_line(result)
        retry_wait = 7
        retries_left = 2
        sleep_with_jitter(60, 0.2)
    else
        log_line("ping failed: " .. result)
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