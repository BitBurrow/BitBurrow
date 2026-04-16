#!/usr/bin/lua

local nixio = require('nixio')
local fs = require('nixio.fs')

local api_url = '{api_url}'
local subd = '{subd}'
local token_path = '/tmp/{ott_filename}'

local function fail_early(message)
    io.stderr:write(message .. '\n')
    os.exit(1)
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

local function log(message, level)
    if level < logging_level then
        return
    end
    if logging_level < 30 then  -- send to stderr when -v used
        io.stderr:write(message .. '\n')
    end
    if log_handle then
        log_handle:write(os.date('!%Y-%m-%dT%H:%M:%SZ') .. ' ' .. message .. '\n')
        log_handle:flush()
    end
end

local function log_debug(message)
    log(message, 10)
end

local function log_info(message)
    log(message, 20)
end

local function log_warning(message)
    log(message, 30)
end

local function log_error(message)
    log(message, 40)
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

local function run_command(command, merge_stderr, failure_okay)
    -- return the captured output after stripping trailing whitespace, or nil on failure
    log_debug("running command: " .. command)
    local wrapped = '{ '
        .. command
        .. (merge_stderr and ' 2>&1' or ' 2>/dev/null')
        .. '; rc=$?; printf "\\n__EXIT__=%d\\n" "$rc"; }'
    local pipe = io.popen(wrapped, 'r')
    if not pipe then
        log_error("B12747 cannot run: " .. command)
        return nil
    end
    local output = pipe:read('*a') or ''
    pipe:close()  -- old method of `local ok, why, code = pipe:close()` did not capture exit code
    local exit_code = output:match('\n__EXIT__=(%d+)\n?$')
    if not exit_code then
        log_error("B60214 could not determine exit status for: " .. command)
        return nil
    end
    exit_code = tonumber(exit_code)
    output = output:gsub('\n__EXIT__=%d+\n?$', '')
    output = output:gsub('%s+$', '')
    if exit_code == 0 then
        if output ~= '' then
            log_debug("  command succeeded: " .. output:gsub('\n', '\\n'))
        else
            log_debug("  command succeeded with empty output")
        end
        return output
    end
    local msg
    if output ~= '' then
        msg = "B11840 running " .. command .. " failed: " .. output:gsub('\n', '\\n')
    else
        msg = "B11545 running " .. command .. " failed with exit code " .. tostring(exit_code)
    end
    if failure_okay then
        log_debug(msg)
    else
        log_error(msg)
    end
    return nil
end

local function read_text_file(path, empty_if_unreadable)
    -- return file contents, or nil on failure
    local handle = io.open(path, 'r')
    if not handle then
        if empty_if_unreadable then
            log_debug("file unreadable, treating as empty: " .. path)
            return ''
        end
        log_error("B41834 cannot read file: " .. path)
        return nil
    end
    local content, read_err = handle:read('*a')
    local close_ok, close_err = handle:close()
    if content == nil then
        log_error("B21409 cannot read " .. path .. " (" .. tostring(read_err) .. ")")
        return nil
    end
    if close_ok == nil then
        log_error("B55281 cannot close " .. path .. " (" .. tostring(close_err) .. ")")
        return nil
    end
    content = content:gsub('%s+$', '')
    log_debug("read " .. tostring(#content) .. " bytes from: " .. path)
    if #content > 0 and #content < 90 then
        log_debug("  data: " .. content:gsub('\n', '\\n'))
    end
    return content
end

local function write_text_file(path, content, mode)
    -- return true iff successful
    log_debug("writing " .. tostring(#content) .. " bytes to: " .. path)
    if #content < 90 then
        log_debug("  data: " .. content:gsub('%s+$', ''):gsub('\n', '\\n'))
    end
    local handle = io.open(path, 'w')
    if not handle then
        log_error("B38727 cannot write file: " .. path)
        return nil
    end
    local write_ok, write_err = handle:write(content)
    local close_ok, close_err = handle:close()
    if not write_ok then
        log_error("B93465 cannot write " .. path .. " (" .. tostring(write_err) .. ")")
        return nil
    end
    if close_ok == nil then
        log_error("B14993 cannot close " .. path .. " (" .. tostring(close_err) .. ")")
        return nil
    end
    if mode then
        fs.chmod(path, mode)
        log_debug("set permissions on " .. path .. " to " .. mode)
    end
    return true
end

local function make_temp_path(in_dir)
    -- create a temp file and return its path, or nil on failure; in_dir is optional
    if in_dir == nil then
        in_dir = '/tmp'
    else
        in_dir = trim_trailing_slashes(in_dir)
    end
    local path = run_command('mktemp ' .. shell_quote(in_dir .. '/bb' .. subd .. '.XXXXXX'), true)
    -- alternative `os.tmpname()` less flexible, possibly less reliable
    if not path or path == '' then
        log_error("B35286 mktemp failed")
        return nil
    end
    log_debug("created temporary path: " .. path)
    return path
end

local function remove_path(path)
    -- remove the file or empty directory, ignoring all errors
    if path and path ~= '' then
        log_debug("removing path: " .. path)
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
        log_debug("mtime unavailable for " .. path)
        return nil
    end
    log_debug("mtime for " .. path .. " is " .. tostring(stat.mtime))
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
    log_debug(
        "sleeping for " .. tostring(sleep_seconds) .. " seconds (base="
            .. tostring(base_seconds) .. ", jitter=" .. tostring(jitter_fraction) .. ")"
    )
    nixio.nanosleep(sleep_seconds, 0)
end

local function ensure_root_and_single_instance()
    log_debug("checking for root privileges")
    if nixio.getuid() ~= 0 then
        fail_early("must run as root")
    end
    log_debug("attempting to acquire lock directory " .. lock_dir)
    local mkdir_ok = fs.mkdir(lock_dir)
    if not mkdir_ok then
        log_info("lock directory already exists, checking for active owner")
        local existing_pid = read_text_file(lock_pid_path, false)
        if existing_pid and existing_pid:match('^%d+$') then
            if run_command('kill -0 ' .. existing_pid, true, true) then
                fail_early("B49131 another instance is already running, pid: " .. existing_pid)
            end
            log_info("stale lock detected for pid " .. existing_pid .. ", cleaning up")
        else
            log_info("lock directory exists but pid file is missing or invalid")
        end
        remove_path(lock_pid_path)
        fs.rmdir(lock_dir)
        mkdir_ok = fs.mkdir(lock_dir)
        if not mkdir_ok then
            fail_early("B36202 cannot acquire lock directory")
        end
    end
    local pid_written = write_text_file(lock_pid_path, tostring(nixio.getpid()) .. '\n', '0600')
    if not pid_written then
        fs.rmdir(lock_dir)
        fail_early("B79005 cannot acquire lock file")
    end
    log_info("acquired single-instance lock with pid " .. tostring(nixio.getpid()))
end

local function ensure_auth_keys()
    if fs.access(auth_privkey_path) and fs.access(auth_pubkey_path) then
        log_debug("auth_privkey and auth_pubkey both already exist")
        return true
    end
    log_info("authentication keys are missing, generating new keypair")
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
    log_debug("generated new auth_privkey: " .. auth_privkey_path)
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
    log_error("B04865 created " .. auth_privkey_path)
    log_info("generated new auth_pubkey:  " .. auth_pubkey_path)
    return true
end

local function ensure_wg_keys()
    if fs.access(wg_privkey_path) and fs.access(wg_pubkey_path) then
        log_debug("wg_privkey and wg_pubkey both already exist")
        return true
    end
    log_info("WireGuard keys are missing, generating new keypair")
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
    log_info("generated new wg_privkey: " .. wg_privkey_path)
    log_info("generated new wg_pubkey:  " .. wg_pubkey_path)
    return true
end

local function ensure_pubkeys_are_uploaded(token)
    -- return true on success; retry forever on communication failure; return nil on permanent failure
    local auth_mtime = file_mtime(auth_privkey_path) or 0
    local wg_mtime = file_mtime(wg_privkey_path) or 0
    local uploaded_mtime = file_mtime(pubkeys_uploaded_path) or 0
    if uploaded_mtime > auth_mtime and uploaded_mtime > wg_mtime then
        log_info("public keys already marked as uploaded")
        return true  -- these public keys were previously uploaded
    end
    local retry_wait = 7
    local retries_left = 2
    local auth_pubkey = read_text_file(auth_pubkey_path, false)
    local wg_pubkey = read_text_file(wg_pubkey_path, false)
    if not auth_pubkey or not wg_pubkey then
        log_debug("cannot upload public keys because one or more key files could not be read")
        return nil
    end
    log_info("public keys need upload to " .. api_url)
    while true do
        log_debug(
            "attempting adopt6c public key upload; retry_wait="
                .. tostring(retry_wait)
                .. ", retries_left="
                .. tostring(retries_left)
        )
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
            .. '"method":"adopt6c",'
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
        local curl_command = 'curl -sS '
            .. '-X POST '
            .. shell_quote(api_url)
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
            log_debug("adopt6c response body: " .. response_body:gsub('\n', '\\n'))
            local has_jsonrpc = response_body:match('"jsonrpc"%s*:%s*"2%.0"') ~= nil
            local has_result = response_body:match('"result"%s*:') ~= nil
            local has_error = response_body:match('"error"%s*:') ~= nil
            if has_jsonrpc and has_result and not has_error then
                log_info("public key upload succeeded")
                local touch_output = run_command('touch ' .. shell_quote(pubkeys_uploaded_path), true)
                if not touch_output then
                    log_error("B04717 adopt6c succeeded but could not touch pubkeys_uploaded")
                else
                    log_debug("updated upload marker: " .. pubkeys_uploaded_path)
                end
                return true
            end
            log_error("B23806 adopt6c failed: " .. response_body)
        else
            log_warning("adopt6c attempt failed without a usable response; will retry")
        end
        sleep_with_jitter(retry_wait, 0.5)
        retries_left = retries_left - 1
        if retries_left <= 0 then
            retry_wait = retry_wait * 2
            if retry_wait > 3600 then
                retry_wait = 3600
            end
            retries_left = 2
            log_info("increased adopt6c retry wait to " .. tostring(retry_wait) .. " seconds")
        end
    end
end

local function build_ping_request()
    -- return the request body, or nil on failure
    log_debug("building ping request for subd " .. subd)
    local utc_time = run_command("date -u '+%Y-%m-%dT%H:%M:%SZ'", true)
    local uptime = run_command('uptime', true)
    local nonce = run_command('openssl rand -hex 16', true)
    if not utc_time or not uptime or not nonce then
        log_warning('cannot build ping request because one or more inputs were unavailable')
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
    log_debug("built ping request body (" .. tostring(#request_body) .. " bytes)")
    return request_body
end

local function do_ping()
    -- return the pingback response, or nil on failure
    log_info("starting ping cycle")
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
        local authority = api_url:match('^https?://([^/]+)')
        if not authority then
            log_warning('could not parse authority from api url ' .. api_url)
            break
        end
        local keyid_value = http_quoted_string_escape(subd)
        local signature_input_value = 'sig1=("@method" "@authority" "@target-uri" "content-type" "content-digest" "date");created='
            .. created_value
            .. ';keyid="'
            .. keyid_value
            .. '";alg="rsa-pss-sha256"'
        local signature_base = '"@method": POST\n'
            .. '"@authority": ' .. authority .. '\n'
            .. '"@target-uri": ' .. api_url .. '\n'
            .. '"content-type": application/json\n'
            .. '"content-digest": ' .. content_digest_header .. '\n'
            .. '"date": ' .. date_header .. '\n'
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
        log_debug("sending signed ping request to " .. api_url)
        local curl_command = 'curl -sS '
            .. '-X POST '
            .. shell_quote(api_url)
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
        log_debug("ping response body: " .. response_body:gsub('\n', '\\n'))
        local has_jsonrpc = response_body:match('"jsonrpc"%s*:%s*"2%.0"') ~= nil
        local has_error = response_body:match('"error"%s*:') ~= nil
        local pingback = response_body:match('"pingback"%s*:%s*"(([^"\\]|\\.)*)"')
        if has_jsonrpc and not has_error and pingback then
            result = json_unescape(pingback)
            log_info("ping succeeded with pingback: " .. result)
        else
            log_warning("ping response was missing expected success fields")
        end
    until true
    remove_path(body_path)
    remove_path(sig_base_path)
    remove_path(sig_bin_path)
    remove_path(response_path)
    if not result then
        log_warning('ping cycle failed')
    end
    return result
end

local function cleanup_and_exit(message)
    if message then
        log_error(message)
    end
    log_info("cleaning up lock state and exiting")
    close_log()
    os.remove(lock_pid_path)
    fs.rmdir(lock_dir)
    os.exit(1)
end

local config_dir = '/etc/bb' .. subd .. '/'
local log_path = '/tmp/bb' .. subd .. '.log'
local lock_dir = '/tmp/bb' .. subd .. '.lock/'
local lock_pid_path = lock_dir .. 'pid'
local auth_privkey_path = config_dir .. 'client_rsapss.pem'
local auth_pubkey_path = config_dir .. 'client_rsapss_pub.pem'
local wg_privkey_path = config_dir .. 'wgbb1_private.key'
local wg_pubkey_path = config_dir .. 'wgbb1_public.key'
local pubkeys_uploaded_path = config_dir .. 'pubkeys_uploaded'
local log_handle = nil
local logging_level = 30  -- by default, show warnings, errors

for i = 1, #arg do
    local v = arg[i]:match("^%-(v+)$")
    if v then
        logging_level = logging_level - #v * 10
    elseif arg[i] == "--verbose" then
        logging_level = logging_level - 10
    end
end
ensure_root_and_single_instance()
math.randomseed(os.time() + nixio.getpid())
local token = read_text_file(token_path, true)
open_log()
log_info("startup complete; configuration files loaded")
log_debug("api_url=" .. api_url)
log_debug("subd=" .. subd)
log_debug("token length=" .. tostring(#token))
if not ensure_auth_keys() or not ensure_wg_keys() then
    cleanup_and_exit("B60585 cannot continue without key files; exiting")
end
if not ensure_pubkeys_are_uploaded(token) then
    cleanup_and_exit("B36017 cannot continue with uploading keys")
end
local retry_wait = 7
local retries_left = 2
log_info("entering main ping loop")
while true do
    local ok = do_ping()
    if ok then
        retry_wait = 7
        retries_left = 2
        log_debug("ping loop reset retry state after success")
        sleep_with_jitter(60, 0.2)
    else
        log_info(
            "ping failed; sleeping before retry with retry_wait="
                .. tostring(retry_wait)
                .. ", retries_left="
                .. tostring(retries_left)
        )
        sleep_with_jitter(retry_wait, 0.5)
        retries_left = retries_left - 1
        if retries_left <= 0 then
            retry_wait = retry_wait * 2
            if retry_wait > 3600 then
                retry_wait = 3600
            end
            retries_left = 2
            log_info("increased ping retry wait to " .. tostring(retry_wait) .. " seconds")
        end
    end
end

