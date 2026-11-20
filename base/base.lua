#!/usr/bin/lua

-- BitBurrow base daemon
-- note: strings use single quotes unless they are user-visible English, e.g. logging
-- note: generate a new key pair via touch ~/.config/openssl/bitburrow/rotate_keys_after_signing

--
-- public keys to verify subsequent software updates
--

local file_pubkeys = {  -- key_id is the file_cver which first included this pubkey
    {
        key_id = '0tlfc1c',
        pubkey = table.concat({
            '-----BEGIN PUBLIC KEY-----',
            'MIIB1jBBBgkqhkiG9w0BAQowNKAPMA0GCWCGSAFlAwQCAwUAoRwwGgYJKoZIhvcN',
            'AQEIMA0GCWCGSAFlAwQCAwUAogMCAUADggGPADCCAYoCggGBAN4UDVHNHhgGgjFU',
            'eX+6K1NFyIwrvZmjgey5flpsWJoQAjdrlRbCeNtqsBETTElcquYycA987KyKK6wD',
            'P7hNkUNELVGAJeDCQWsCT9h7NhUBlSmJN0oB1equGCdqqUkSguplq6ynByfxzr2b',
            'q7R5sNcyJVwiimRCepFrApbelr31NHelx5iwmXsZXixRDqJrtBAnAC87n0EIopqR',
            '8Pp89vgKIOE1uI3XA9JkCQ3xAcvOKA2Z4Lt5WTb50T41FukM2jxkbMLXE36FBDE6',
            'G2zYfQLlNOnCEiUqlCgupBnTZOk77ml2lxPap2m5yGvBDfc56qLeC60ON0yjOyQI',
            'Bzqf7yvyHlglC14vEaU/wcXBB8s4WyJiaA3LSIZaD8hniV91gY7Mpy4l37Q1X60Y',
            'BIJF2QnC8KyBzCupQSOU9nDicjJ1CumiIao0FZeTF7uIkg7G7mmqegexlGXM9xu/',
            'pbvXWXw7xBx3j7EL7d9KS9VH5bDuW9tg8BwE2TUU5+Z2O7RQuQIDAQAB',
            '-----END PUBLIC KEY-----',
        }, '\n'),
    },
}

--
-- globals
--

local hubconf = os.getenv('HUBCONF') or ''
local discover_upnp  -- external 'upnp.lua' module

local function fail_early(message)
    io.stderr:write(message .. '\n')
    os.exit(1)
end

local function hub_config(key, default)
    for k, v in hubconf:gmatch('([^\r\n=]+)=([^\r\n]*)') do
        if k == key then return v end
    end
    if default ~= nil then return default end
    fail_early("B09542 HUBCONF environment variable is missing a value for " .. key)
end

local api_url = hub_config('api_url')
local log_err_route = hub_config('log_err_route')
local ott_filename = hub_config('ott_filename')
local subd = hub_config('subd')
local lua_exe = hub_config('lua_exe', '/usr/bin/lua')
local bbsubd = 'bb' .. subd
local config_dir = '/etc/' .. bbsubd .. '/'
local base_config_path = config_dir .. 'base.conf'
local tmp_dir = os.getenv('TMPDIR')
if tmp_dir and tmp_dir ~= '' then
    tmp_dir = tmp_dir:gsub('/+$', '') .. '/'  -- note all directories end in '/'
else
    tmp_dir = '/tmp/'
end
local ott_path = tmp_dir .. ott_filename
local bbsubd_tmp_dir = tmp_dir .. bbsubd .. '/'
local lock_dir = bbsubd_tmp_dir .. 'lock/'
local lock_dir_pid_path = lock_dir .. 'pid'
local lock_file_path = lock_dir .. 'flock'
local lock_dir_stop_request_path = lock_dir .. 'stop_request'
local sleep_method = nil
local launch_method = nil
local our_pgid = nil  -- process group ID
local base_config = nil

--
-- logging
--

local log_path = nil  -- to enable, use: log_path = bbsubd_tmp_dir .. 'log'
local log_handle = nil
local logging_level = 30  -- by default, show warnings, errors

local function shell_quote(value)
    return "'" .. tostring(value):gsub("'", "'\"'\"'") .. "'"
end

local function displayable(str, max_len)
    if not max_len then
        max_len = 20
    end
    local ellipsis = (#str > max_len) and '...' or ''
    return str:sub(1, max_len):gsub('\n', '\\n'):gsub('\r', '\\r'):gsub('\t', '\\t') .. ellipsis
end

local function rstrip(text)
    return text:sub(1, text:match('^.*()%S') or 0)  -- strip trailing whitespace
end

local function trim(text)
    return (rstrip(text):gsub('^%s+', ''))
end

local function open_log()
    if not log_path or log_handle then return end  -- no log_path or handle is already open
    local handle, err = io.open(log_path, 'a')
    if not handle then
        io.stderr:write("B71356 cannot open log; using stderr: ", tostring(err), '\n')
        return
    end
    -- 'exec 9>&-;' closes inherited lock fd 9 so surviving children cannot block daemon restarts;
    -- see https://man7.org/linux/man-pages/man2/flock.2.html
    local ok = os.execute('exec 9>&-; chmod 0600 ' .. shell_quote(log_path))  -- chmod() not defined
    if ok ~= 0 and ok ~= true then  -- cover both Lua 5.1 and 5.2+ return values
        handle:close()
        io.stderr:write("B62762 cannot set log permissions; using stderr: ", log_path, '\n')
        return
    end
    log_handle = handle
end

local function close_log()
    if log_handle then
        log_handle:close()
    end
    log_handle = nil
end

local function log(message, level)
    if level >= 30 then  -- send errors and warnings to server
        -- run_command() and make_temp_path() are not defined yet
        local tmp_template = shell_quote(tmp_dir .. bbsubd .. '.log.XXXXXX')
        local tmp_pipe = io.popen('exec 9>&-; umask 077; mktemp ' .. tmp_template .. ' 2>/dev/null', 'r')
        -- if mktemp fails, continue local logging
        local tmp_path = tmp_pipe and tmp_pipe:read('*l') or nil
        if tmp_pipe then tmp_pipe:close() end
        if tmp_path == '' then tmp_path = nil end
        local f = tmp_path and io.open(tmp_path, 'w') or nil
        if f then
            local write_ok = f:write(message)
            local close_ok = f:close()
            if write_ok and close_ok ~= nil then
                os.execute(
                    'exec 9>&-; curl -f --max-time 10 -X POST'
                    .. ' -H "Content-Type: text/plain"'
                    .. ' --data-binary @' .. shell_quote(tmp_path)
                    .. ' ' .. shell_quote(log_err_route)
                    .. ' >/dev/null 2>&1 || true'
                )
            end
        end
        if tmp_path then os.remove(tmp_path) end
    end
    if level >= logging_level then
        if not log_handle or logging_level < 30 then  -- when using logread, stderr fallback, or -v
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

local function run_command_result(command, output, exit_code, failure_ok)
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
    return nil, output, exit_code
end

local function run_command(command, merge_stderr, failure_ok)
    -- return captured output on success; return nil, output, and exit code on command failure
    -- do not use shell pipelines here (`set -o pipefail` not universally supported)
    -- instead, use a command with file redirection and `&&` within { ... }
    log_debug("running command: " .. command)
    local wrapped = 'exec 9>&-; { '
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
    output = rstrip(output)
    return run_command_result(command, output, exit_code, failure_ok)
end

local function trim_trailing_slashes(path)
    return (path:gsub('/+$', ''))
end

local function make_temp_path(in_dir, failure_ok)
    -- create a temp file and return its path, or nil on failure; all args optional
    if in_dir == nil then
        in_dir = bbsubd_tmp_dir
    else
        in_dir = trim_trailing_slashes(in_dir) .. '/'
    end
    local cmd = 'mktemp ' .. shell_quote(in_dir .. bbsubd .. '.XXXXXX')
    local path = run_command(cmd, true, failure_ok)
    -- alternative `os.tmpname()` is less flexible, possibly less reliable
    if not path or path == '' then
        if in_dir == bbsubd_tmp_dir  -- dir here should only be created from the service wrapper
                and run_command('test ! -d ' .. shell_quote(in_dir), true, true) then
            fail_early("B91253 daemon runtime directory missing; restart required: " .. in_dir)
        end
        if not failure_ok then
            log_error("B35286 mktemp failed")
        end
        return nil
    end
    log_debug("created temporary path: " .. path)
    return path
end

local function remove_path(path, log_failures)
    -- remove the file or empty directory; return true iff success; fails on non-empty dir
    if not path or path == '' then return nil end
    local ok, err = os.remove(path)
    if ok then
        log_debug("successfully removed " .. path)
        return true
    end
    if log_failures then
        log_error("B26972 could not remove " .. path .. " (" .. tostring(err) .. ")")
    else
        log_debug("could not remove " .. path .. " (" .. tostring(err) .. ")")
    end
    return nil
end

local function remove_paths(...)
    for index = 1, select('#', ...) do
        local path = select(index, ...)
        remove_path(path)
    end
end

local function chmod(path, mode)
    -- return true iff successful
    if mode and run_command('chmod ' .. mode .. ' ' .. shell_quote(path), true) then return true end
    return nil
end

local function get_mode(path)
    -- return file mode like '0700', or nil on failure
    local line = run_command('ls -ld ' .. shell_quote(path), false, true)
    if not line then return nil end
    local perms = line:match('^(%S+)')
    if not perms or #perms < 10 then return nil end
    local mode = 0
    local spec = {
        {2, 'r', 256}, {3, 'w', 128}, {4, 'x', 64},
        {5, 'r', 32}, {6, 'w', 16}, {7, 'x', 8},
        {8, 'r', 4}, {9, 'w', 2}, {10, 'x', 1},
    }
    for i = 1, #spec do
        local c = perms:sub(spec[i][1], spec[i][1])
        if c == spec[i][2] then
            mode = mode + spec[i][3]
        end
    end
    local executable_special = {
        {4, 's', 64}, {7, 's', 8}, {10, 't', 1},
    }
    for i = 1, #executable_special do
        local c = perms:sub(executable_special[i][1], executable_special[i][1])
        if c == executable_special[i][2] then
            mode = mode + executable_special[i][3]
        end
    end
    local special = {
        {4, 's', 2048}, {4, 'S', 2048},
        {7, 's', 1024}, {7, 'S', 1024},
        {10, 't', 512}, {10, 'T', 512},
    }
    for i = 1, #special do
        local c = perms:sub(special[i][1], special[i][1])
        if c == special[i][2] then
            mode = mode + special[i][3]
        end
    end
    return string.format('%04o', mode)
end

local function mkdir(path, mode, err_if_exists)
    -- return true iff successful; mode and err_if_exists are optional
    local command = err_if_exists and 'mkdir ' or 'mkdir -p '  -- by default, okay if dir already exists
    if not run_command(command .. shell_quote(path), true, true) then return nil end
    return not mode or chmod(path, mode)
end

local function dirname(path)
    -- return path after stripping the filename and final slash
    local dir = path:match('^(.*)/[^/]*$')
    if dir == nil or dir == '' then
        return '.'
    end
    return dir
end

local function basename(path)
    return path:match("^.*/([^/]*)$") or path
end

local function is_directory(path)
    return run_command("test -d " .. shell_quote(path), true, true) and true or nil
end

local function is_readable(path)
    -- return true iff path is readable, nil otherwise
    return run_command('test -r ' .. shell_quote(path), true, true) and true or nil
end

local function is_writable(path)
    -- return true iff path (file or dir) is writable
    return run_command('test -w ' .. shell_quote(path), true, true) and true or nil
    -- -- alternative method if path is a directory:
    -- local temp_path = make_temp_path(path, true)
    -- if not temp_path then return nil end
    -- remove_path(temp_path)
    -- return true
end

local function file_mtime(path)
    -- return mtime (epoch seconds) or 0 on failure
    local stdout = run_command('date +%s -r ' .. shell_quote(path), false, true)
    return tonumber(stdout) or 0
end

--
-- file i/o
--

local oversized_files = {}

local function read_text_file(path, empty_if_unreadable, preserve_whitespace, max_bytes)
    -- return file contents, or nil on failure
    local handle = io.open(path, 'r')
    local ret_error = empty_if_unreadable and '' or nil
    if not handle then
        if empty_if_unreadable then
            log_debug("file unreadable, treating as empty: " .. path)
        else
            log_error("B41834 cannot read file: " .. path)
        end
        return ret_error
    end
    local content, read_err = handle:read(max_bytes and max_bytes + 1 or '*a')
    if max_bytes and not content and not read_err then content = '' end
    local close_ok, close_err = handle:close()
    if content == nil then
        log_error("B21409 cannot read " .. path .. " (" .. tostring(read_err) .. ")")
        return ret_error
    end
    if close_ok == nil then
        log_error("B55281 cannot close " .. path .. " (" .. tostring(close_err) .. ")")
        return ret_error
    end
    if max_bytes then
        if #content > max_bytes then
            if not oversized_files[path] then
                log_warning("B28694 omitting oversized file: " .. path)
            end
            oversized_files[path] = true
            return ret_error
        end
        oversized_files[path] = nil
    end
    if not preserve_whitespace then
        content = rstrip(content)
    end
    log_debug("read " .. tostring(#content) .. " bytes from: " .. path)
    log_debug("--data: " .. displayable(content, 20))  -- 20 to not show entire private key
    return content
end

local function display_text_file(path)
    return displayable(read_text_file(path, true, false))
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
        if not chmod(path, mode) then
            remove_path(path)
            return nil
        end
        log_debug("set permissions on " .. path .. " to " .. mode)
    end
    return true
end

local function download_file(command, path, max_download_bytes, quiet)
    -- limit download size to prevent DOS from a compromised hub
    local status_path = make_temp_path(nil, quiet)
    max_download_bytes = max_download_bytes or 654321  -- default max 0.6 MB; search tag_base_max_dl_bytes
    local log_report = quiet and log_debug or log_error
    if not status_path then return nil end
    -- read a bounded pipe; older curl versions ignore --max-filesize when Content-Length
    -- is unknown; limits HTTP headers too
    local wrapped = 'exec 9>&-; (ulimit -c 0; ulimit -f 128 || exit 1; ' .. command
            .. ' --max-filesize ' .. tostring(max_download_bytes) .. ' --output -'
            .. '; download_status=$?; printf "%s\\n" "$download_status" >'
            .. shell_quote(status_path) .. ') 2>/dev/null'
    log_debug("running bounded download: " .. command)
    local pipe = io.popen(wrapped, 'r')
    if not pipe then
        remove_path(status_path)
        log_report("B39427 cannot start download")
        return nil
    end
    local content, read_error = pipe:read(max_download_bytes + 1)
    pipe:close()  -- Lua 5.1 does not reliably return the child's exit status here
    local status = read_text_file(status_path, true)
    remove_path(status_path)
    content = content or ''
    if read_error then log_report("B76932 curl failed; read_error=" .. tostring(read_error)) end
    if #content > max_download_bytes then log_report("B42170 curl failed; bytes=" .. tostring(#content)) end
    if status ~= '0' then
        log_report("B82691 curl failed; rc=" .. (status ~= '' and status or '(missing)'))
    end
    if read_error or #content > max_download_bytes or status ~= '0' then return nil end
    return write_text_file(path, content)
end

local function copy_table(source)
    local copy = {}
    for key, value in pairs(source) do copy[key] = value end
    return copy
end

local function write_text_file_atomic(path, content, mode)  -- well-tested but ended up not using
    -- return true iff successful; fail if path already exists
    if is_directory(path) then
        log_error("B73491 " .. path .. " is a directory")
        return nil
    end
    log_debug("writing atomically " .. tostring(#content) .. " bytes to: " .. path)
    log_debug("--data: " .. displayable(content, 60))
    local tmp_path = make_temp_path(dirname(path))
    if not tmp_path then return nil end
    local ok = nil
    repeat
        local handle = io.open(tmp_path, 'w')
        if not handle then
            log_error("B90582 cannot write temp file: " .. tmp_path)
            break
        end
        local write_ok, write_err = handle:write(content)
        local close_ok, close_err = handle:close()
        if not write_ok then
            log_error("B07354 cannot write " .. tmp_path .. " (" .. tostring(write_err) .. ")")
            break
        end
        if close_ok == nil then
            log_error("B28145 cannot close " .. tmp_path .. " (" .. tostring(close_err) .. ")")
            break
        end
        if mode and not chmod(tmp_path, mode) then break end
        if not run_command('ln ' .. shell_quote(tmp_path) .. ' ' .. shell_quote(path), true, true) then
            log_debug("B32426 cannot atomically create file, maybe it already exists: " .. path)
            break
        end
        ok = true
    until true
    remove_path(tmp_path)
    return ok
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
    if not dst then
        log_error("B73772 cannot open " .. tmp_path .. " (" .. tostring(err) .. ")")
        src:close()
        remove_path(tmp_path)
        return nil
    end
    while true do
        local chunk, read_err = src:read(8192)
        if not chunk then
            if read_err then
                log_error("B27035 reading " .. src_path .. " failed (" .. tostring(read_err) .. ")")
                src:close()
                dst:close()
                remove_path(tmp_path)
                return nil
            end
            break
        end
        local ok, werr = dst:write(chunk)
        if not ok then
            log_error("B79472 write to " .. tmp_path .. " failed (" .. tostring(werr) .. ")")
            src:close()
            dst:close()
            remove_path(tmp_path)
            return nil
        end
    end
    local src_close_ok, src_close_err = src:close()
    local dst_close_ok, dst_close_err = dst:close()
    if src_close_ok == nil then
        log_error("B90352 cannot close " .. src_path .. " (" .. tostring(src_close_err) .. ")")
        remove_path(tmp_path)
        return nil
    end
    if dst_close_ok == nil then
        log_error("B08324 cannot close " .. tmp_path .. " (" .. tostring(dst_close_err) .. ")")
        remove_path(tmp_path)
        return nil
    end
    if not chmod(tmp_path, mode) then
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
-- fractional sleep
--

local sleep_methods = {
    'nixio.nanosleep',  -- method 1
    'socket.sleep',
    'sleep [float]',
    'ucode sleep',
    'busybox usleep',
    '/proc/uptime loop',
    'sleep [int]',  -- method 7
}
local sleep_modules = {}

local function uptime_seconds()
    -- avoid read_text_file() here; the '/proc/uptime loop' method reads this in a tight loop
    local handle = io.open('/proc/uptime', 'r')
    if not handle then return nil end
    local line = handle:read('*l')
    handle:close()
    return tonumber(tostring(line or ''):match('^(%S+)'))
end

local function sleep_module(name)
    if sleep_modules[name] == nil then
        local ok, module_or_error = pcall(require, name)
        if ok then
            sleep_modules[name] = module_or_error
        else
            sleep_modules[name] = false
        end
    end
    return sleep_modules[name] or nil
end

local function sleep_using_method(method, seconds)
    if method == 1 then  -- nixio.nanosleep
        local nixio = sleep_module('nixio')
        if not nixio or type(nixio.nanosleep) ~= 'function' then
            return nil, 'nixio.nanosleep is unavailable'
        end
        local deadline = uptime_seconds()
        deadline = deadline and (deadline + seconds) or nil
        while true do  -- retry remaining duration after an EINTR interrupt
            local whole_seconds = math.floor(seconds)
            local nanoseconds = math.floor(
                (seconds - whole_seconds) * 1000000000 + 0.5
            )
            if nanoseconds >= 1000000000 then
                whole_seconds = whole_seconds + 1
                nanoseconds = 0
            end
            local call_ok, result, error_number, error_text = pcall(
                nixio.nanosleep,
                whole_seconds,
                nanoseconds
            )
            if not call_ok then return nil, tostring(result) end
            if result == true then return true end
            if not nixio.const or error_number ~= nixio.const.EINTR then
                return nil, tostring(error_text or 'nixio.nanosleep returned failure')
            end
            local now = deadline and uptime_seconds() or nil
            if not now then
                return nil, tostring(error_text or 'nixio.nanosleep was interrupted')
            end
            seconds = deadline - now
            if seconds <= 0 then return true end
        end
    elseif method == 2 then  -- socket.sleep
        local socket = sleep_module('socket')
        if not socket or type(socket.sleep) ~= 'function' then
            return nil, 'socket.sleep is unavailable'
        end
        local ok, call_problem = pcall(socket.sleep, seconds)
        if not ok then return nil, tostring(call_problem) end
        return true
    elseif method == 3 then  -- sleep [float]
        if run_command('sleep ' .. string.format('%.6f', seconds), true, true) == nil then
            return nil, 'sleep [float] failed'
        end
        return true
    elseif method == 4 then  -- ucode sleep
        local milliseconds = math.max(1, math.floor(seconds * 1000 + 0.5))
        local ucode = 'if (!sleep(' .. tostring(milliseconds) .. ')) { exit(1); }'
        local command = 'ucode -e ' .. shell_quote(ucode)
        if run_command(command, true, true) == nil then
            return nil, 'ucode sleep failed'
        end
        return true
    elseif method == 5 then  -- busybox usleep
        local microseconds = math.max(1, math.floor(seconds * 1000000 + 0.5))
        if run_command('busybox usleep ' .. tostring(microseconds), true, true) == nil then
            return nil, 'busybox usleep failed'
        end
        return true
    elseif method == 6 then  -- /proc/uptime loop
        local now = uptime_seconds()
        if not now then return nil, 'cannot read /proc/uptime' end
        local deadline = now + seconds
        -- sleep the integer portion; it's more efficient than looping (okay if it fails)
        run_command('sleep ' .. tostring(math.floor(seconds)), true, true)
        repeat
            now = uptime_seconds()
            if not now then return nil, 'cannot read /proc/uptime' end
        until now >= deadline
        return true
    elseif method == 7 then  -- sleep [int]
        local round_up = math.floor(seconds + 0.9)  -- 3.09 → 3 but 3.1 → 4
        if run_command('sleep ' .. tostring(round_up), true, true) == nil then
            return nil, 'sleep [int] failed'
        end
        return true
    end
    return nil, "unknown sleep method " .. tostring(method)
end

local function set_sleep_method(test_all_methods)
    local test_seconds = 0.3
    local minimum_elapsed = 0.25
    sleep_method = nil
    for method = 1, #sleep_methods do
        local saved_logging_level = logging_level
        logging_level = 20  -- disable run_command() logging while we probe
        local started = uptime_seconds()
        local ok, problem = sleep_using_method(method, test_seconds)
        local finished = uptime_seconds()
        logging_level = saved_logging_level
        local elapsed = started and finished and (finished - started) or nil
        local m_text = "sleep method " .. tostring(method) .. " (" .. sleep_methods[method] .. ")"
        if ok and elapsed and elapsed >= minimum_elapsed then
            log_debug(m_text .. " succeeded: " .. string.format('%.2f', elapsed) .. " seconds")
            sleep_method = sleep_method or method  -- use first method that works on this base
            if not test_all_methods then
                return true
            end
        else
            log_debug(m_text .. " failed: " .. tostring(problem))
        end
    end
    if sleep_method == nil then
        log_error("B68347 no usable sleep method")
        return nil
    end
    return true
end

local function sleep(seconds)
    if type(seconds) ~= 'number' or seconds ~= seconds
            or seconds < 0 or seconds == math.huge then
        -- the odd 'seconds ~= seconds' checks for NaN (not a number)
        log_error("B15064 invalid sleep duration " .. tostring(seconds))
        return nil
    end
    if seconds == 0 then return true end
    if not sleep_method then return nil end
    local ok, problem = sleep_using_method(sleep_method, seconds)
    if not ok then
        local message = "B47295 " .. sleep_methods[sleep_method] .. " failed to sleep for "
                .. tostring(seconds) .. " seconds: " .. tostring(problem)
        log_error(message)
        return nil
    end
    return true
end

--
-- launch method for run_command_for()
--

local function set_launch_method()
    if run_command('command -v dd', true, true) and uptime_seconds() then
        if run_command('command -v setsid', true, true) then
            launch_method = 'setsid'
        elseif run_command('command -v start-stop-daemon', true, true) then
            launch_method = 'start-stop-daemon'
        end
    end
    if not launch_method then
        log_warning("B44368 timed commands need dd, /proc/uptime, and either setsid or start-stop-daemon")
        return false
    end
    if launch_method == 'start-stop-daemon' then
        our_pgid = read_text_file('/proc/self/stat', true):match('^%d+ %(.+%) %S+ %d+ (%d+) ')
        if not our_pgid then
            launch_method = nil
            log_warning("B43025 cannot determine our process group")
            return false
        end
    end
    return true
end

--
-- process management
--

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

local function normalize_pid(pid)
    local normalized = tostring(pid or ''):match('^0*([1-9]%d*)$')
    if normalized == '1' then
        return nil
    end
    return normalized
end

local function is_running(pid)
    -- note: pid must be normalized, e.g. normalize_pid()
    local stat = io.open('/proc/' .. pid .. '/stat', 'r')
    if stat then
        local content = stat:read('*l')
        stat:close()
        local state = content and content:match('^%d+ %(.+%) (%S) ')
        if state == 'Z' then return false end
    end
    return run_command('kill -0 ' .. pid, true, true) ~= nil
end

local function run_command_for(command, max_seconds, merge_stderr, failure_ok)
    -- return captured output, nil, 0, false on success; otherwise nil, output/error, code, timed_out
    local script_path, output_path, status_path, pid_path
    local output, exit_code, failure, pgid
    local timed_out = false
    repeat
        if type(command) ~= 'string' or command:find('\0', 1, true) then
            failure = "B60814 command must be a string without NUL bytes"
            break
        end
        if type(max_seconds) ~= 'number' or max_seconds ~= max_seconds
                or max_seconds <= 0 or max_seconds == math.huge then
            failure = "B50412 max_seconds must be a positive finite number"
            break
        end
        log_debug("running timed command: " .. command)
        if not launch_method then
            failure = "B60459 no usable timed-command launcher"
            break
        end
        script_path = make_temp_path(nil, true)
        output_path = make_temp_path(nil, true)
        status_path = make_temp_path(nil, true)
        if launch_method == 'start-stop-daemon' then pid_path = make_temp_path(nil, true) end
        if not script_path or not output_path or not status_path
                or (launch_method == 'start-stop-daemon' and not pid_path) then
            failure = "B95013 cannot create command temporary files"
            break
        end
        if not write_text_file(script_path, command, '0600') then
            failure = "B87623 cannot write command script"
            break
        end
        local runner = '{ sh ' .. shell_quote(script_path)
                .. (merge_stderr and ' 2>&1' or ' 2>/dev/null')
                .. '; rc=$?; printf "%s\\n" "$rc" >' .. shell_quote(status_path) .. '; }'
        -- below, bs=1 preserves partial output if process takes too long
        runner = runner .. ' | { dd bs=1 count=' .. string.format('%.0f', 18001)
                .. ' 2>/dev/null; cat >/dev/null; }'
        local started = uptime_seconds()
        if not started then
            failure = "B17892 cannot read command deadline clock"; break
        end
        local deadline = started + max_seconds
        local pid
        -- note: if base.lua is killed by service manager, detached shell can continue forever
        if launch_method == 'setsid' then
            pid = normalize_pid(run_command('(exec 9>&-; setsid sh -c ' .. shell_quote(runner)
                .. ' </dev/null >' .. shell_quote(output_path) .. ' 2>&1 & echo $!)', true, true))
            pgid = pid
        else
            -- 'start-stop-daemon -b' creates a session and can double-fork; PID and PGID can
            -- differ; https://lists.busybox.net/pipermail/busybox-cvs/2023-November/041700.html
            -- the acknowledgement prevents execution if startup fails; redirection must be
            -- inside the wrapper because start-stop-daemon redirects its own output
            local wrapped = 'exec 9>&-; exec </dev/null >' .. shell_quote(output_path) .. ' 2>&1; '
                    .. 'read -r stat </proc/$$/stat || exit 1; set -- ${stat##*) }; '
                    .. 'printf "%s %s\\n" "$$" "$3" >' .. shell_quote(pid_path) .. ' || exit 1; '
                    .. 'for attempt in 1 2 3 4 5 6 7 8 9 10; do '
                    .. 'if read -r state <' .. shell_quote(pid_path) .. ' && [ "$state" = go ]; then '
                    .. runner .. '; exit; fi; sleep 1; done; exit 1'
            local launched = run_command('(exec 9>&-; start-stop-daemon -S -b -p '
                .. shell_quote(pid_path) .. ' -x /bin/sh -- -c ' .. shell_quote(wrapped) .. ')', true, true)
            while launched do
                local child, group = read_text_file(pid_path, true, true):match('^(%d+) (%d+)\n$')
                if child then
                    pid, pgid = normalize_pid(child), normalize_pid(group)
                    break
                end
                local now = uptime_seconds()
                if not now then
                    failure = "B95323 cannot read command deadline clock"; break
                end
                if now >= deadline then
                    timed_out = true; break
                end
                if now - started >= 5 or not sleep(math.min(0.1, deadline - now)) then break end
            end
            if pid then
                if not pgid or pgid == our_pgid then
                    run_command('kill -KILL ' .. pid, true, true)
                    pid, pgid = nil, nil
                elseif not write_text_file(pid_path, 'go\n') then
                    pid = nil
                end
            end
        end
        if failure or timed_out then break end
        if not pid then
            failure = "B61884 cannot start command shell"; break
        end
        while is_running(pid) do
            local now = uptime_seconds()
            if not now then
                failure = "B99386 cannot read command deadline clock"; break
            end
            if now >= deadline then
                timed_out = true; break
            end
            if not sleep(math.min(0.1, deadline - now)) then
                failure = "B98271 cannot wait for command"
                break
            end
        end
    until true
    -- cleanup also stops children left behind when their parent shell exits
    if pgid then run_command('kill -KILL -' .. pgid, true, true) end
    if not failure then
        local handle = io.open(output_path, 'r')
        if handle then
            local read_error
            output, read_error = handle:read(18001)
            local closed = handle:close()
            if read_error or not closed then failure = "B51683 cannot read command output" end
            output = output or ''
        else
            failure = "B99921 cannot open command output"
        end
        if not failure then
            exit_code = timed_out and 124 or tonumber(read_text_file(status_path, true):match('^%d+$'))
            if not exit_code then failure = "B13525 could not determine command exit status" end
        end
    end
    remove_paths(script_path, output_path, status_path, pid_path)
    if failure then output, exit_code = failure, nil end
    local result, error_output = run_command_result(tostring(command), output, exit_code, failure_ok)
    return result, error_output, exit_code, timed_out
end

local function cleanup_and_exit(exit_code)
    log_debug("cleaning up lock state and exiting")
    local pid = get_pid()
    local lock_pid = read_text_file(lock_dir_pid_path, true)
    if pid and lock_pid == pid then
        remove_path(lock_dir_stop_request_path, false)  -- file may not exist
        remove_path(lock_dir_pid_path, true)
    else
        log_warning("B00765 our pid (" .. tostring(pid) .. ") and lock pid ("
            .. tostring(lock_pid) .. ") differ; not removing")
    end
    close_log()
    os.exit(exit_code)
end

--
-- JSON integration
--

local json  -- loaded from the resolved release directory below

local function decode_json(body, max_bytes, max_depth)
    if type(body) ~= 'string' or (max_bytes and #body > max_bytes) then return nil end
    -- we could consider checking nesting and item count here for routers with minimal RAM
    local ok, value, pos, err = pcall(json.decode, body, 1, json.null)
    -- dkjson decodes one value; require the complete response to contain only that value
    if not ok or err or body:find('%S', pos) then return nil end
    if max_depth then
        local function within_depth(item, depth)
            if depth > max_depth then return false end
            if type(item) == 'table' then
                for _, child in pairs(item) do
                    if not within_depth(child, depth + 1) then return false end
                end
            end
            return true
        end
        if not within_depth(value, 0) then return nil end
    end
    return value
end

local function json_container(value, kind)
    local meta = type(value) == 'table' and getmetatable(value)
    return meta and meta.__jsontype == kind
end

local function jsonrpc_result(body, max_bytes, max_depth)
    local response = decode_json(body, max_bytes, max_depth)
    if not json_container(response, 'object') or response.jsonrpc ~= '2.0' or response.id ~= 1
            or response.error ~= nil or not json_container(response.result, 'object') then
        return nil
    end
    return response.result
end

--
-- helper functions
--

local function sleep_with_jitter(base_seconds, jitter_fraction)
    -- sleep base_seconds ± some jitter; exit gracefully on 'stop_request' file
    local min_seconds = math.floor(base_seconds * (1 - jitter_fraction))
    local max_seconds = math.ceil(base_seconds * (1 + jitter_fraction))
    if min_seconds < 0 then
        min_seconds = 0
    end
    if max_seconds < min_seconds then
        max_seconds = min_seconds
    end
    local secs = math.random(min_seconds, max_seconds)
    local message = "sleeping for " .. tostring(secs) .. " seconds (base="
            .. tostring(base_seconds) .. ", jitter=" .. tostring(jitter_fraction) .. ")"
    log_debug(message)
    while secs > 0 do
        if is_readable(lock_dir_stop_request_path) then
            log_warning("B72755 stop_request")
            cleanup_and_exit(0)
        end
        local interval = math.min(secs, 2)
        if not sleep(interval) then cleanup_and_exit(13) end
        secs = secs - interval
    end
end

local function run_tests()
    -- returns true only if all tests pass
    local t1 = set_sleep_method(true)
    return t1
end

local function read_base_config()
    local handle, problem, errno = io.open(base_config_path, 'r')
    if not handle then
        if errno == 2 then return {} end  -- fresh install; settings passed via HUBCONF env var
        return nil, "B53281 cannot open " .. base_config_path .. " (" .. tostring(problem) .. ")"
    end
    local text, read_error = handle:read('*a')
    local close_ok, close_error = handle:close()
    if text == nil then
        return nil, "B21408 cannot read " .. base_config_path .. " (" .. tostring(read_error) .. ")"
    end
    if close_ok == nil then
        return nil, "B40316 cannot close " .. base_config_path .. " (" .. tostring(close_error) .. ")"
    end
    local values = decode_json(text)
    if not json_container(values, 'object') then
        return nil, "B82637 expected a JSON object in " .. base_config_path
    end
    for key, value in pairs(values) do
        if not key:match('^[a-z][a-z0-9_]*$') or type(value) ~= 'string' then
            return nil, "B15724 invalid config field in " .. base_config_path .. ': ' .. displayable(key, 80)
        end
    end
    return values
end

local function save_base_config(values)
    local keys = {}
    for key in pairs(values) do keys[#keys + 1] = key end
    table.sort(keys)
    local object = setmetatable(values, {__jsontype = 'object'})
    local ok, text_data = pcall(json.encode, object, {indent = true, keyorder = keys})
    if not ok then
        log_error("B70246 cannot encode " .. base_config_path .. " (" .. tostring(text_data) .. ")")
        return nil
    end
    local temp_path = make_temp_path(config_dir)
    if not temp_path then return nil end
    if not write_text_file(temp_path, text_data .. '\n', '0600') then
        remove_path(temp_path)
        return nil
    end
    local rename_ok, rename_error = os.rename(temp_path, base_config_path)
    if not rename_ok then
        log_error("B28691 cannot replace " .. base_config_path .. " (" .. tostring(rename_error) .. ")")
        remove_path(temp_path)
        return nil
    end
    log_debug("saved JSON to " .. base_config_path)
    return true
end

local function clear_pending_task_result()
    local updated_config = copy_table(base_config)
    updated_config.pending_task_id = nil
    updated_config.pending_update_target = nil
    updated_config.pending_task_method = nil  -- remove obsolete value
    updated_config.pending_update_state = nil  -- remove obsolete value
    if not save_base_config(updated_config) then return nil end
    base_config = updated_config
    return true
end

--
-- CLI
--

local cli_verb = nil
local running_path = arg and arg[0] or ''
if running_path == '' then fail_early("B72200 cannot find running_path") end

local function print_help()
    local program = arg and arg[0] or 'base.lua'
    io.stdout:write(table.concat({
        "Usage: " .. program .. " [options] <verb>",
        "",
        "Manage or run the BitBurrow base daemon.",
        "",
        "Verbs:",
        "  install      Install or reinstall the daemon as a system service.",
        "  daemonize    Run the daemon in the foreground. This internal verb is",
        "               invoked by a service wrapper holding the lock.",
        "  run-tests    Run the self-tests and exit.",
        "  version      Show the version and exit.",
        "  help         Show this help message and exit.",
        "",
        "Options:",
        "  -v, -vv, ... Increase logging verbosity.",
        "  --verbose    Increase logging verbosity by one level.",
        "",
    }, "\n"), "\n")
end

local function set_cli_verb(value)
    if cli_verb ~= nil then
        log_error("cannot use two verbs (" .. cli_verb .. ", " .. value .. ")")
        print_help()
        os.exit(1)
    end
    cli_verb = value
end

for _, value in ipairs(arg) do
    local v = value:match("^%-(v+)$")
    if v then
        logging_level = logging_level - #v * 10
    elseif value == "--verbose" then
        logging_level = logging_level - 10
    elseif value == '-h' or value == '--help' then
        set_cli_verb('help')
    elseif value == '--version' then
        set_cli_verb('version')
    elseif value:sub(1, 1) == '-' then
        log_error("invalid argument: " .. value)
        print_help()
        os.exit(1)
    else
        set_cli_verb(value)
    end
end
if cli_verb == 'daemonize' then
    -- validate the service wrapper's PID and inherited lock before reading shared state
    local pid = get_pid()
    if not pid or read_text_file(lock_dir_pid_path, true) ~= pid then
        -- bypassing lock can overwrite a newer base.conf or replay counter
        fail_early("B36581 daemon PID does not match the service wrapper; start through the service")
    end
    -- keep fd 9 in this child; flock must act on the inherited open file description
    local command = 'test /proc/self/fd/9 -ef ' .. shell_quote(lock_file_path)
            .. ' && flock -n -x 9 >/dev/null 2>&1'
    local locked = os.execute(command)
    if locked ~= 0 and locked ~= true then  -- Lua 5.1 and 5.2+ return values
        fail_early("B46279 daemon requires the service lock on fd 9; start through the service")
    end
end
local file_xver, install_dir, running_dir
do
    -- follow the service symlink so dkjson comes from the same release as base.lua
    local resolved = run_command('readlink -f ' .. shell_quote(running_path), true, true)
    if not resolved or resolved:sub(1, 1) ~= '/' then
        fail_early("B47135 cannot resolve running script: " .. running_path)
    end
    running_path = resolved
    running_dir = dirname(running_path)
    local ok, module = pcall(dofile, running_dir .. '/dkjson.lua')
    if not ok then
        log_error("B43891 cannot load dkjson.lua: " .. tostring(module))
        close_log()
        os.exit(1)
    end
    json = module
end
local function valid_xver(value)
    return type(value) == 'string' and #value >= 12 and value:sub(8, 8) == '-'
            and value:match('^[0-9a-z]+%-[0-9a-z]+$')
end
if cli_verb == 'install' or cli_verb == 'daemonize' or cli_verb == 'version' then
    if cli_verb ~= 'version' then
        local problem
        base_config, problem = read_base_config()
        if not base_config then fail_early(problem) end
    end
    if cli_verb ~= 'install' then
        file_xver = basename(running_dir)
        install_dir = dirname(running_dir)
        if not valid_xver(file_xver) then fail_early("B62917 cannot determine executing xver") end
    end
end
if cli_verb == nil then
    log_error("a verb is required")
    print_help()
    os.exit(1)
elseif cli_verb == 'help' then
    print_help()
    os.exit(0)
elseif cli_verb == 'version' then
    print("BitBurrow base daemon, version " .. file_xver)
    os.exit(0)
elseif cli_verb == 'install' then
    -- handled below
elseif cli_verb == 'run-tests' then
    if run_tests() then
        os.exit(0)
    else
        log_error("not all tests passed (use '-v' to see details)")
        os.exit(1)
    end
elseif cli_verb == 'daemonize' or cli_verb == 'delete-adopt5c-code' then
    -- handled below
else
    log_error("invalid verb: " .. cli_verb)
    print_help()
    os.exit(1)
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
        run_command('mkdir -p ' .. shell_quote(path), true, true)
        if is_writable(path) then
            return path
        end
    end
    log_error("B95830 cannot find_install_dir(); home is " .. home)
    return nil
end

local function locked_runner_script(lua_path)
    return table.concat({
        'TMPDIR=' .. shell_quote(tmp_dir),
        'export TMPDIR',
        'HUBCONF=' .. shell_quote(hubconf),
        'export HUBCONF',
        'mkdir -p ' .. shell_quote(bbsubd_tmp_dir) .. ' || exit 1',
        'chmod 0700 ' .. shell_quote(bbsubd_tmp_dir) .. ' 2>/dev/null',
        'mkdir -p ' .. shell_quote(lock_dir) .. ' || exit 1',
        'chmod 0700 ' .. shell_quote(lock_dir) .. ' 2>/dev/null',
        'exec 9>' .. shell_quote(lock_file_path) .. ' || exit 1',
        'i=0',
        'while [ "$i" -lt 7 ]; do',
        '    if flock -n 9; then',
        '        rm -f ' .. shell_quote(lock_dir_stop_request_path),
        '        echo $$ > ' .. shell_quote(lock_dir_pid_path) .. ' || exit 1',
        '        exec ' .. shell_quote(lua_exe) .. ' ' .. shell_quote(lua_path) .. ' daemonize',
        '    fi',
        '    # too noisy: logger -t ' .. shell_quote(bbsubd) .. ' "B04983 waiting for another instance to quit"',
        '    sleep 1',
        '    i=$((i + 1))',
        'done',
        'logger -t ' .. shell_quote(bbsubd) .. ' "B86695 giving up; another instance is still running"',
        'exit 0',
    }, '\n')
end

local function stop_runner_script(message, indent)
    indent = indent or ''
    return indent .. table.concat({
        'logger -t ' .. shell_quote(bbsubd) .. ' ' .. shell_quote(message),
        'mkdir -p ' .. shell_quote(lock_dir),
        'touch ' .. shell_quote(lock_dir_stop_request_path),
        'for i in 0 1 2 3 4 5 6; do',
        '    pid="$(cat ' .. shell_quote(lock_dir_pid_path) .. ' 2>/dev/null)"',
        '    [ -z "$pid" ] && break',
        '    kill -0 "$pid" 2>/dev/null || break',
        '    sleep 1',
        'done',
        '# do not remove lock_dir or lock_file_path here',
    }, '\n' .. indent)
end

local function remove_temporary_installer()
    -- remove only the exact temporary layout created by adopt5p.sh
    if running_path:sub(1, #tmp_dir) ~= tmp_dir then return end
    local relative_path = running_path:sub(#tmp_dir + 1)
    if not relative_path:match('^bbbase%.[^/]+/base%.lua$') then return end
    remove_paths(running_path, running_dir .. '/dkjson.lua', running_dir)
end

local function init_service_wrapper_text(lua_path)
    local locked_runner = locked_runner_script(lua_path)
    local stop_runner = stop_runner_script('stop_service called from procd', '    ')
    return table.concat({
        '#!/bin/sh /etc/rc.common',
        '',
        'START=95',
        'STOP=10',
        'USE_PROCD=1',
        '',
        'start_service() {',
        '    procd_open_instance',
        '    procd_set_param command /bin/sh -c ' .. shell_quote(locked_runner),
        '    procd_set_param file ' .. shell_quote(lua_path),
        '    procd_set_param respawn 60 10 0',  -- up for 60 seconds clears the crash count
        '    procd_set_param stdout 1',  -- 1 means make output viewable via `logread`
        '    procd_set_param stderr 1',
        '    procd_close_instance',
        '}',
        '',
        'stop_service() {\n' .. stop_runner,
        '}',
        '',
    }, '\n')
end

local function systemd_service_wrapper_texts(lua_path)
    local start_text = '#!/bin/sh\n' .. locked_runner_script(lua_path) .. '\n'
    local stop_text = '#!/bin/sh\n' .. stop_runner_script('stop called from systemd') .. '\nexit 0\n'
    local service_text = table.concat({
        '[Unit]',
        'Description=' .. bbsubd .. ' daemon',
        'After=network-online.target',
        'Wants=network-online.target',
        '',
        '[Service]',
        'Type=simple',
        'ExecStart=/usr/local/sbin/' .. bbsubd .. '-start.sh',
        'ExecStop=/usr/local/sbin/' .. bbsubd .. '-stop.sh',
        'Restart=always',
        'RestartSec=5',
        'StandardOutput=journal',
        'StandardError=journal',
        '',
        '[Install]',
        'WantedBy=multi-user.target',
        '',
    }, '\n')
    return start_text, stop_text, service_text
end

local function install_service_wrapper_file(path, content, mode)
    if read_text_file(path, true, true) == content and get_mode(path) == mode then
        return true
    end
    local tmp_path = make_temp_path(tmp_dir)  -- bbsubd_tmp_dir may not exist during installation
    if not tmp_path then return nil end
    local installed = write_text_file(tmp_path, content) and file_copy(tmp_path, path, mode)
    remove_path(tmp_path)
    return installed
end

local function install_init_service(lua_path)
    local init_path = '/etc/init.d/' .. bbsubd
    if not install_service_wrapper_file(init_path, init_service_wrapper_text(lua_path), '0755') then
        return nil
    end
    local command = shell_quote(init_path)
    if not run_command(command .. ' enabled', true, true)
            and not run_command(command .. ' enable') then
        return nil
    end
    local running = run_command(command .. ' running', true, true)
    return run_command(command .. (running and ' restart' or ' start'))
end

local function install_systemd_service(lua_path)
    local service_name = bbsubd .. '.service'
    local service_path = '/etc/systemd/system/' .. service_name
    local start_script_path = '/usr/local/sbin/' .. bbsubd .. '-start.sh'
    local stop_script_path = '/usr/local/sbin/' .. bbsubd .. '-stop.sh'
    local start_text, stop_text, service_text = systemd_service_wrapper_texts(lua_path)
    if not install_service_wrapper_file(start_script_path, start_text, '0755')
            or not install_service_wrapper_file(stop_script_path, stop_text, '0755')
            or not install_service_wrapper_file(service_path, service_text, '0644') then
        return nil
    end
    remove_path('/etc/init.d/' .. bbsubd)
    if not run_command('systemctl daemon-reload') then return nil end
    local service = shell_quote(service_name)
    if not run_command('systemctl is-enabled ' .. service, true, true)
            and not run_command('systemctl enable ' .. service) then
        return nil
    end
    local running = run_command('systemctl is-active ' .. service, true, true)
    return run_command((running and 'systemctl restart ' or 'systemctl start ') .. service)
end

-- values must match class Platform() in db.py
local platform = run_command('command -v systemctl', true, true) and 'sysd' or 'init'

local function install_daemon_service(lua_path)
    local install_service = platform == 'sysd' and install_systemd_service or install_init_service
    local installed = install_service(lua_path)
    if installed then remove_temporary_installer() end
    return installed
end

local packager_cmds = nil
local packager_specs = {
    {'apt-get', {update = {'apt-get', 'update'}, install = {'apt-get', 'install', '-y'}}},
    {'apk', {update = {'apk', 'update'}, install = {'apk', 'add'}}},
    {'opkg', {update = {'opkg', 'update'}, install = {'opkg', 'install'}}},
    {'dnf', {update = {'dnf', 'makecache'}, install = {'dnf', 'install', '-y'}}},
    {'yum', {update = {'yum', 'makecache'}, install = {'yum', 'install', '-y'}}},
    {'pacman', {update = {'pacman', '-Sy', '--noconfirm'}, install = {'pacman', '-S', '--noconfirm'}}},
    {'zypper', {update = {'zypper', '--non-interactive', 'refresh'}, install = {'zypper', '--non-interactive', 'install'}}},
}
---@diagnostic disable-next-line: deprecated
local unpack_fn = table.unpack or unpack  -- support Lua 5.1

local function packager(action, arg)
    if not packager_cmds then  -- find first valid package manager and cache it for future calls
        for i = 1, #packager_specs do
            local candidate = packager_specs[i]
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

local install_one_of_cache = {}  -- cache results of successful calls to install_one_of()

local function install_one_of(package_list, command)
    local package_cache = install_one_of_cache[package_list]
    if package_cache and package_cache[command] then
        return true
    end
    package_cache = package_cache or {}
    install_one_of_cache[package_list] = package_cache
    for retry = 0, 2 do  -- 0, 1, 2
        if run_command('command -v ' .. command, true, true) then
            package_cache[command] = true
            return true
        end
        if retry >= 2 then  -- before the last iteration, wait
            sleep(75)
        end
        if retry >= 1 then  -- before the last 2 iterations, run an update
            packager('update')
        end
        for pkg in package_list:gmatch("%S+") do
            packager('install', pkg)
            if run_command('command -v ' .. command, true, true) then
                log_info("installed package " .. pkg .. " for " .. command)
                package_cache[command] = true
                return true
            end
        end
    end
    log_error("B80574 cannot install package for " .. command)
    -- don't update package_cache[command] so that next call will retry
    return nil
end

local function delete_adopt5c_code(path)
    -- removes adopt5c_code from path, normally '/etc/rc.local'
    -- return true iff adopt5c code was not found
    -- return false iff adopt5c code was found and deleted
    -- return nil on failure
    local prefixes = {  -- should mirror get_adopt5c_code(); search: tag_adopt5c_code
        'if ip -4 addr',
        '  s=,',
        '  whi',
        '    [',
        '    c',
        '     ',
        '     ',
        '    e',
        '    a',
        '    p',
        '    c',
        '     ',
        '    e',
        '    s',
        '  don',
        '  exi',
        '}; th',
        '  r=s',
        '  r=$',
        '  OCT',
        '  u()',
        '  u i',
        '  u n',
        '  u p',
        '  uci',
        '  /et',
        '  /et',
        '  rm ',
        '  kil',
        'fi',
        'T=',
        'echo ',
        'echo ',
        'U=http',
        'sleep 51',
    }
    if run_command('test -L ' .. shell_quote(path), false, true) then  -- if is a symlink
        path = run_command('readlink -f ' .. shell_quote(path), true)
        if not path then return nil end
    end
    local mode = get_mode(path)
    if not mode then
        local handle, _, error_code = io.open(path, 'r')
        if handle then handle:close() end
        if error_code == 2 then return true end  -- no bootstrap code to remove
        log_error("B70513 cannot get mode for " .. path)
        return nil
    end
    local content = read_text_file(path, false, true)
    if content == nil then return nil end
    local lines = {}
    local pos = 1
    while pos <= #content do
        local eol_start, eol_end, eol = content:find('(\r?\n)', pos)
        if eol_start then
            lines[#lines + 1] = {
                text = content:sub(pos, eol_start - 1),
                eol = eol,
            }
            pos = eol_end + 1
        else
            lines[#lines + 1] = {
                text = content:sub(pos),
                eol = '',
            }
            break
        end
    end
    local delete_at = nil
    for i = 1, #lines - #prefixes + 1 do
        local found = true
        for j = 1, #prefixes do
            local line = lines[i + j - 1].text
            local prefix = prefixes[j]
            if line:sub(1, #prefix) ~= prefix  -- don't delete another base's lines
                    or (prefix == 'T=' and line:match('^T=/tmp/(%S+)%s*$') ~= ott_filename) then
                found = false
                break
            end
        end
        if found then
            delete_at = i
            break
        end
    end
    if not delete_at then
        log_warning("adopt5c code not found in " .. path)
        return true
    end
    local kept = {}
    for i = 1, #lines do
        if i < delete_at or i >= delete_at + #prefixes then
            kept[#kept + 1] = lines[i].text .. lines[i].eol
        end
    end
    local new_content = table.concat(kept)
    local temp_path = make_temp_path(dirname(path))
    if not temp_path then return nil end
    if not write_text_file(temp_path, new_content, mode) then
        remove_path(temp_path)
        return nil
    end
    local replaced, err = os.rename(temp_path, path)
    if not replaced then
        log_error("B76534 cannot replace " .. path .. " (" .. tostring(err) .. ")")
        remove_path(temp_path)
        return nil
    end
    log_info("B25039 deleted adopt5c code from " .. path)
    return false
end

--
-- keys management
--

math.randomseed(os.time() + tonumber(get_pid() or '0'))
local auth_privkey_path = config_dir .. 'client_rsapss.pem'
local auth_pubkey_path = config_dir .. 'client_rsapss_pub.pem'
local pubkeys_uploaded_path = config_dir .. 'pubkeys_uploaded'
-- on some systems, AppArmor needs wg keys in /etc/wireguard/; see https://www.mail-archive.com/ubuntu-bugs%40lists.ubuntu.com/msg6274314.html
local wg_privkey_path = '/etc/wireguard/' .. bbsubd .. '_private.key'
local wg_pubkey_path = '/etc/wireguard/' .. bbsubd .. '_public.key'

local function run_command_with_umask_077(command)
    local wrapped = 'if umask 077; then ' .. command .. '; else false; fi'
    -- don't need to undo umask because io.popen() starts the command in a separate process
    return run_command(wrapped, true)
end

local function ensure_auth_keys()
    if is_readable(auth_privkey_path) and is_readable(auth_pubkey_path) then
        log_debug("auth_privkey and auth_pubkey both already exist")
        return true
    end
    -- could recover if privkey still exists, but we're not going to such extremes to cover user error
    log_info("authentication keys are missing; generating new keypair")
    remove_paths(auth_privkey_path, auth_pubkey_path)  -- privkey so it is recreated with umask 077
    -- note: OpenSSL 1.1.1 found on test routers can't sign with Ed25519 keys
    local output = run_command_with_umask_077(
        'openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out '
        .. shell_quote(auth_privkey_path)
    )
    if not output then
        remove_paths(auth_privkey_path, auth_pubkey_path)
        return nil
    end
    output = run_command_with_umask_077(
        'openssl pkey -in '
        .. shell_quote(auth_privkey_path)
        .. ' -pubout -out '
        .. shell_quote(auth_pubkey_path)
    )
    if not output then
        remove_paths(auth_privkey_path, auth_pubkey_path)
        return nil
    end
    return true
end

local function ensure_wg_keys()
    if not install_one_of('wireguard-tools wg-installer-server', 'wg') then return nil end
    if is_readable(wg_privkey_path) and is_readable(wg_pubkey_path) then
        log_debug("wg_privkey and wg_pubkey both already exist")
        return true
    end
    log_info("generating new WireGuard keys")
    if not is_directory('/etc/wireguard/') then
        mkdir('/etc/wireguard/', '0700')
    end
    remove_path(wg_pubkey_path)
    local output = run_command_with_umask_077(
        'wg genkey >' .. shell_quote(wg_privkey_path)
        .. ' && wg pubkey <' .. shell_quote(wg_privkey_path)
        .. ' >' .. shell_quote(wg_pubkey_path)
    )
    if not output then
        remove_paths(wg_privkey_path, wg_pubkey_path)
        return nil
    end
    return true
end

local function next_retry_state(retry_wait, retries_left, operation, max_wait)
    retries_left = retries_left - 1
    if retries_left > 0 then return retry_wait, retries_left end
    retry_wait = math.min(retry_wait * 2, max_wait or 3600)
    log_info(
        "increased " .. operation .. " retry wait to " .. tostring(retry_wait) .. " seconds"
    )
    return retry_wait, 2
end

local function mark_adopted()
    if not write_text_file(pubkeys_uploaded_path, 'cleanup_pending\n', '0600') then
        log_error("B04717 adopt6c succeeded but could not mark cleanup pending")
    end
    return true
end

local function upload_auth_key(token)
    local auth_pubkey = read_text_file(auth_pubkey_path, false)
    if not auth_pubkey then return nil end  -- log_error() already called from read_text_file()
    local request_path = make_temp_path()
    local response_path = make_temp_path()
    if not request_path or not response_path then  -- log_error() already called
        remove_paths(request_path, response_path)
        return nil
    end
    local request_body = json.encode({
        jsonrpc = '2.0',
        id = 1,
        method = 'adopt6c',
        params = {subd = subd, token = token, auth_pubkey = auth_pubkey},
    })
    local write_ok = write_text_file(request_path, request_body, '0600')
    if not write_ok then  -- log_error() already called from write_text_file()
        remove_paths(request_path, response_path)
        return nil
    end
    local curl_command = 'curl -sS --connect-timeout 20 --max-time 90 '
            .. '-X POST '
            .. shell_quote(api_url)
            .. ' -H '
            .. shell_quote('Content-Type: application/json')
            .. ' --data-binary @'
            .. shell_quote(request_path)
    local curl_output = download_file(curl_command, response_path)
    local response_body = curl_output and read_text_file(response_path, true) or nil
    remove_paths(request_path, response_path)
    if curl_output and response_body then
        log_debug("adopt6c response body: " .. displayable(response_body, 60))
        local result = jsonrpc_result(response_body)
        if result and result.status == 'ok' and result.subd == subd then
            log_info("public key upload succeeded")
            return true
        end
        log_error("B23806 adopt6c failed: " .. displayable(response_body, 60))
    else
        log_warning("B49863 adopt6c attempt failed without a usable response; will retry")
    end
    return false
end

local function do_adopt6c(ping)
    -- return true on successful API call (bootstrap cleanup is needed)
    -- return false iff API call does not need to be done
    -- return nil on permanent failure
    -- retry forever on communication failure
    local auth_mtime = file_mtime(auth_privkey_path)
    local uploaded_mtime = file_mtime(pubkeys_uploaded_path)
    if auth_mtime > 0 and uploaded_mtime >= auth_mtime then
        -- above, use '>=' and not '>' to avoid race condition and disabled client
        log_info("public key already marked as uploaded")
        local state = read_text_file(pubkeys_uploaded_path, false)
        if state == 'uploaded' then return false end
        if state == 'cleanup_pending' then return true end
    end
    local retry_wait, retries_left = 7, 2
    local warned_missing = false
    log_info("public keys need upload to " .. api_url)
    while true do
        -- strip '\n' from middle if 2 'echo ... >>$T' lines in get_adopt5c_code()
        local token = read_text_file(ott_path, true):gsub('%s+', '')
        if token ~= '' then  -- let hub enforce expired OTT; router clock can be off by years
            local uploaded = upload_auth_key(token)
            if uploaded then return mark_adopted() end
            if uploaded == nil then return nil end
            warned_missing = false
        elseif not warned_missing then
            log_warning("B16500 cannot read " .. ott_path .. "; trying ping and watching for a token")
            warned_missing = true
        end
        if ping() then return true end
        sleep_with_jitter(retry_wait, 0.5)
        retry_wait, retries_left = next_retry_state(retry_wait, retries_left, 'adopt6c', 300)
    end
    log_error("B49403 should never get here")
end

local function safe_utf8(text)
    return (text:gsub('[\128-\255][\128-\191]*', function(bytes)
        local first, second = bytes:byte(1, 2)
        local length = first >= 194 and first <= 223 and 2
                or first >= 224 and first <= 239 and 3
                or first >= 240 and first <= 244 and 4 or 0
        if #bytes ~= length or (first == 224 and second < 160)
                or (first == 237 and second >= 160) or (first == 240 and second < 144)
                or (first == 244 and second >= 144) then
            return '\239\191\189'
        end
        return bytes
    end))
end

local function collect_telemetry()
    local function read_text_file_top(path)
        return safe_utf8(read_text_file(path, true, false, 32768))
    end
    return {
        proc_uptime = read_text_file_top('/proc/uptime'),
        proc_loadavg = read_text_file_top('/proc/loadavg'),
        proc_meminfo = read_text_file_top('/proc/meminfo'),
        proc_net_dev = read_text_file_top('/proc/net/dev'),
        proc_net_route = read_text_file_top('/proc/net/route'),
        -- maybe add: df or /proc/mounts + stat
        -- maybe add: ip addr, /proc/net/fib_trie
        -- maybe add: wg
        etc_os_release = read_text_file_top('/etc/os-release'),
        platform = platform,
        -- don't need file_xver every time, but it's low-cost and needed if hub or we restart
        file_xver = file_xver,
        telemetry_version = 1,
    }
end

local function build_ping_request()
    -- return the request body, or nil on failure
    log_debug("building ping request for subd " .. subd)
    local utc_time = run_command("date -u '+%Y-%m-%dT%H:%M:%SZ'", true)
    local request_id = run_command('openssl rand -hex 16', true)
    if not utc_time or not request_id then
        log_debug('cannot build ping request because one or more inputs were unavailable')
        return nil
    end
    local request_body = json.encode({
        jsonrpc = '2.0',
        id = 1,
        method = 'ping',
        params = {
            subd = subd,
            time = utc_time,
            telemetry = collect_telemetry(),
            request_id = request_id,
        },
    })
    log_debug("built ping request body (" .. tostring(#request_body) .. " bytes)")
    return request_body
end

local function send_signed_jsonrpc(request_body)
    -- return response body, or nil on failure
    local result = nil
    local signature_base = nil
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
            '{ '
            .. 'openssl dgst -sha256 -binary -out ' .. shell_quote(sig_bin_path) .. ' '
            .. shell_quote(body_path)
            .. ' && '
            .. 'openssl base64 -A -in ' .. shell_quote(sig_bin_path)
            .. '; }',
            true
        )
        if not content_digest_value then break end
        local content_digest_header = 'sha-256=:' .. content_digest_value .. ':'
        local timestamp_pair = run_command("LC_ALL=C date -u '+%s|%a, %d %b %Y %H:%M:%S GMT'")
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
        local signature_params = '("@method" "@authority" "@target-uri" "content-type" '
                .. '"content-digest" "date");created='
                .. created_value
                .. ';keyid="'
                .. keyid_value
                .. '";nonce="'
                .. nonce_param_value
                .. '";alg="rsa-pss-sha512"'
        local signature_input_value = 'sig1=' .. signature_params
        signature_base = '"@method": POST\n'
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
            'openssl dgst -sha512 '
            .. '-sigopt rsa_padding_mode:pss '
            .. '-sigopt rsa_mgf1_md:sha512 '
            .. '-sigopt rsa_pss_saltlen:64 '
            .. '-sign ' .. shell_quote(auth_privkey_path)
            .. ' -binary -out ' .. shell_quote(sig_bin_path)
            .. ' ' .. shell_quote(sig_base_path),
            true
        )
        if not sign_output then
            log_error("B41234 OpenSSL could not sign request with rsa-pss-sha512")
            break
        end
        -- verify:
        -- openssl dgst -sha512 -sigopt rsa_padding_mode:pss -sigopt rsa_mgf1_md:sha512 \
        --     -sigopt rsa_pss_saltlen:64 -verify <tmp-dir>/client_rsapss_pub.pem \
        --     -signature <tmp-dir>/api_data.sig <tmp-dir>/api_data
        local signature_b64 = run_command(
            'openssl base64 -A -in ' .. shell_quote(sig_bin_path),
            true
        )
        if not signature_b64 then break end
        log_debug("sending signed JSON-RPC request to " .. api_url)
        -- hub max timeout is 90 seconds; see clamp_wait_seconds() and others
        local curl_command = 'curl -sS --connect-timeout 20 --max-time 100 '
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
        local curl_output = download_file(curl_command, response_path)
        if not curl_output then break end
        local response_body = read_text_file(response_path, true)
        if not response_body then break end
        log_debug("ping response body: " .. displayable(response_body, 300))
        -- enable to debug signature issues (search: tag_rfc9421_signature_debug):
        -- if response_body:match('"error"%s*:') ~= nil then
        --     log_error("B05156 signature_base='" .. displayable(signature_base, 900) .. "'")
        -- end
        result = response_body
    until true
    remove_paths(body_path, sig_base_path, sig_bin_path, response_path)
    return result
end

local function send_task_result(task_id, task_method, ok, output)
    -- return true iff task_result was accepted by server
    output = tostring(output or '')
    if #output > 18000 then
        output = output:sub(1, 18000) .. '\n...truncated...'
    end
    if task_method == 'rce' then
        -- binary output or a truncated UTF-8 character must not invalidate JSON
        output = safe_utf8(output)
    end
    local request_body = json.encode({
        jsonrpc = '2.0',
        id = 1,
        method = 'task_result',
        params = {
            subd = subd,
            task_id = tostring(task_id),
            task_method = tostring(task_method),
            ok = not not ok,
            output = output,
        },
    })
    local response_body = send_signed_jsonrpc(request_body)
    if not response_body then
        log_warning("task_result failed without a usable response")
        return nil
    end
    log_debug("task_result response body: " .. displayable(response_body, 300))
    local result = jsonrpc_result(response_body)
    if result and result.status == 'ok' then
        log_info("task_result accepted for " .. task_method .. " task " .. task_id)
        return true
    end
    log_error("B83275 task_result rejected: " .. displayable(response_body, 300))
    return nil
end

local function send_pending_task_result()
    local task_id = base_config.pending_task_id
    if not task_id then return true end
    local target = base_config.pending_update_target
    if task_id == '' or not valid_xver(target) then
        log_warning("B81916 invalid pending task result; clearing it")
        return clear_pending_task_result()
    end
    local installed = target == file_xver
    if not send_task_result(task_id, 'update', installed,
                installed and 'ok' or 'update interrupted before activation') then
        return nil
    end
    return clear_pending_task_result()
end

--
-- configure WireGuard
--

local enable_wg, request_wg_config, restore_wg

do
    local wg_status = nil
    local function integer(value, minimum, maximum)
        if type(value) ~= 'number' and type(value) ~= 'string' then return nil end
        if not tostring(value):match('^%d+$') then return nil end
        local number = tonumber(value)
        if number and number >= minimum and number <= maximum then return number end
        return nil
    end
    local function public_key(value)
        return type(value) == 'string' and #value == 44
                and value:match('^[A-Za-z0-9+/]+=$')
                and value:sub(43, 43):match('^[AEIMQUYcgkosw048]$')
                and value ~= string.rep('A', 43) .. '='
    end
    local function ip_words(value)
        if type(value) ~= 'string' or #value > 45 then return nil end
        if not value:find(':', 1, true) then
            local a, b, c, d = value:match('^(%d+)%.(%d+)%.(%d+)%.(%d+)$')
            if not a then return nil end
            local words = {a, b, c, d}
            for index, word in ipairs(words) do
                if #word > 3 or (#word > 1 and word:sub(1, 1) == '0') then return nil end
                words[index] = tonumber(word)
                if words[index] > 255 then return nil end
            end
            return 4, words
        end
        local left, right = value:match('^(.-)::(.-)$')
        local compressed = left ~= nil
        if not compressed then left, right = value, '' end
        local words, tail = {}, {}
        for index, part in ipairs({left, right}) do
            local target = index == 1 and words or tail
            if part ~= '' then
                for word in (part .. ':'):gmatch('(.-):') do
                    if #word < 1 or #word > 4 or not word:match('^%x+$') then return nil end
                    target[#target + 1] = tonumber(word, 16)
                end
            end
        end
        if compressed then
            if #words + #tail >= 8 then return nil end
            while #words + #tail < 8 do words[#words + 1] = 0 end
        elseif #words ~= 8 then
            return nil
        end
        for _, word in ipairs(tail) do words[#words + 1] = word end
        return 6, words
    end
    local function cidr(value, route)
        if type(value) ~= 'string' then return nil end
        local address, prefix = value:match('^([^/]+)/(%d+)$')
        local family, words = ip_words(address)
        if not family then return nil end
        local bits = family == 4 and 32 or 128
        prefix = integer(prefix, 1, bits)
        if not prefix then return nil end
        local width, formatted = family == 4 and 8 or 16, {}
        for index, word in ipairs(words) do
            local host_bits = math.min(width, math.max(0, bits - prefix - width * (#words - index)))
            if route and word % 2 ^ host_bits ~= 0 then return nil end
            formatted[index] = string.format(family == 4 and '%d' or '%x', word)
        end
        return {
            family = family,
            cidr = table.concat(formatted, family == 4 and '.' or ':') .. '/' .. tostring(prefix),
        }
    end
    local function cidrs(value, route)
        if type(value) ~= 'string' or #value > 8192 then return nil end
        local entries = {}
        for part in (value .. ','):gmatch('(.-),') do
            local entry = cidr(trim(part), route)
            if not entry or #entries >= 128 then return nil end
            entries[#entries + 1] = entry
        end
        return entries
    end
    local function endpoint(value)
        if type(value) ~= 'string' then return nil end
        local address, port = value:match('^%[([^%]]+)%]:(%d+)$')
        local wanted_family = 6
        if not address then
            address, port = value:match('^([^:]+):(%d+)$')
            wanted_family = 4
        end
        -- get_conf() supplies a public IP; numeric endpoints avoid DNS blocking HTTPS work
        return ip_words(address) == wanted_family and #port <= 5 and integer(port, 1, 65535)
    end
    local function validate_conf(text)
        local conf = decode_json(text, 262144, 12)
        if not json_container(conf, 'object') or not json_container(conf.Interface, 'object')
                or not json_container(conf.Peers, 'array') then
            return nil, "B37080 invalid WireGuard configuration object"
        end
        local intf = conf.Interface
        -- reject malformed names now, before they cause hard-to-debug issues
        if type(intf.Name) ~= 'string' or #intf.Name > 15 or intf.Name == '.'
                or intf.Name == '..' or not intf.Name:match('^[%w_=+.%-]+$') then
            return nil, "B27458 invalid WireGuard interface name: " .. tostring(intf.Name)
        end
        if intf.PrivateKey ~= nil and intf.PrivateKey ~= json.null then
            return nil, "B27754 WireGuard private keys must remain local"
        end
        local fields = {
            Name = true,
            PrivateKey = true,
            Address = true,
            ListenPort = true,
            MTU = true,
            FwMark = true,
            Table = true,
            DNS = true,
        }
        for name in pairs(intf) do
            if not fields[name] then return nil, "B39148 unsupported field " .. name end
        end
        local addresses = cidrs(intf.Address, false)
        local mtu = integer(intf.MTU == nil and 1420 or intf.MTU, 1280, 9000)
        local mark = integer(intf.FwMark == nil and 0 or intf.FwMark, 0, 4294967295)
        if not addresses or not mtu or not mark
                or (intf.ListenPort ~= nil and not integer(intf.ListenPort, 0, 65535)) then
            return nil, "B42267 invalid WireGuard interface settings"
        end
        local ipv4, ipv6 = false, false
        for _, address in ipairs(addresses) do
            if address.family == 4 then ipv4 = true else ipv6 = true end
        end
        if not ipv4 then return nil, "B83484 WireGuard requires an IPv4 interface address" end
        if #conf.Peers < 1 or #conf.Peers > 128 then
            return nil, "B93680 invalid WireGuard peer count"
        end
        local routes, keys, ipv4_keys = {}, {}, {}
        local peer_fields = {
            PublicKey = true,
            AllowedIPs = true,
            Endpoint = true,
            PersistentKeepalive = true
        }
        for _, peer in ipairs(conf.Peers) do
            if not json_container(peer, 'object') or not public_key(peer.PublicKey)
                    or keys[peer.PublicKey] then
                return nil, "B97715 invalid or duplicate WireGuard peer"
            end
            for name in pairs(peer) do
                if not peer_fields[name] then return nil, "B43677 unsupported WireGuard peer field" end
            end
            keys[peer.PublicKey] = true
            local allowed = cidrs(peer.AllowedIPs, true)
            local keepalive = peer.PersistentKeepalive
            if keepalive == nil then keepalive = 0 end
            if not allowed or not integer(keepalive, 0, 65535)
                    or (peer.Endpoint ~= nil and not endpoint(peer.Endpoint)) then
                return nil, "B46583 invalid peer settings (default routes are unsupported)"
            end
            local peer_ips, peer_ipv4 = {}, {}
            for _, entry in ipairs(allowed) do
                if routes[entry.cidr] then return nil, "B45639 duplicate WireGuard route" end
                routes[entry.cidr] = entry
                peer_ips[#peer_ips + 1] = entry.cidr
                if entry.family == 4 then
                    peer_ipv4[#peer_ipv4 + 1] = entry.cidr
                else
                    ipv6 = true
                end
            end
            keys[peer.PublicKey] = table.concat(peer_ips, ',')
            ipv4_keys[peer.PublicKey] = table.concat(peer_ipv4, ',')
        end
        -- table and DNS fields are metadata here; no policy routing changes
        return conf, {
            addresses = addresses,
            routes = routes,
            keys = keys,
            ipv4_keys = ipv4_keys,
            ipv6 = ipv6,
            mtu = mtu,
            mark = mark
        }
    end
    enable_wg = function(text)
        if platform ~= 'init' and platform ~= 'sysd' then
            return nil, "B85937 WireGuard activation requires Linux"
        end
        local conf, settings = validate_conf(text)
        if not conf then return nil, settings end
        -- stable ordering avoids rewriting base.conf solely because table iteration order changed
        local saved_json = json.encode(conf, {
            keyorder = {
                'Interface', 'Peers', 'Name', 'PrivateKey', 'Address', 'ListenPort', 'MTU',
                'FwMark', 'Table', 'DNS', 'PublicKey', 'AllowedIPs', 'Endpoint', 'PersistentKeepalive',
            }
        })
        if base_config.wg_conf ~= saved_json or base_config.wg_enabled ~= '1'
                or base_config.wg_ipv6_policy ~= 'optional' then
            local updated = copy_table(base_config)
            updated.wg_conf, updated.wg_enabled = saved_json, '1'
            updated.wg_ipv6_policy = 'optional'
            -- persist desired state first, so an interrupted apply can resume after restart;
            -- keep IPv6 in the saved JSON; availability is checked again on every restore
            if not save_base_config(updated) then
                return nil, "B39841 cannot save WireGuard settings"
            end
            base_config = updated
        end
        -- restoration never installs packages or regenerates a missing private key
        if not run_command('command -v ip', true, true) then return nil, "B71149 'ip' not found" end
        if not run_command('command -v wg', true, true) then return nil, "B67793 'wg' not found" end
        if not is_readable(wg_privkey_path) then return nil, "B63659 unreadable wg private key" end
        local name = shell_quote(conf.Interface.Name)
        local function command(text)
            local output, problem, exit_code = run_command(text, true, true)
            if not output then
                if not problem or problem == '' then
                    problem = exit_code and "exit code " .. tostring(exit_code) or "no error output"
                end
                error("B78313 error '" .. displayable(problem, 512) .. "' running: "
                    .. displayable(text, 1024), 0)
            end
            return output
        end
        local wanted = {}
        for _, address in ipairs(settings.addresses) do wanted[address.cidr] = true end
        local ipv6_inspected = false
        local function configure_family(family, clear)
            local ip = 'ip -' .. tostring(family)
            -- inspect both before changing anything, including when testing IPv6 support in ip
            local addresses = command(ip .. ' -o address show dev ' .. name .. ' scope global')
            local routes = command(ip .. ' route show dev ' .. name .. ' proto static')
            if family == 6 then ipv6_inspected = true end
            local cleanup_problem
            local function remove(text)
                if not clear then
                    command(text); return
                end
                -- one failed cleanup must not prevent attempts to remove the other IPv6 state
                local ok, problem = pcall(command, text)
                if not ok then cleanup_problem = cleanup_problem or problem end
            end
            for address in addresses:gmatch('inet6?%s+(%S+)') do
                local existing = cidr(address, false)
                if existing and (clear or not wanted[existing.cidr]) then
                    remove(ip .. ' address del ' .. shell_quote(address) .. ' dev ' .. name)
                end
            end
            for line in routes:gmatch('[^\r\n]+') do
                local address = line:match('^(%S+)')
                if address and not address:find('/', 1, true) then
                    address = address .. (family == 4 and '/32' or '/128')
                end
                local existing = cidr(address, true)
                if existing and (clear or not settings.routes[existing.cidr]) then
                    remove(ip .. ' route del ' .. shell_quote(address) .. ' dev ' .. name
                        .. ' proto static')
                end
            end
            if clear then
                if cleanup_problem then error(cleanup_problem, 0) end
                return
            end
            for _, address in ipairs(settings.addresses) do
                if address.family == family then
                    command(ip .. ' address replace ' .. shell_quote(address.cidr) .. ' dev ' .. name)
                end
            end
            if family == 4 then
                command('ip link set dev ' .. name .. ' mtu ' .. tostring(settings.mtu) .. ' up')
            end
            for _, route in pairs(settings.routes) do
                if route.family == family then
                    command(ip .. ' route replace ' .. shell_quote(route.cidr) .. ' dev ' .. name
                        .. ' proto static')
                end
            end
        end
        local function peer_allowed_ips(peer, ipv6)
            local keys = ipv6 and settings.keys or settings.ipv4_keys
            return 'wg set ' .. name .. ' peer ' .. shell_quote(peer.PublicKey)
                    .. ' allowed-ips ' .. shell_quote(keys[peer.PublicKey])
        end
        local ok, problem = pcall(function()
            if not run_command('ip link show dev ' .. name, true, true) then
                command('ip link add dev ' .. name .. ' type wireguard')
                -- tell NetworkManager to ignore interface (hides 'VPN' icon in system status area)
                -- best effort only (ignore failures); requires that the device exist;
                -- no need to set up persistence in /etc/NetworkManager/conf.d/
                run_command('nmcli device set ' .. name .. ' managed no', true, true)
            end
            -- this also refuses an existing non-WireGuard interface with the same name
            local peers = command('wg show ' .. name .. ' peers')
            command('wg set ' .. name .. ' private-key ' .. shell_quote(wg_privkey_path)
                .. ' fwmark ' .. tostring(settings.mark))
            if conf.Interface.ListenPort ~= nil then
                local port = integer(conf.Interface.ListenPort, 0, 65535)
                command('wg set ' .. name .. ' listen-port ' .. tostring(port))
            end
            for key in peers:gmatch('%S+') do
                if not settings.keys[key] then
                    command('wg set ' .. name .. ' peer ' .. shell_quote(key) .. ' remove')
                end
            end
            for _, peer in ipairs(conf.Peers) do
                -- an empty IPv4 list clears AllowedIPs for an IPv6-only peer until IPv6 is ready
                local cmd = peer_allowed_ips(peer, false) .. ' persistent-keepalive '
                        .. tostring(integer(peer.PersistentKeepalive or 0, 0, 65535))
                if peer.Endpoint then cmd = cmd .. ' endpoint ' .. shell_quote(peer.Endpoint) end
                command(cmd)
            end
            configure_family(4)
        end)
        if not ok then
            wg_status = nil; return nil, problem
        end
        -- IPv4 is now configured and up; respect the interface's IPv6 setting, without changing
        -- global/default/LAN/WAN sysctls; the value of all.disable_ipv6 is not a capability test
        local disabled = read_text_file('/proc/sys/net/ipv6/conf/' .. conf.Interface.Name
            .. '/disable_ipv6', true)
        local ipv6_problem
        if disabled == '1' then
            ipv6_problem = "disabled on " .. conf.Interface.Name
        elseif disabled ~= '0' then
            ipv6_problem = "interface IPv6 support unavailable"
        else
            ok, problem = pcall(function()
                configure_family(6)
                for _, peer in ipairs(conf.Peers) do
                    if settings.keys[peer.PublicKey] ~= settings.ipv4_keys[peer.PublicKey] then
                        command(peer_allowed_ips(peer, true))
                    end
                end
            end)
            if not ok then
                ipv6_problem = tostring(problem)
                local cleanup_problem
                for _, peer in ipairs(conf.Peers) do
                    if settings.keys[peer.PublicKey] ~= settings.ipv4_keys[peer.PublicKey] then
                        local removed, err = pcall(command, peer_allowed_ips(peer, false))
                        if not removed then cleanup_problem = cleanup_problem or err end
                    end
                end
                -- a failed capability probe made no changes; do not try unsupported IPv6 cleanup
                if ipv6_inspected then
                    local removed, err = pcall(configure_family, 6, true)
                    if not removed then cleanup_problem = cleanup_problem or err end
                end
                if cleanup_problem then
                    ipv6_problem = ipv6_problem .. "; IPv6 cleanup incomplete: " .. cleanup_problem
                end
            end
        end
        local status = "B84526 on " .. conf.Interface.Name .. ", IPv4 configured"
        if ipv6_problem and (settings.ipv6 or ipv6_inspected) then
            status = status .. ", IPv6 skipped: " .. ipv6_problem
        elseif settings.ipv6 then
            status = status .. ", IPv6 configured"
        else
            status = status .. ", IPv6 not requested"
        end
        if status ~= wg_status then
            if ipv6_problem and (settings.ipv6 or ipv6_inspected) then
                log_warning(status)
            else
                log_info(status)
            end
        end
        wg_status = status
        return true, status
    end
    request_wg_config = function()
        if not ensure_wg_keys() or not install_one_of('ip-full iproute2 iproute', 'ip') then
            return nil, "B29081 cannot prepare WireGuard tools and keys"
        end
        local key = read_text_file(wg_pubkey_path, true)
        if not public_key(key) then return nil, "B78910 invalid local WireGuard public key" end
        local body = json.encode({
            jsonrpc = '2.0',
            id = 1,
            method = 'wg',
            params = {subd = subd, pubkey = key},
        })
        local result = jsonrpc_result(send_signed_jsonrpc(body), 262144, 12)
        if not result or result.status ~= 'ok' or result.subd ~= subd
                or not json_container(result.task_args, 'object') then
            return nil, "B57314 invalid wg API response"
        end
        return json.encode(result.task_args)
    end
    restore_wg = function()
        if base_config.wg_enabled ~= '1' then return end
        local call_ok, ok, problem = pcall(enable_wg, base_config.wg_conf)
        if not call_ok then problem, ok = ok, nil end
        if not ok then log_error(tostring(problem)) end
    end
end

-- -- keeping for possible future use; see commit 7f442c3
-- local function rewrite_service_runner(lua_path)
--     local result = nil
--     if platform == 'init' then
--         local init_path = '/etc/init.d/' .. bbsubd
--         result = install_service_wrapper_file(init_path, init_service_wrapper_text(lua_path), '0755')
--     else
--         local start_script_path = '/usr/local/sbin/' .. bbsubd .. '-start.sh'
--         local start_text = systemd_service_wrapper_texts(lua_path)
--         result = install_service_wrapper_file(start_script_path, start_text, '0755')
--     end
--     if result == nil then return nil end
--     return true
-- end

local function verify_file_signature(staged_path, signature, key_id)
    -- return true iff successful, string error message otherwise
    if type(key_id) ~= 'string' or key_id == '' then return "missing key_id" end
    if type(signature) ~= 'string' or signature == '' then return "missing signature" end
    -- trust only running program's keys because only their signature was verified with a prior pubkey
    local pubkey = nil
    for _, entry in ipairs(file_pubkeys) do
        if entry.key_id == key_id then
            pubkey = entry.pubkey
            break
        end
    end
    if not pubkey then return "missing pubkey for key_id " .. key_id end
    if #signature % 4 ~= 0 or signature:find('[^A-Za-z0-9+/=]') then return "signature not base64" end
    local pubkey_path = make_temp_path(nil, true)
    local signature_path = make_temp_path(nil, true)
    local raw_signature_path = make_temp_path(nil, true)
    if not pubkey_path or not signature_path or not raw_signature_path then
        remove_paths(pubkey_path, signature_path, raw_signature_path)
        return "cannot create temp files"
    end
    local verified = false
    local failure = nil
    repeat
        if not write_text_file(pubkey_path, pubkey .. '\n', '0600')
                or not write_text_file(signature_path, signature, '0600') then
            failure = "cannot write verification files"
            break
        end
        if not run_command('openssl base64 -d -A -in ' .. shell_quote(signature_path)
                    .. ' -out ' .. shell_quote(raw_signature_path), true, true) then
            failure = "could not decode the signature"
            break
        end
        local ok, output = run_command('openssl dgst -sha512 -verify ' .. shell_quote(pubkey_path)
            .. ' -signature ' .. shell_quote(raw_signature_path)
            .. ' -sigopt rsa_padding_mode:pss -sigopt rsa_pss_saltlen:64'
            .. ' -sigopt rsa_mgf1_md:sha512 ' .. shell_quote(staged_path), true, true)
        if not ok then
            failure = "'openssl -verify' failed"
            if output and output ~= '' then failure = failure .. ': ' .. displayable(output, 170) end
            break
        end
        verified = true
    until true
    remove_paths(pubkey_path, signature_path, raw_signature_path)
    if not verified then return failure end
    return true
end

local function rce(task_args)
    if not json_container(task_args, 'object') then
        return false, "B41362 missing signed document"
    end
    local sequence_id = task_args.sequence_id
    local previous = base_config.rce_sequence_id or file_xver:sub(1, 7)
    if type(sequence_id) ~= 'string' or #sequence_id ~= 7
            or not sequence_id:match('^[0-9a-z]+$')
            or #previous ~= 7 or not previous:match('^[0-9a-z]+$')
            or sequence_id <= previous then
        return false, "B27581 RCE sequence_id must be greater than " .. previous
                .. "; try again in a minute"
    end
    if task_args.subd ~= subd then
        return false, "B63384 RCE subd does not match this base"
    end
    local content, signature, key_id = task_args.content, task_args.signature, task_args.key_id
    if type(content) ~= 'string' or content:find('\0', 1, true)
            or type(signature) ~= 'string' or type(key_id) ~= 'string' then
        return false, "B61872 invalid signed document fields"
    end
    local signature_path = make_temp_path(nil, true)
    if not signature_path then return false, "B65835 cannot create RCE signature file" end
    local ok, output = false, nil
    repeat
        local signature_base = 'sabozt45m\n' .. subd .. '\n' .. sequence_id .. '\n' .. content
        if not write_text_file(signature_path, signature_base, '0600') then
            output = "B11483 cannot write RCE signature base"
            break
        end
        local verified = verify_file_signature(signature_path, signature, key_id)
        if verified ~= true then
            output = "B19163 bad RCE signature: " .. tostring(verified)
            break
        end
        if not launch_method then
            output = "B39107 no usable timed-command launcher"
            break
        end
        local updated_config = copy_table(base_config)
        updated_config.rce_sequence_id = sequence_id
        if not save_base_config(updated_config) then
            output = "B62484 cannot save RCE sequence_id"
            break
        end
        base_config = updated_config
        local captured, error_output, exit_code, timed_out = run_command_for(content, 60, true, true)
        ok = exit_code == 0 and not timed_out
        if timed_out then  -- put status first so it survives task-output truncation at the hub boundary
            output = "RCE timed out after 60 seconds (exit status "
                    .. tostring(exit_code or 'unknown') .. ")"
        elseif exit_code ~= nil then
            output = "RCE exited with status " .. tostring(exit_code)
        else
            output = "RCE failed; exit status unavailable"
        end
        local detail = captured or error_output
        if detail and detail ~= '' then output = output .. '\n' .. detail end
    until true
    remove_path(signature_path)
    return ok, output
end

local update, cleanup_based_dirs
do
    local placeholder = 'y5Tf2ghvd2jTi9m21geAP'
    local max_bytes = 654321  -- search tag_base_max_dl_bytes
    local cleaned = false
    local download_root = (api_url:gsub('/api/v1$', ''))
    local function tree_paths(tree)
        local paths = {}
        for _, entry in ipairs(tree.paths) do paths[entry.path] = entry end
        return paths
    end
    local function file_hash(path)
        local quoted = shell_quote(path)
        local output = run_command('sha256sum ' .. quoted, false, true)
        if output then return output:match('^%x+') end
        output = run_command('openssl dgst -sha256 ' .. quoted, false, true)
        return output and output:match('%x+$')
    end
    local function fetch(path, version, destination)
        remove_path(destination)
        local url = download_root .. '/' .. version .. '/' .. path
        return download_file('curl -f --max-time 120 ' .. shell_quote(url), destination)
    end
    local function verify_tree(body)
        -- keep this prefix in sync with sign_code() in devtool.py
        local first, signature, after, key_id = body:sub(1, 1024):match(
            '^{\n  "signature": "()([A-Za-z0-9+/]+=?=?)()",\n'
            .. '  "key_id": "([0-9a-z]+)",\n'
        )
        if not first or #key_id ~= 7 then return "invalid tree signature prefix" end
        local path = make_temp_path(nil, true)
        if not path then return "cannot create tree verification file" end
        local unsigned = body:sub(1, first - 1) .. placeholder .. body:sub(after)
        local result = write_text_file(path, unsigned, '0600')
                and verify_file_signature(path, signature, key_id)
        remove_path(path)
        return result or "cannot write tree verification file"
    end
    local function publish_link(path, target)
        local temporary = dirname(path) .. '/' .. bbsubd .. '.base_new.lua'
        remove_path(temporary)
        if not run_command('ln -s ' .. shell_quote(target) .. ' ' .. shell_quote(temporary),
                    true, true) then
            return nil
        end
        local ok = os.rename(temporary, path)
        if not ok then remove_path(temporary) end
        return ok
    end
    update = function(task_id, target_xver)
        if not valid_xver(target_xver) then return false, "B80430 invalid target_xver" end
        if target_xver == file_xver then return true, 'ok' end
        local target_cver = target_xver:sub(1, 7)
        if file_xver and target_cver < file_xver:sub(1, 7) then
            return false, "B80431 update would downgrade"
        end
        local new_based_dir = install_dir .. '/' .. target_xver .. '/'
        local entry_point = install_dir .. '/base.lua'
        local entry_point_link = run_command('readlink -f ' .. shell_quote(entry_point), true, true)
        local already_active = not task_id and entry_point_link == new_based_dir .. 'base.lua'
        -- keep unverified downloads out of persistent release storage
        local tree_path = make_temp_path(nil, true)
        if not tree_path then return false, "B80442 cannot create tree staging file" end
        local body
        for _ = 1, 3 do
            if fetch('base/tree.json', target_cver, tree_path) then
                body = read_text_file(tree_path, false, true, max_bytes)
                if body and body ~= '' then break end
            end
            body = nil
        end
        remove_path(tree_path)
        if not body then return false, "B80434 tree download failed after three attempts" end
        -- authenticate the manifest before parsing it
        local verified = verify_tree(body)
        if verified ~= true then
            return false, "B80436 bad tree signature: " .. verified
        end
        local tree = decode_json(body)
        if not json_container(tree, 'object') or not json_container(tree.paths, 'array') then
            return false, "B80435 invalid 'tree.json' format"
        end
        local paths = tree_paths(tree)
        if not paths['base.lua']
                or not paths['base.lua'].sha256sum
                or not paths['tree.json']
                or paths['tree.json'].cver ~= target_cver then
            return false, "B80441 tree does not describe base release " .. target_cver
        end
        if not mkdir(install_dir, '0700') or not mkdir(new_based_dir, '0700') then
            return false, "B80432 cannot create release directory"
        end
        local manifest = new_based_dir .. 'tree.json'
        if read_text_file(manifest, true, true, max_bytes) ~= body
                and not write_text_file(manifest, body, '0644') then
            return false, "B80443 cannot save release tree"
        end
        local old_paths = {}
        -- a damaged local cache should only prevent reusing old files
        if task_id then
            local old_body = read_text_file(running_dir .. '/tree.json', true, true, max_bytes)
            local ok, cached = pcall(tree_paths, decode_json(old_body, max_bytes))
            if ok then old_paths = cached end
        end
        for _, entry in ipairs(tree.paths) do
            local path = entry.path
            if entry.sha256sum then  -- only hashed entries are installed
                local name = basename(path)
                local installed = new_based_dir .. name
                local destination = make_temp_path(new_based_dir, true)
                if not destination then return false, "B84820 cannot stage release file: " .. path end
                local source = running_dir .. '/' .. name
                if already_active and is_readable(installed) then source = installed end
                local old = old_paths[path]
                local reuse = not task_id
                        or (type(old) == 'table' and old.sha256sum == entry.sha256sum)
                local ready = reuse and is_readable(source) and file_copy(source, destination, '0644')
                        and file_hash(destination) == entry.sha256sum
                if not ready then
                    for _ = 1, 3 do
                        if fetch('base/' .. path, entry.cver or target_cver, destination)
                                and file_hash(destination) == entry.sha256sum then
                            ready = true
                            break
                        end
                    end
                    if not ready then
                        remove_path(destination)
                        return false, "B80437 download or hash check failed: " .. path
                    end
                    if not chmod(destination, '0644') then
                        remove_path(destination)
                        return false, "B80439 chmod failed"
                    end
                end
                if path:sub(-4) == '.lua' then
                    local command = 'STAGED_PATH=' .. shell_quote(destination) .. ' '
                            .. shell_quote(lua_exe) .. ' -e '
                            .. shell_quote('assert(loadfile(os.getenv("STAGED_PATH")))')
                    if (path == 'base.lua'
                                and #read_text_file(destination, true, true, max_bytes) <= 3)
                            or not run_command(command, true) then
                        remove_path(destination)
                        return false, "B80438 invalid Lua file: " .. path
                    end
                end
                if not os.rename(destination, installed) then
                    remove_path(destination)
                    return false, "B80445 cannot replace release file: " .. path
                end
            end
        end
        if already_active then return true, 'ok' end
        if task_id then
            local updated_config = copy_table(base_config)
            updated_config.pending_task_id = task_id
            updated_config.pending_update_target = target_xver
            if not save_base_config(updated_config) then
                return false, "B33348 cannot save update state"
            end
            base_config = updated_config
        end
        if not publish_link(entry_point, new_based_dir .. 'base.lua') then
            if task_id then clear_pending_task_result() end
            return false, "B57225 cannot activate release symlink"
        end
        if not task_id then return true, 'ok' end
        log_info("update installed; restarting")
        cleanup_and_exit(0)
    end
    cleanup_based_dirs = function()
        if cleaned then return end
        local output = run_command('for path in ' .. shell_quote(install_dir) .. '/*; do '
            .. 'if [ -d "$path" ] && [ ! -L "$path" ]; then '
            .. 'printf "%s\\n" "${path##*/}"; fi; done', false, true)
        if not output then return end
        cleaned = true
        for name in output:gmatch('[^\r\n]+') do
            if valid_xver(name) and name < file_xver then
                local path = shell_quote(install_dir .. '/' .. name)
                if not run_command('rm -rf ' .. path, true, true) then
                    cleaned = false
                end
            end
        end
    end
end


local function handle_task(task_id, task_method, task_args)
    -- return true iff task was handled or no task was present -and- output
    -- accept task arguments as either JSON strings or already decoded objects
    if type(task_args) == 'string' then task_args = decode_json(task_args) or task_args end
    log_info("received task " .. task_method .. " id=" .. task_id)
    if task_method == 'no_op' then
        return true, 'ok'
    end
    if task_method == 'enable_wg' then
        local wg_conf, problem = request_wg_config()
        if not wg_conf then return false, problem end
        return enable_wg(wg_conf)
    end
    if task_method == 'rce' then
        return rce(task_args)
    end
    if task_method == 'update' then
        local target_xver = json_container(task_args, 'object') and task_args.target_xver
        return update(task_id, target_xver)
    end
    if task_method == 'discover_upnp' then
        if not discover_upnp then
            -- load from the release directory resolved at startup
            local load_upnp = assert(loadfile(running_dir .. '/upnp.lua'))
            local discover = load_upnp({
                display_text_file = display_text_file,
                displayable = displayable,
                download_file = download_file,
                install_one_of = install_one_of,
                json = json,
                log_debug = log_debug,
                log_info = log_info,
                log_warning = log_warning,
                make_temp_path = make_temp_path,
                normalize_pid = normalize_pid,
                read_text_file = read_text_file,
                remove_path = remove_path,
                remove_paths = remove_paths,
                run_command = run_command,
                run_command_for = run_command_for,
                shell_quote = shell_quote,
                sleep = sleep,
                trim = trim,
                write_text_file = write_text_file,
            })
            assert(type(discover) == 'function', "upnp.lua did not return a discovery function")
            discover_upnp = discover
        end
        local pcap_base64 = json_container(task_args, 'object') and task_args.pcap or nil
        if type(pcap_base64) ~= 'string' then pcap_base64 = nil end
        return discover_upnp(pcap_base64)
    end
    if task_method == 'df' then
        local df_args = json_container(task_args, 'object') and task_args.args or task_args
        if df_args ~= '-hT' then
            return false, 'unsupported df args'
        end
        local output, problem, code, timed_out = run_command_for('df ' .. df_args, 30, true, true)
        if not output then
            local detail = timed_out and "timed out" or problem ~= '' and problem or tostring(code)
            return false, "df failed: " .. detail
        end
        return true, output
    end
    return false, 'unsupported task_method'
end

local function flush_pending_task_result()
    local completed, pending_ready = pcall(send_pending_task_result)
    if not completed then
        log_error("B91867 cannot send pending task result: "
            .. displayable(tostring(pending_ready), 500))
    elseif not pending_ready then
        log_error("B91868 cannot send pending task result")
    end
    return completed and pending_ready
end

local function do_ping(on_authenticated)
    -- return the status response, or nil on failure
    -- report an earlier update before accepting another task, including during adoption recovery
    local pending_id = base_config.pending_task_id
    local pending_ready = flush_pending_task_result()
    log_debug("starting ping cycle")
    local request_body = build_ping_request()
    if not request_body then return nil end
    local response_body = send_signed_jsonrpc(request_body)
    if not response_body then return nil end
    log_debug("ping response body: " .. displayable(response_body, 300))
    local result = jsonrpc_result(response_body)
    if result and result.status == 'ok' then
        if on_authenticated then on_authenticated() end
        cleanup_based_dirs()
        log_info("ping succeeded with status " .. displayable(result.status, 60))
        local id, method = result.task_id, result.task_method
        if type(id) == 'string' and type(method) == 'string' then
            -- a stale pending task may become reportable only after the hub assigns it again
            if not pending_ready then pending_ready = flush_pending_task_result() end
            if pending_id and id == pending_id and method == 'update' then
                return pending_ready and result.status or nil
            end
            if not pending_ready and method == 'update' then
                -- keep pinging, but never overwrite the previous update's unreported result
                local message = "B42117 cannot run another update while an update result is pending"
                if not send_task_result(id, method, false, message) then return nil end
                return result.status
            end
            local ran, ok, output = pcall(handle_task, id, method, result.task_args)
            if not ran then
                output = "B52938 task " .. displayable(method, 80) .. " raised: "
                        .. displayable(tostring(ok), 500)
                log_error(output)
                ok = false
            end
            if not send_task_result(id, method, ok, output) then return nil end
        end
        return result.status
    end
    -- note the Berror code below is used in api.py
    log_error("B64445 ping rejected: " .. displayable(response_body, 300))
    return nil
end

local function poll_safely(operation)
    local completed, result = pcall(operation)
    if completed then return result end
    log_error("B62491 polling error: " .. displayable(tostring(result), 500))
    return nil
end

local function finish_adoption_cleanup()
    if not run_command('umask 077 && flock -n -x /etc/rc.local.bitburrow.lock '
                .. shell_quote(lua_exe) .. ' ' .. shell_quote(running_path) .. ' delete-adopt5c-code',
                true, true) then
        log_warning("B76324 rc.local cleanup busy or failed; will retry")
        return nil
    end
    local removed, err, errno = os.remove(ott_path)
    if not removed and errno ~= 2 then
        log_error("B83756 cannot remove " .. ott_path .. " (" .. tostring(err) .. ")")
        return nil
    end
    return write_text_file(pubkeys_uploaded_path, 'uploaded\n', '0600')
end

--
-- install or reinstall as a service and exit
--

if get_uid() ~= 0 then
    log_error("B97106 must run as root")
    close_log()
    os.exit(2)
end
if cli_verb == 'delete-adopt5c-code' then  -- internal; invoked under the rc.local lock by flock
    local result = delete_adopt5c_code('/etc/rc.local')
    close_log()
    os.exit(result == nil and 1 or 0)
end
if cli_verb == 'install' then
    if not set_sleep_method() then
        close_log()
        os.exit(13)
    end
    if not install_one_of('flock util-linux', 'flock')
            or not install_one_of('curl', 'curl')
            or not install_one_of('openssl openssl-util', 'openssl') then
        close_log()
        os.exit(4)
    end
    local parent = find_install_dir()
    local installed, problem
    if parent and mkdir(bbsubd_tmp_dir, '0700') then
        install_dir = parent .. subd
        installed, problem = update(nil, hub_config('target_xver'))
        if installed then installed = install_daemon_service(install_dir .. '/base.lua') end
    end
    if installed then
        log_info("B32020 successfully installed; exiting")
    else
        log_error("B21488 cannot install as a service: " .. tostring(problem or 'wrapper failed'))
    end
    close_log()
    os.exit(installed and 0 or 4)
end
if cli_verb ~= 'daemonize' then
    log_error("B38333 cli_verb == " .. cli_verb)
    os.exit(1)
end

--
-- make sure prerequisites are installed
--

log_warning("B20392 completed adopt5w, log level " .. logging_level)
if not set_sleep_method() then cleanup_and_exit(13) end
if not set_launch_method() then
    log_warning("B69877 set_launch_method() failed; df, UPnP, etc. are broken")
end
if not install_one_of('curl', 'curl') or not install_one_of('openssl openssl-util', 'openssl') then
    cleanup_and_exit(5)
end

--
-- collect authentication details
--

mkdir(config_dir, '0700')
restore_wg()  -- use saved settings before any hub API call
if not ensure_auth_keys() then
    log_error("B60585 cannot continue without key files; exiting")
    cleanup_and_exit(5)
end

--
-- register with hub
--

local adopt6c_result = do_adopt6c(function()
    local accepted = false
    poll_safely(function()
        do_ping(function() accepted = mark_adopted() end)
    end)
    return accepted
end)
if adopt6c_result == nil then
    log_error("B36017 could not confirm adoption; keeping token and bootstrap for retry")
    cleanup_and_exit(6)
end
local cleanup_due = adopt6c_result and 0

--
-- loop forever
--

local retry_wait = 7
local retries_left = 2
log_info("entering main ping loop")
while true do
    if cleanup_due then
        local now = uptime_seconds()
        if now and now >= cleanup_due then
            if poll_safely(finish_adoption_cleanup) then
                cleanup_due = nil
            else
                cleanup_due = now + 300
            end
        end
    end
    local ok = poll_safely(do_ping)
    if ok then
        retry_wait = 7
        retries_left = 2
        log_debug("ping loop reset retry state after success")
        sleep_with_jitter(2, 0.2)
    else
        log_info(
            "ping failed; sleeping before retry with retry_wait="
            .. tostring(retry_wait)
            .. ", retries_left="
            .. tostring(retries_left)
        )
        sleep_with_jitter(retry_wait, 0.5)
        retry_wait, retries_left = next_retry_state(retry_wait, retries_left, 'ping')
    end
end
