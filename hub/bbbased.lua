#!/usr/bin/lua

--
-- hard-coded at time of downloaded in get_adopt5s_script()
--

local api_url = '{api_url}'
local subd = '{subd}'
local token_path = '/tmp/{ott_filename}'
local log_err_route = '{log_err_route}'

--
-- logging
--

local bbsubd = 'bb' .. subd
local log_path = nil  -- to enable, use: log_path = '/tmp/' .. bbsubd .. '.log'
local log_handle = nil
local logging_level = 30  -- by default, show warnings, errors
logging_level = 20  -- for dev, use level info
for i = 1, #arg do
    local v = arg[i]:match("^%-(v+)$")
    if v then
        logging_level = logging_level - #v * 10
    elseif arg[i] == "--verbose" then
        logging_level = logging_level - 10
    end
end

local function displayable(str, max_len)
    if not max_len then
        max_len = 20
    end
    local elipse = (#str > max_len) and '...' or ''
    return str:sub(1, max_len):gsub('\n', '\\n'):gsub('\t', '\\t') .. elipse
end

local function fail_early(message)
    io.stderr:write(message .. '\n')
    os.exit(1)
end

local function open_log()
    if not log_path then return end  -- use logread instead
    local handle = io.open(log_path, 'w')
    if not handle then
        fail_early("B62762 cannot create: " .. log_path)
    end
    handle:close()
    os.execute('chmod 0600 ' .. log_path)  -- chmod() is undefined; log_path must not have spaces
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

local function shell_quote(value)
    return "'" .. tostring(value):gsub("'", "'\\''") .. "'"
end

local function log(message, level)
    if level >= 30 then  -- send errors and warnings to server
        local tmpname = os.tmpname()
        local f = io.open(tmpname, 'w')
        if f then
            f:write(message)
            f:close()
            os.execute(
                'curl -f --max-time 60 -X POST'
                .. ' -H "Content-Type: text/plain"'
                .. ' --data-binary @' .. shell_quote(tmpname)
                .. ' ' .. shell_quote(log_err_route)
                .. ' >/dev/null 2>&1 || true'
            )
            os.remove(tmpname)
        end
    end
    if level >= logging_level then
        if not log_path or logging_level < 30 or not log_path then  -- when using logread or -v
            io.stderr:write(message .. '\n')  -- send to stderr
        end
        if log_handle then
            log_handle:write(os.date('!%Y-%m-%dT%H:%M:%SZ') .. ' ' .. message .. '\n')
            log_handle:flush()
        end
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

open_log()
log_warning("start BitBurrow base daemon (log level " .. logging_level .. ")")

--
-- paths
--

local function http_quoted_string_escape(value)
    value = tostring(value)
    value = value:gsub('\\', '\\\\')
    value = value:gsub('"', '\\"')
    value = value:gsub('[%z\1-\31\127]', '')
    return value
end

local function run_command(command, merge_stderr, failure_ok)
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
            log_debug("--command succeeded: " .. displayable(output, 60))
        else
            log_debug("--command succeeded with empty output")
        end
        return output
    end
    local msg
    local disp_command = displayable(command, 25)
    if output ~= '' then
        msg = "B11840 running " .. disp_command .. " failed: " .. displayable(output, 60)
    else
        msg = "B11545 running " .. disp_command .. " failed with exit code " .. tostring(exit_code)
    end
    if failure_ok then
        log_debug(msg)
    else
        log_error(msg)
    end
    return nil
end

local function trim_trailing_slashes(path)
    return (path:gsub('/+$', ''))
end     

local function make_temp_path(in_dir, failure_ok)
    -- create a temp file and return its path, or nil on failure; all args optional
    if in_dir == nil then
        in_dir = '/tmp'
    else
        in_dir = trim_trailing_slashes(in_dir)
    end
    local cmd = 'mktemp ' .. shell_quote(in_dir .. '/' .. bbsubd .. '.XXXXXX')
    local path = run_command(cmd, true, failure_ok)
    -- alternative `os.tmpname()` is less flexible, possibly less reliable
    if not path or path == '' then
        if not failure_ok then
            log_error("B35286 mktemp failed")
        end
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

local function chmod(path, mode)
    -- return true iff successful
    if mode then
        if run_command('chmod ' .. mode .. ' ' .. shell_quote(path), true) then
            return true
        end
    end
    return nil
end

local function mkdir(path, mode)
    -- return true iff successful or already existed; mode is optional
    if run_command('mkdir -p ' .. shell_quote(path), true, true) then
        if mode then
            return chmod(path, mode)
        end
        return true
    end
    return nil
end

local function dirname(path)
    -- return path after stripping the filename and final slash
    local dir = path:match('^(.*)/[^/]*$')
    if dir == nil or dir == '' then
        return '.'
    end
    return dir
end

local function is_readable(path)
    -- return true iff path is readable
    if run_command('test -r ' .. shell_quote(path), true, true) then
        return true
    end
    return nil
end

local function is_writable(path)
    -- return true iff path (file or dir) is writable
    if run_command('test -w ' .. shell_quote(path), true, true) then
        return true
    end
    return nil
    -- -- alternative method if path is a directory:
    -- local temp_path = make_temp_path(path, true)
    -- if not temp_path then
    --     return nil
    -- end
    -- remove_path(temp_path)
    -- return true
end

local function file_mtime(path)
    -- return mtime (epoch seconds) or 0 on failure
    local stdout = run_command('date +%s -r ' .. shell_quote(path), false, true)
    if stdout and stdout ~= '' then
        local t = tonumber(stdout)
        if t then
            return t
        end
    end
    return 0
end

--
-- file i/o
--

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
    log_debug("--data: " .. displayable(content, 20))  -- 20 to not show entire private key
    return content
end

local function write_text_file(path, content, mode)
    -- return true iff successful
    log_debug("writing " .. tostring(#content) .. " bytes to: " .. path)
    log_debug("--data: " .. displayable(content, 60))
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
        chmod(path, mode)
        log_debug("set permissions on " .. path .. " to " .. mode)
    end
    return true
end

local function file_copy(src_path, dst_path, mode)
    -- return true iff successful
    local src, err = io.open(src_path, 'rb')
    if not src then
        log_error("B78553 cannot open " .. src_path .. " (" .. tostring(err) .. ")")
        return nil
    end
    local tmp_path = make_temp_path(dirname(dst_path))
    if not tmp_path then
        src:close()
        return nil
    end
    local dst, err = io.open(tmp_path, 'wb')
    while true do
        local chunk = src:read(8192)
        if not chunk then break end
        local ok, werr = dst:write(chunk)
        if not ok then
            log_error("B79472 write to " .. tmp_path .. " failed (" .. tostring(werr) .. ")")
            src:close()
            dst:close()
            remove_path(tmp_path)
            return nil
        end
    end
    src:close()
    dst:close()
    if not run_command(string.format('chmod %s %q', mode, tmp_path)) then
        remove_path(tmp_path)
        return nil
    end
    local ok, rerr = os.rename(tmp_path, dst_path)
    if not ok then
        log_error("B77653 rename to " .. dst_path .. " failed (" .. tostring(rerr) .. ")")
        remove_path(tmp_path)
        return nil
    end
    return true
end

--
-- process management
--

local lock_dir = '/tmp/' .. bbsubd .. '.lock/'
local lock_pid_path = lock_dir .. 'pid'

local function get_pid()
    -- returns a string of the current PID; use tonumber(get_pid()) if you need an int
    local f = io.open('/proc/self/stat', 'r')
    if not f then return nil end
    local content = f:read('*l')
    f:close()
    return content and content:match('^(%d+)') or nil
end

local function get_uid()
    local f = io.open('/proc/self/status', 'r')
    if not f then return nil end
    for line in f:lines() do
        local uid = line:match('^Uid:%s+(%d+)')
        if uid then
            f:close()
            return tonumber(uid)
        end
    end
    f:close()
    return nil
end

local function ensure_root_and_single_instance()
    -- return true iff it's okay to continue, nil on error
    log_debug("checking for root privileges")
    if get_uid() ~= 0 then
        log_error("B97106 must run as root")
        return nil
    end
    log_debug("attempting to acquire lock directory " .. lock_dir)
    local mkdir_ok = mkdir(lock_dir)
    if not mkdir_ok then
        log_info("lock directory already exists, checking for active owner")
        local existing_pid = read_text_file(lock_pid_path, false)
        if existing_pid and existing_pid:match('^%d+$') then
            if run_command('kill -0 ' .. existing_pid, true, true) then
                log_error("B49131 another instance is already running, pid: " .. existing_pid)
                return nil
            end
            log_info("stale lock detected for pid " .. existing_pid .. ", cleaning up")
        else
            log_info("lock directory exists but pid file is missing or invalid")
        end
        remove_path(lock_pid_path)
        remove_path(lock_dir)
        mkdir_ok = mkdir(lock_dir)
        if not mkdir_ok then
            log_error("B36202 cannot acquire lock directory")
            return nil
        end
    end
    local pid_written = write_text_file(lock_pid_path, get_pid() .. '\n', '0600')
    if not pid_written then
        remove_path(lock_dir)
        log_error("B79005 cannot acquire lock file")
        return nil
    end
    log_info("acquired lock, pid " .. get_pid())
    return true
end

local function cleanup_and_exit(message)
    if message then
        log_error(message)
    end
    log_debug("cleaning up lock state and exiting")
    close_log()
    remove_path(lock_pid_path)
    remove_path(lock_dir)
    os.exit(1)
end

--
-- helper functions
--

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
    run_command('sleep ' .. tostring(sleep_seconds))
end

--
-- keys management
--

math.randomseed(os.time() + tonumber(get_pid()))
local config_dir = '/etc/' .. bbsubd .. '/'
local auth_privkey_path = config_dir .. 'client_rsapss.pem'
local auth_pubkey_path = config_dir .. 'client_rsapss_pub.pem'
local wg_privkey_path = config_dir .. 'wgbb1_private.key'
local wg_pubkey_path = config_dir .. 'wgbb1_public.key'
local pubkeys_uploaded_path = config_dir .. 'pubkeys_uploaded'

local function ensure_auth_keys()
    if is_readable(auth_privkey_path) and is_readable(auth_pubkey_path) then
        log_debug("auth_privkey and auth_pubkey both already exist")
        return true
    end
    log_info("authentication keys are missing; generating new keypair")
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
    chmod(auth_privkey_path, '0600')
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
    if is_readable(wg_privkey_path) and is_readable(wg_pubkey_path) then
        log_debug("wg_privkey and wg_pubkey both already exist")
        return true
    end
    log_info("WireGuard keys are missing; generating new keypair")
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
    chmod(wg_privkey_path, '0600')
    log_info("generated new wg_privkey: " .. wg_privkey_path)
    log_info("generated new wg_pubkey:  " .. wg_pubkey_path)
    return true
end

local function do_adopt6c()
    -- return true on success; retry forever on communication failure; return nil on permanent failure
    local auth_mtime = file_mtime(auth_privkey_path)
    local wg_mtime = file_mtime(wg_privkey_path)
    local uploaded_mtime = file_mtime(pubkeys_uploaded_path)
    if uploaded_mtime >= auth_mtime and uploaded_mtime >= wg_mtime then
        -- above, use '>=' and not '>' to avoid race condition and disabled client
        log_info("public keys already marked as uploaded")
        return true  -- these public keys were previously uploaded
    end
    local token = read_text_file(token_path, true):gsub("%s+", "")
    -- strip '\n' from middle if 2-line 'echo ... >>$T' is used in get_adopt5c_code()
    local token_mtime = file_mtime(token_path)
    if token_mtime == 0 then
        log_error("B16500 cannot read " .. token_path)
        return nil
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
        if (os.time() - token_mtime) >= 45*60 then
            -- if changing max time above, search: tag_ott_valid_for
            log_error("B31143 token " .. token_path .. " is expired")  -- enforced on server too
            return nil
        end
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
            log_debug("adopt6c response body: " .. displayable(response_body, 60))
            local has_jsonrpc = response_body:match('"jsonrpc"%s*:%s*"2%.0"') ~= nil
            local has_result = response_body:match('"result"%s*:') ~= nil
            local has_error = response_body:match('"error"%s*:') ~= nil
            if has_jsonrpc and has_result and not has_error then
                log_info("public key upload succeeded")
                local touch_output = run_command('touch ' .. shell_quote(pubkeys_uploaded_path))
                if not touch_output then
                    log_error("B04717 adopt6c succeeded but could not touch pubkeys_uploaded")
                else
                    log_debug("updated upload marker: " .. pubkeys_uploaded_path)
                end
                return true
            end
            log_error("B23806 adopt6c failed: " .. displayable(response_body, 60))
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
    local request_id = run_command('openssl rand -hex 16', true)
    if not utc_time or not uptime or not request_id then
        log_debug('cannot build ping request because one or more inputs were unavailable')
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
        .. '"request_id":"'
        .. json_escape(request_id)
        .. '"'
        .. '}'
        .. '}'
    log_debug("built ping request body (" .. tostring(#request_body) .. " bytes)")
    return request_body
end

local function choose_signature_algorithm()
    -- prefer RFC 9421 rsa-pss-sha512; fall back to non-standard rsa-pss-sha256
    local probe_path = make_temp_path()
    local sig_path = make_temp_path()
    if not probe_path or not sig_path then
        remove_path(probe_path)
        remove_path(sig_path)
        return {
            name = 'rsa-pss-sha256',
            digest = 'sha256',
            mgf1 = 'sha256',
            saltlen = '32',
        }
    end
    if not write_text_file(probe_path, 'probe', '0600') then
        remove_path(probe_path)
        remove_path(sig_path)
        return {
            name = 'rsa-pss-sha256',
            digest = 'sha256',
            mgf1 = 'sha256',
            saltlen = '32',
        }
    end
    local ok = run_command(
        'openssl dgst -sha512 '
            .. '-sigopt rsa_padding_mode:pss '
            .. '-sigopt rsa_mgf1_md:sha512 '
            .. '-sigopt rsa_pss_saltlen:64 '
            .. '-sign '
            .. shell_quote(auth_privkey_path)
            .. ' -binary -out '
            .. shell_quote(sig_path)
            .. ' '
            .. shell_quote(probe_path),
        true,
        true
    )
    remove_path(probe_path)
    remove_path(sig_path)
    if ok then
        return {
            name = 'rsa-pss-sha512',
            digest = 'sha512',
            mgf1 = 'sha512',
            saltlen = '64',
        }
    end
    log_warning('OpenSSL lacks rsa-pss-sha512 support; falling back to non-standard rsa-pss-sha256')
    return {
        name = 'rsa-pss-sha256',
        digest = 'sha256',
        mgf1 = 'sha256',
        saltlen = '32',
    }
end

local function do_ping()
    -- return the status response, or nil on failure
    log_debug("starting ping cycle")
    local request_body = build_ping_request()
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
        local timestamp_pair = run_command("date -u '+%s|%a, %d %b %Y %H:%M:%S GMT'")
        if not timestamp_pair then break end
        local created_value, date_header = timestamp_pair:match('^(%d+)|(.+)$')
        if not created_value or not date_header then
            log_warning('could not parse date output for signature headers')
            break
        end
        local authority = api_url:match('^https?://([^/]+)')
        if not authority then
            log_warning('could not parse authority from api url ' .. api_url)
            break
        end
        local nonce_value = run_command('openssl rand -hex 16', true)
        if not nonce_value then break end
        local keyid_value = http_quoted_string_escape(subd)
        local nonce_param_value = http_quoted_string_escape(nonce_value)
        local sigalg = choose_signature_algorithm()
        local signature_params = '("@method" "@authority" "@target-uri" "content-type" "content-digest" "date");created='
            .. created_value
            .. ';keyid="'
            .. keyid_value
            .. '";nonce="'
            .. nonce_param_value
            .. '";alg="'
            .. sigalg.name
            .. '"'
        local signature_input_value = 'sig1=' .. signature_params
        local signature_base = '"@method": POST\n'
            .. '"@authority": ' .. authority .. '\n'
            .. '"@target-uri": ' .. api_url .. '\n'
            .. '"content-type": application/json\n'
            .. '"content-digest": ' .. content_digest_header .. '\n'
            .. '"date": ' .. date_header .. '\n'
            .. '"@signature-params": '
            .. signature_params
        write_ok = write_text_file(sig_base_path, signature_base, '0600')
        if not write_ok then break end
        local sign_output = run_command(
            'openssl dgst -'
                .. sigalg.digest
                .. ' -sigopt rsa_padding_mode:pss '
                .. '-sigopt rsa_mgf1_md:' .. sigalg.mgf1
                .. ' -sigopt rsa_pss_saltlen:' .. sigalg.saltlen
                .. ' -sign ' .. shell_quote(auth_privkey_path)
                .. ' -binary -out ' .. shell_quote(sig_bin_path)
                .. ' ' .. shell_quote(sig_base_path),
            true
        )
        if not sign_output then break end
        -- verify:
        -- openssl dgst -sha512 -sigopt rsa_padding_mode:pss -sigopt rsa_mgf1_md:sha512 \
        --     -sigopt rsa_pss_saltlen:64 -verify /tmp/client_rsapss_pub.pem \
        --     -signature /tmp/api_data.sig /tmp/api_data
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
        log_debug("ping response body: " .. displayable(response_body, 300))
        local has_jsonrpc = response_body:match('"jsonrpc"%s*:%s*"2%.0"') ~= nil
        local has_error = response_body:match('"error"%s*:') ~= nil
        local status = response_body:match('"result"%s*:%s*{.-"status"%s*:%s*"([^"]*)"')
        if has_jsonrpc and not has_error and status then
            result = json_unescape(status)
            log_info("ping succeeded with status: " .. displayable(result, 60))
        else
            log_error("signature_base='" .. displayable(signature_base, 900) .. "'")
        end
    until true
    remove_path(body_path)
    remove_path(sig_base_path)
    remove_path(sig_bin_path)
    remove_path(response_path)
    return result
end

--
-- installation
--

local function find_install_dir()
    local home = os.getenv('HOME') or ''
    local try1_paths = {
        '/usr/local/sbin/',
        '/usr/sbin/',
        '/sbin/',
        '/usr/local/bin/',
        '/usr/bin/',
        '/bin/',
        home .. '/.local/bin/',
        home .. '/bin/',
    }
    local try2_paths = {
        home .. '/.local/bin/',
        home .. '/bin/',
    }
    for _, path in ipairs(try1_paths) do
        if is_writable(path) then
            return path
        end
    end
    for _, path in ipairs(try2_paths) do
        run_command('mkdir -p ' .. path, true, true)
        if is_writable(path) then
            return path
        end
    end
    log_error("B95830 cannot find_install_dir(); home is " .. home)
    return ''
end

local function install_init_service(lua_path, init_path)
    -- return true iff already installed and running as a service
    -- return nil on failure
    -- return false after successful install or reinstall (running under /tmp)
    local running_path = arg and arg[0] or ''
    if running_path == '' then
        log_error("B72200 cannot find running_path")
        return nil
    end
    if running_path == '' or running_path:sub(1, 5) ~= '/tmp/' then
        log_debug("already installed (running as " .. running_path .. ")")
        return true
    end
    local temp_path = make_temp_path()
    local init_text = table.concat({
        '#!/bin/sh /etc/rc.common',
        '',
        'START=95',
        'STOP=10',
        'USE_PROCD=1',
        '',
        'start_service() {',
        '    procd_open_instance',
        '    procd_set_param command /usr/bin/lua ' .. lua_path,
        '    procd_set_param respawn',
        '    procd_set_param stdout 1', -- 1 means make output viewable via `logread`
        '    procd_set_param stderr 1',
        '    procd_close_instance',
        '}',
        '',
    }, '\n')
    if not write_text_file(temp_path, init_text) then
        remove_path(temp_path)
        return nil
    end
    if not file_copy(temp_path, init_path, '0755') then
        remove_path(temp_path)
        return nil
    end
    remove_path(temp_path)
    if not file_copy(running_path, lua_path, '0644') then return nil end
    if not run_command(shell_quote(init_path) .. ' enabled', true, true) then
        if not run_command(shell_quote(init_path) .. ' enable') then
            return nil
        end
    end
    if not run_command(shell_quote(init_path) .. ' running', true, true) then
        if not run_command(shell_quote(init_path) .. ' start') then return nil end
        log_debug("successfully installed; exiting")
    else
        if not run_command(shell_quote(init_path) .. ' restart') then return nil end
        log_debug("successfully reinstalled; exiting")
    end
    -- new service should run now; don't use this here: dofile(lua_path)
    remove_path(running_path)
    remove_path(dirname(running_path))  -- temp directory should be empty; does nothing if not
    return false
end

local packager_cmds = nil
local spec = {
    {'apt-get', {update = {'apt-get', 'update'}, install = {'apt-get', 'install', '-y'}}},
    {'apk', {update = {'apk', 'update'}, install = {'apk', 'add'}}},
    {'opkg', {update = {'opkg', 'update'}, install = {'opkg', 'install'}}},
    {'dnf', {update = {'dnf', 'makecache'}, install = {'dnf', 'install', '-y'}}},
    {'yum', {update = {'yum', 'makecache'}, install = {'yum', 'install', '-y'}}},
    {'pacman', {update = {'pacman', '-Sy', '--noconfirm'}, install = {'pacman', '-S', '--noconfirm'}}},
    {'zypper', {update = {'zypper', '--non-interactive', 'refresh'}, install = {'zypper', '--non-interactive', 'install'}}},
}
local unpack_fn = table.unpack or unpack

local function packager(action, arg)
    if not packager_cmds then  -- find first valid package manager and cache it for future calls
        for i = 1, #spec do
            local candidate = spec[i]
            if run_command('command -v ' .. candidate[1], true, true) then
                packager_cmds = candidate[2]
                break
            end
        end
    end
    if not packager_cmds then
        log_error("B91049 cannot find package manager")
        return nil
    end
    local cmd = packager_cmds[action]
    if not cmd then return nil end
    local parts = {unpack_fn(cmd)}
    if action == 'install' and arg then
        parts[#parts + 1] = arg
    end
    return run_command(table.concat(parts, ' '), true, true)
end

local function install_one_of(package_list, command)
    local retry = 0
    while retry < 4 do
        if run_command('command -v ' .. command, true, true) then return true end
        if retry >= 2 then  -- wait before retries 2, 3
            run_command("sleep 75")
        end
        if retry >= 1 then  -- update before retries 1, 2, 3
            packager('update')
        end
        for pkg in package_list:gmatch("%S+") do
            packager('install', pkg)
            if run_command('command -v ' .. command, true, true) then
                log_info("installed package " .. pkg .. " for " .. command)
                return true
            end
        end
        retry = retry + 1
    end
    log_error("B80574 cannot install package for " .. command)
    return nil
end

--
-- if running from /tmp, install or reinstall as a service and exit
--

local install_path = find_install_dir() .. bbsubd .. '.lua'
local install_attempt = install_init_service(install_path, '/etc/init.d/' .. bbsubd)
if install_attempt == nil then
    cleanup_and_exit("B21488 cannot install as a service")
end
if install_attempt == false then
    cleanup_and_exit("B32020 successfully installed; exiting")
end

--
-- if already running in another process, exit
--

if not ensure_root_and_single_instance() then
    cleanup_and_exit("B41990 invalid instance; exiting")
end

--
-- install prerequisites
--

install_one_of('curl', 'curl')
install_one_of('openssl openssl-util', 'openssl')
install_one_of('wireguard-tools wg-installer-server', 'wg')

--
-- collect authentication details
--

mkdir(config_dir, '0700')
if not ensure_auth_keys() or not ensure_wg_keys() then
    cleanup_and_exit("B60585 cannot continue without key files; exiting")
end

--
-- register with hub
--

if not do_adopt6c() then
    cleanup_and_exit("B36017 cannot continue with uploading keys")
end

--
-- loop forever
--

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

