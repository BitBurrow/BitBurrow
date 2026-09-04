#!/usr/bin/lua

-- BitBurrow base daemon
-- note: strings use single quotes unless they are user-visible English, e.g. logging

--
-- for backwards compatibility during update to 0tkuo3w; future versions will use HUBCONF
--

local api_url = '{api_url}'
local download_url = '{download_url}'
local log_err_route = '{log_err_route}'
local ott_filename = '{ott_filename}'
local subd = '{subd}'

--
-- globals
--

local hubconf = os.getenv('HUBCONF') or table.concat({
    'api_url=' .. api_url,
    'download_url=' .. download_url,
    'log_err_route=' .. log_err_route,
    'ott_filename=' .. ott_filename,
    'subd=' .. subd,
}, '\n')

local function hub_config(key)
    for k, v in hubconf:gmatch('([^\r\n=]+)=([^\r\n]*)') do
        if k == key then return v end
    end
end

-- local api_url = hub_config('api_url')
-- local download_url = hub_config('download_url')
-- local log_err_route = hub_config('log_err_route')
-- local ott_filename = hub_config('ott_filename')
-- local subd = hub_config('subd')
local commit_date = '0tkux2d'
local bbsubd = 'bb' .. subd
local file_version = '{file_version}'
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

--
-- logging
--

local log_path = nil  -- to enable, use: log_path = bbsubd_tmp_dir .. 'log'
local log_handle = nil
local logging_level = 30  -- by default, show warnings, errors
logging_level = 20  -- for dev, use level info

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
    os.execute('chmod 0600 ' .. shell_quote(log_path))  -- chmod() is not yet defined
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
    if level >= 30 then  -- send errors and warnings to server
        -- run_command() and make_temp_path() are not defined yet
        local tmp_template = shell_quote(tmp_dir .. bbsubd .. '.log.XXXXXX')
        local tmp_pipe = io.popen('umask 077; mktemp ' .. tmp_template .. ' 2>/dev/null', 'r')
        if not tmp_pipe then return nil end
        local tmp_path = tmp_pipe:read('*l')
        tmp_pipe:close()
        if tmp_path == '' then tmp_path = nil end
        local f = tmp_path and io.open(tmp_path, 'w') or nil
        if f then
            local write_ok = f:write(message)
            local close_ok = f:close()
            if write_ok and close_ok ~= nil then
                os.execute(
                    'curl -f --max-time 10 -X POST'
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
        if not log_path or logging_level < 30 then  -- when using logread or -v
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

local function run_command(command, merge_stderr, failure_ok)
    -- return the captured output after stripping trailing whitespace, or nil on failure
    -- do not use shell pipelines here (`set -o pipefail` not universally supported)
    -- instead, use a command with file redirection and `&&` within { ... }
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
        in_dir = bbsubd_tmp_dir
    else
        in_dir = trim_trailing_slashes(in_dir) .. '/'
    end
    local cmd = 'mktemp ' .. shell_quote(in_dir .. bbsubd .. '.XXXXXX')
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

local function read_text_file(path, empty_if_unreadable, preserve_whitespace)
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
    local content, read_err = handle:read('*a')
    local close_ok, close_err = handle:close()
    if content == nil then
        log_error("B21409 cannot read " .. path .. " (" .. tostring(read_err) .. ")")
        return ret_error
    end
    if close_ok == nil then
        log_error("B55281 cannot close " .. path .. " (" .. tostring(close_err) .. ")")
        return ret_error
    end
    if not preserve_whitespace then
        content = content:gsub('%s+$', '')  -- strip trailing whitespace
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
        if not chmod(path, mode) then remove_path(path) return nil end
        log_debug("set permissions on " .. path .. " to " .. mode)
    end
    return true
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
        logging_level = 20  -- disable run_command() logging
        local started = uptime_seconds()
        local ok, problem = sleep_using_method(method, test_seconds)
        local finished = uptime_seconds()
        logging_level = saved_logging_level
        local elapsed = started and finished and (finished - started) or nil
        local m_text = "sleep method " .. tostring(method) .. " (" .. sleep_methods[method] .. ")"
        if ok and elapsed and elapsed >= minimum_elapsed then
            log_debug(m_text .. " succeeded: " .. string.format('%.2f', elapsed) .. " seconds")
            sleep_method = sleep_method or method  -- use first method that works on this device
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
        log_error(
            "B47295 " .. sleep_methods[sleep_method] .. " failed to sleep for "
                .. tostring(seconds) .. " seconds: " .. tostring(problem)
        )
        return nil
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

local function wait_until_dead(pid, attempts)
    -- note: pid must be normalized, e.g. normalize_pid()
    for _ = 1, attempts do
        if not is_running(pid) then return true end
        sleep(0.1)
    end
    return not is_running(pid)
end

local function kill_process(pid)
    -- note: pid must be normalized, e.g. normalize_pid()
    if not is_running(pid) then return true end
    run_command('kill -TERM ' .. pid, true, true)
    if wait_until_dead(pid, 20) then return true end
    run_command('kill -KILL ' .. pid, true, true)
    return wait_until_dead(pid, 20)
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
            .. tostring(lock_pid) ..") differ; not removing")
    end
    close_log()
    os.exit(exit_code)
end

--
-- JSON functions
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

local json_simple_escapes = {
    ['"'] = '"',
    ['\\'] = '\\',
    ['/'] = '/',
    b = '\b',
    f = '\f',
    n = '\n',
    r = '\r',
    t = '\t',
}

local function json_unescape(value)
    -- minimal JSON decoding, preserving the prior policy that non-ASCII \u escapes become '?'
    -- decode once from left to right so an escaped backslash cannot introduce a second escape
    local result = {}
    local pos = 1
    while pos <= #value do
        local slash = value:find('\\', pos, true)
        if not slash then
            result[#result + 1] = value:sub(pos)
            break
        end
        if slash > pos then
            result[#result + 1] = value:sub(pos, slash - 1)
        end
        local escaped = value:sub(slash + 1, slash + 1)
        if escaped == 'u' then
            local hex = value:sub(slash + 2, slash + 5)
            if #hex == 4 and hex:match('^%x%x%x%x$') then
                local num = tonumber(hex, 16)
                if num and num < 128 then
                    result[#result + 1] = string.char(num)
                else
                    result[#result + 1] = '?'
                end
                pos = slash + 6
            else
                result[#result + 1] = '\\'
                pos = slash + 1
            end
        else
            local decoded = json_simple_escapes[escaped]
            if decoded then
                result[#result + 1] = decoded
                pos = slash + 2
            else
                -- Preserve malformed or incomplete escapes for the caller to reject or display.
                result[#result + 1] = '\\'
                pos = slash + 1
            end
        end
    end
    return table.concat(result)
end

local function json_get_string(body, name)
    local start_pos = body:find('"' .. name .. '"%s*:%s*"')
    if not start_pos then return nil end
    local value_start = body:find('"', start_pos + #name + 2)
    if not value_start then return nil end
    value_start = value_start + 1
    local pos = value_start
    while pos <= #body do
        local char = body:sub(pos, pos)
        if char == '"' then
            local slash_count = 0
            local back = pos - 1
            while back >= value_start and body:sub(back, back) == '\\' do
                slash_count = slash_count + 1
                back = back - 1
            end
            if slash_count % 2 == 0 then
                return json_unescape(body:sub(value_start, pos - 1))
            end
        end
        pos = pos + 1
    end
    return nil
end

local function json_get_object(body, name)
    local name_end = body:find('"' .. name .. '"%s*:%s*{')
    if not name_end then return nil end
    local object_start = body:find('{', name_end)
    if not object_start then return nil end
    local depth = 0
    local in_string = false
    local escaped = false
    for pos = object_start, #body do
        local char = body:sub(pos, pos)
        if in_string then
            if escaped then
                escaped = false
            elseif char == '\\' then
                escaped = true
            elseif char == '"' then
                in_string = false
            end
        elseif char == '"' then
            in_string = true
        elseif char == '{' then
            depth = depth + 1
        elseif char == '}' then
            depth = depth - 1
            if depth == 0 then
                return body:sub(object_start, pos)
            end
        end
    end
    return nil
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
    log_debug(
        "sleeping for " .. tostring(secs) .. " seconds (base="
            .. tostring(base_seconds) .. ", jitter=" .. tostring(jitter_fraction) .. ")"
    )
    while secs > 0 do
        if is_readable(lock_dir_stop_request_path) then
            log_warning("B72755 stop_request")
            cleanup_and_exit(0)
        end
        if secs >= 2 then
            sleep(2)
            secs = secs - 2
        else
            sleep(1)
            secs = secs - 1
        end
    end
end

local function run_tests()
    -- returns true only if all tests pass
    local t1 = set_sleep_method(true)
    return t1
end

--
-- CLI
--

local cli_verb = nil
local running_path = arg and arg[0] or ''
if running_path == '' then fail_early("B72200 cannot find running_path") end

local function print_help()
    local program = arg and arg[0] or 'bbbased.lua'
    io.stdout:write(table.concat({
        "Usage: " .. program .. " [options] <verb>",
        "",
        "Manage or run the BitBurrow base daemon.",
        "",
        "Verbs:",
        "  install      Install or reinstall the daemon as a system service.",
        "  daemonize    Run the daemon in the foreground. This internal verb is",
        "               normally invoked by a service wrapper holding the lock.",
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
if cli_verb == nil then
    log_error("a verb is required")
    print_help()
    os.exit(1)
elseif cli_verb == 'help' then
    print_help()
    os.exit(0)
elseif cli_verb == 'version' then
    print("BitBurrow base daemon, version " .. file_version)
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
elseif cli_verb == 'daemonize' then
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
        '        echo $$ > ' .. shell_quote(lock_dir_pid_path),
        '        exec /usr/bin/lua ' .. shell_quote(lua_path) .. ' daemonize',
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
    local temp_name = relative_path:match('^(bbbased%.[^/]+)/bbbased%.lua$')
    if not temp_name then return end
    remove_paths(running_path, tmp_dir .. temp_name)
end

local function init_service_text(lua_path)
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
        '    procd_set_param respawn 60 10 7',  -- up for 60 seconds clears the crash count
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

local function systemd_service_texts(lua_path)
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

local function install_service_file(path, content, mode)
    -- return success and whether the file was changed
    if read_text_file(path, true, true) == content and get_mode(path) == mode then
        return true, false
    end
    local tmp_path = make_temp_path(tmp_dir)  -- bbsubd_tmp_dir may not exist during installation
    if not tmp_path then return nil end
    local installed = write_text_file(tmp_path, content) and file_copy(tmp_path, path, mode)
    remove_path(tmp_path)
    if not installed then return nil end
    return true, true
end

local function install_init_service(lua_path)
    -- return nil on failure, true after successful install or reinstall
    local init_path = '/etc/init.d/' .. bbsubd
    if not install_service_file(init_path, init_service_text(lua_path), '0755') then return nil end
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
    remove_temporary_installer()
    return true
end

local function install_systemd_service(lua_path)
    -- return nil on failure, true after successful install or reinstall
    local service_name = bbsubd .. '.service'
    local service_path = '/etc/systemd/system/' .. service_name
    local start_script_path = '/usr/local/sbin/' .. bbsubd .. '-start.sh'
    local stop_script_path = '/usr/local/sbin/' .. bbsubd .. '-stop.sh'
    local start_text, stop_text, service_text = systemd_service_texts(lua_path)
    if not install_service_file(start_script_path, start_text, '0755')
            or not install_service_file(stop_script_path, stop_text, '0755')
            or not install_service_file(service_path, service_text, '0644') then
        return nil
    end
    if not file_copy(running_path, lua_path, '0644') then return nil end
    remove_path('/etc/init.d/' .. bbsubd)
    if not run_command('systemctl daemon-reload') then return nil end
    if not run_command('systemctl is-enabled ' .. shell_quote(service_name), true, true) then
        if not run_command('systemctl enable ' .. shell_quote(service_name)) then
            return nil
        end
    end
    if not run_command('systemctl is-active ' .. shell_quote(service_name), true, true) then
        if not run_command('systemctl start ' .. shell_quote(service_name)) then return nil end
        log_debug("successfully installed; exiting")
    else
        if not run_command('systemctl restart ' .. shell_quote(service_name)) then return nil end
        log_debug("successfully reinstalled; exiting")
    end
    -- new service should run now; don't use this here: dofile(lua_path)
    remove_temporary_installer()
    return true
end

-- values must match class Platform() in db.py
local platform = run_command('command -v systemctl', true, true) and 'sysd' or 'init'

local function install_daemon_service(lua_path)
    if platform == 'sysd' then
        return install_systemd_service(lua_path)
    else  -- 'init'
        return install_init_service(lua_path)
    end
end

local function restart_after_update()
    if platform == 'init' then
        local init_path = '/etc/init.d/' .. bbsubd
        local reload_command = shell_quote(init_path) .. ' reload'
        while not run_command(reload_command, true, true) do
            log_error("B70489 update reload request failed; retrying")
            sleep(30)
        end
        sleep(30)  -- procd should intentionally replace this instance
        log_error("B91527 update reload did not stop the daemon; exiting")
    end
    cleanup_and_exit(0)
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
local unpack_fn = table.unpack or unpack

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
        '(curl $U',
    }
    local mode = get_mode(path)
    if not mode then
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
            if line:sub(1, #prefix) ~= prefix then
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
        log_debug("adopt5c code not found in " .. path)
        return true
    end
    local kept = {}
    for i = 1, #lines do
        if i < delete_at or i >= delete_at + #prefixes then
            kept[#kept + 1] = lines[i].text .. lines[i].eol
        end
    end
    local new_content = table.concat(kept)
    local temp_path = make_temp_path(tmp_dir)
    if not temp_path then return nil end
    local replaced = write_text_file(temp_path, new_content)
        and chmod(temp_path, mode)
        and run_command('mv ' .. shell_quote(temp_path) .. ' ' .. shell_quote(path), true)
    if not replaced then
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
local config_dir = '/etc/' .. bbsubd .. '/'
local auth_privkey_path = config_dir .. 'client_rsapss.pem'
local auth_pubkey_path = config_dir .. 'client_rsapss_pub.pem'
local wg_privkey_path = config_dir .. 'wgbb1_private.key'
local wg_pubkey_path = config_dir .. 'wgbb1_public.key'
local pubkeys_uploaded_path = config_dir .. 'pubkeys_uploaded'
local deploy_result_path = config_dir .. 'deploy_result'

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
    if not output then remove_paths(auth_privkey_path, auth_pubkey_path) return nil end
    return true
end

local function ensure_wg_keys()
    if not install_one_of('wireguard-tools wg-installer-server', 'wg') then return nil end
    if is_readable(wg_privkey_path) and is_readable(wg_pubkey_path) then
        log_debug("wg_privkey and wg_pubkey both already exist")
        return true
    end
    log_info("WireGuard keys are missing; generating new keypair")
    remove_path(wg_pubkey_path)
    local output = run_command_with_umask_077(
        'wg genkey >' .. shell_quote(wg_privkey_path)
            .. ' && wg pubkey <' .. shell_quote(wg_privkey_path)
            .. ' >' .. shell_quote(wg_pubkey_path)
    )
    if not output then remove_paths(wg_privkey_path, wg_pubkey_path) return nil end
    return true
end

local function next_retry_state(retry_wait, retries_left, operation)
    retries_left = retries_left - 1
    if retries_left > 0 then return retry_wait, retries_left end
    retry_wait = math.min(retry_wait * 2, 3600)
    log_info(
        "increased " .. operation .. " retry wait to " .. tostring(retry_wait) .. " seconds"
    )
    return retry_wait, 2
end

local function do_adopt6c()
    -- return true on successful API call
    -- return false iff API call does not need to be done
    -- return nil on permanent failure
    -- retry forever on communication failure
    local auth_mtime = file_mtime(auth_privkey_path)
    local uploaded_mtime = file_mtime(pubkeys_uploaded_path)
    if uploaded_mtime >= auth_mtime then
        -- above, use '>=' and not '>' to avoid race condition and disabled client
        log_info("public key already marked as uploaded")
        return false  -- these public keys were previously uploaded
    end
    local token = read_text_file(ott_path, true):gsub("%s+", "")
    -- strip '\n' from middle if 2 'echo ... >>$T' lines in get_adopt5c_code()
    local token_mtime = file_mtime(ott_path)
    if token == '' or token_mtime == 0 then
        log_warning("B16500 cannot read " .. ott_path .. "; trying ping")
        return false  -- just in case pubkey was uploaded but updating pubkeys_uploaded_path failed
    end
    local retry_wait = 7
    local retries_left = 2
    local auth_pubkey = read_text_file(auth_pubkey_path, false)
    if not auth_pubkey then
        -- log_error() already called from read_text_file()
        return nil
    end
    log_info("public keys need upload to " .. api_url)
    while true do
        if (os.time() - token_mtime) >= 45*60 then
            -- if changing max time above, search: tag_ott_valid_for
            log_error("B31143 token " .. ott_path .. " is expired")  -- enforced on server too
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
            -- log_error() already called from make_temp_path()
            remove_paths(request_path, response_path)
            return nil
        end
        local request_body = '{'
            .. '"jsonrpc":"2.0",'
            .. '"id":1,'
            .. '"method":"adopt6c",'
            .. '"params":{'
                .. '"subd":"' .. json_escape(subd) .. '",'
                .. '"token":"' .. json_escape(token) .. '",'
                .. '"auth_pubkey":"' .. json_escape(auth_pubkey) .. '"'
                .. '}'
            .. '}'
        local write_ok = write_text_file(request_path, request_body, '0600')
        if not write_ok then
            -- log_error() already called from write_text_file()
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
            .. ' -o '
            .. shell_quote(response_path)
        local curl_output = run_command(curl_command, true)
        local response_body = read_text_file(response_path, true)
        remove_paths(request_path, response_path)
        if curl_output and response_body then
            log_debug("adopt6c response body: " .. displayable(response_body, 60))
            local has_jsonrpc = response_body:match('"jsonrpc"%s*:%s*"2%.0"') ~= nil
            local has_result = response_body:match('"result"%s*:') ~= nil
            local has_error = response_body:match('"error"%s*:') ~= nil
            local status = json_get_string(response_body, 'status')
            local response_subd = json_get_string(response_body, 'subd')
            local mostly_okay = has_jsonrpc and has_result and not has_error
            if mostly_okay and status == 'ok' and response_subd == subd then
                log_info("public key upload succeeded")
                if not write_text_file(pubkeys_uploaded_path, 'uploaded\n', '0600') then
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
        retry_wait, retries_left = next_retry_state(retry_wait, retries_left, 'adopt6c')
    end
    log_error("B49403 should never get here")
end

local function collect_telemetry()
    local telemetry = '{'
        .. '"proc_uptime":"' .. json_escape(read_text_file('/proc/uptime', true, false)) .. '",'
        .. '"proc_loadavg":"' .. json_escape(read_text_file('/proc/loadavg', true, false)) .. '",'
        .. '"proc_meminfo":"' .. json_escape(read_text_file('/proc/meminfo', true, false)) .. '",'
        .. '"proc_net_dev":"' .. json_escape(read_text_file('/proc/net/dev', true, false)) .. '",'
        .. '"proc_net_route":"' .. json_escape(read_text_file('/proc/net/route', true, false)) .. '",'
        -- maybe add: df or /proc/mounts + stat
        -- maybe add: ip addr, /proc/net/fib_trie
        -- maybe add: wg
        .. '"etc_os_release":"' .. json_escape(read_text_file('/etc/os-release', true, false)) .. '",'
        .. '"platform":"' .. platform .. '",'
        -- don't need file_version every time, but it's low-cost and needed if hub or we restart
        .. '"file_version":"' .. json_escape(file_version) .. '",'
        .. '"telemetry_version": 1'
    return telemetry .. '}'
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
    local request_body = '{'
        .. '"jsonrpc":"2.0",'
        .. '"id":1,'
        .. '"method":"ping",'
        .. '"params":{'
            .. '"subd":"' .. json_escape(subd) .. '",'
            .. '"time":"' .. json_escape(utc_time) .. '",'
            .. '"telemetry":' .. collect_telemetry() .. ','
            .. '"request_id":"' .. json_escape(request_id) .. '"'
            .. '}'
        .. '}'
    log_debug("built ping request body (" .. tostring(#request_body) .. " bytes)")
    return request_body
end

local signature_algorithms = {
    sha256 = {
        name = 'rsa-pss-sha256',
        digest = 'sha256',
        mgf1 = 'sha256',
        saltlen = '32',
    },
    sha512 = {
        name = 'rsa-pss-sha512',
        digest = 'sha512',
        mgf1 = 'sha512',
        saltlen = '64',
    },
}

local function choose_signature_algorithm()
    -- prefer RFC 9421 rsa-pss-sha512; fall back to non-standard rsa-pss-sha256
    local probe_path = make_temp_path()
    local sig_path = make_temp_path()
    if not probe_path or not sig_path then
        remove_paths(probe_path, sig_path)
        return signature_algorithms.sha256
    end
    if not write_text_file(probe_path, 'probe', '0600') then
        remove_paths(probe_path, sig_path)
        return signature_algorithms.sha256
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
    remove_paths(probe_path, sig_path)
    if ok then return signature_algorithms.sha512 end
    log_warning('B41234 OpenSSL lacks rsa-pss-sha512 support; falling back to rsa-pss-sha256')
    return signature_algorithms.sha256  -- non-standard for RFC 9421
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
        local sigalg = choose_signature_algorithm()
        local signature_params = '("@method" "@authority" "@target-uri" "content-type" '
            .. '"content-digest" "date");created='
            .. created_value
            .. ';keyid="'
            .. keyid_value
            .. '";nonce="'
            .. nonce_param_value
            .. '";alg="'
            .. sigalg.name
            .. '"'
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
            .. ' -o '
            .. shell_quote(response_path)
        local curl_output = run_command(curl_command, true)
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
    local request_body = '{'
        .. '"jsonrpc":"2.0",'
        .. '"id":1,'
        .. '"method":"task_result",'
        .. '"params":{'
            .. '"subd":"' .. json_escape(subd) .. '",'
            .. '"task_id":"' .. json_escape(task_id) .. '",'
            .. '"task_method":"' .. json_escape(task_method) .. '",'
            .. '"ok":' .. (ok and 'true' or 'false') .. ','
            .. '"output":"' .. json_escape(output) .. '"'
            .. '}'
        .. '}'
    local response_body = send_signed_jsonrpc(request_body)
    if not response_body then
        log_warning("task_result failed without a usable response")
        return nil
    end
    log_debug("task_result response body: " .. displayable(response_body, 300))
    local has_jsonrpc = response_body:match('"jsonrpc"%s*:%s*"2%.0"') ~= nil
    local has_error = response_body:match('"error"%s*:') ~= nil
    local status = json_get_string(response_body, 'status')
    if has_jsonrpc and not has_error and status == 'ok' then
        log_info("task_result accepted for " .. task_method .. " task " .. task_id)
        return true
    end
    log_error("B83275 task_result rejected: " .. displayable(response_body, 300))
    return nil
end

local function send_deploy_result()
    local task_data = read_text_file(deploy_result_path, true, true)
    if not task_data or task_data == '' then return true end
    local task_id, task_method = task_data:match('^([^\r\n]+)[\r\n]+([^\r\n]+)')
    if not task_id or task_method ~= 'update' then
        log_warning("B49873 invalid deploy result marker; removing it")
        remove_path(deploy_result_path)
        return nil
    end
    if send_task_result(task_id, task_method, true, 'ok') then
        remove_path(deploy_result_path)
        return true
    end
    return nil
end

local function xml_unescape(value)
    if not value then return nil end
    value = value:gsub('&#x([0-9A-Fa-f]+);', function(n)
        n = tonumber(n, 16)
        return n and n < 256 and string.char(n) or ''
    end)
    value = value:gsub('&#([0-9]+);', function(n)
        n = tonumber(n, 10)
        return n and n < 256 and string.char(n) or ''
    end)
    return (value:gsub('&lt;', '<')
        :gsub('&gt;', '>')
        :gsub('&quot;', '"')
        :gsub('&apos;', "'")
        :gsub('&amp;', '&'))
end

local function resolve_http_url(base_url, reference)
    reference = xml_unescape(reference)
    if not reference then return nil end
    reference = reference:match('^%s*(.-)%s*$')
    if reference:match('^https?://') then return reference end
    local scheme, authority, path = base_url:match('^(https?)://([^/]+)(/[^?#]*)')
    if not scheme then
        scheme, authority = base_url:match('^(https?)://([^/?#]+)')
        path = '/'
    end
    if not scheme or not authority then return nil end
    if reference:sub(1, 2) == '//' then
        return scheme .. ':' .. reference
    end
    if reference:sub(1, 1) == '/' then
        return scheme .. '://' .. authority .. reference
    end
    local directory = path:gsub('[^/]*$', '')
    return scheme .. '://' .. authority .. directory .. reference
end

local function http_url_host(url)
    local authority = url and url:match('^https?://([^/%?#]+)')
    if not authority or authority == ''
            or authority:find('@', 1, true)
            or authority:find('[%c%s]') then
        return nil
    end
    if authority:sub(1, 1) == '[' then
        local host, suffix = authority:match('^%[([^%]]+)%](.*)$')
        if not host or host == '' then return nil end
        if suffix ~= '' then
            local port = suffix:match('^:(%d+)$')
            if not port or tonumber(port) > 65535 then return nil end
        end
        return host
    end
    local host, port = authority:match('^([^:]+):(%d+)$')
    if not host then
        if authority:find(':', 1, true) then return nil end
        host = authority
    elseif tonumber(port) > 65535 then
        return nil
    end
    return host ~= '' and host or nil
end

local function upnp_control_url(xml, location)
    local url_base = xml:match('<URLBase[^>]*>%s*(.-)%s*</URLBase%s*>')
    local base_url = xml_unescape(url_base) or location
    local service_names = {'WANIPConnection', 'WANPPPConnection'}
    for _, service_name in ipairs(service_names) do
        for service in xml:gmatch('<service[^>]*>(.-)</service%s*>') do
            local service_type = service:match(
                '<serviceType[^>]*>%s*(.-)%s*</serviceType%s*>'
            )
            if service_type and service_type:find(
                    'urn:schemas%-upnp%-org:service:' .. service_name .. ':%d+',
                    1
                ) then
                local control_url = service:match(
                    '<controlURL[^>]*>%s*(.-)%s*</controlURL%s*>'
                )
                return resolve_http_url(base_url, control_url)
            end
        end
    end
    return nil
end

local function uint16_be(value)
    value = math.floor(value) % 65536
    return string.char(math.floor(value / 256), value % 256)
end

local function uint16_le(value)
    value = math.floor(value) % 65536
    return string.char(value % 256, math.floor(value / 256))
end

local function uint32_le(value)
    value = math.floor(value) % 4294967296
    local b1 = value % 256
    value = math.floor(value / 256)
    local b2 = value % 256
    value = math.floor(value / 256)
    local b3 = value % 256
    value = math.floor(value / 256)
    return string.char(b1, b2, b3, value % 256)
end

local function internet_checksum(data)
    local sum = 0
    for i = 1, #data, 2 do
        sum = sum + data:byte(i) * 256 + (data:byte(i + 1) or 0)
        while sum > 65535 do
            sum = (sum % 65536) + math.floor(sum / 65536)
        end
    end
    return 65535 - sum
end

local function ipv4_bytes(address)
    if not address then return nil end
    local a, b, c, d = address:match(
        '^(%d+)%.(%d+)%.(%d+)%.(%d+)$'
    )
    local octets = {tonumber(a), tonumber(b), tonumber(c), tonumber(d)}
    for i = 1, 4 do
        if not octets[i] or octets[i] > 255 then return nil end
    end
    return string.char(octets[1], octets[2], octets[3], octets[4])
end

local function ipv4_number(address)
    local bytes = ipv4_bytes(address)
    if not bytes then return nil end
    local a, b, c, d = bytes:byte(1, 4)
    return ((a * 256 + b) * 256 + c) * 256 + d
end

local function ipv4_in_subnet(address, subnet_address, prefix_length)
    local address_number = ipv4_number(address)
    local subnet_number = ipv4_number(subnet_address)
    prefix_length = tonumber(prefix_length)
    if not address_number or not subnet_number or not prefix_length
            or prefix_length < 0 or prefix_length > 32 then
        return false
    end
    local subnet_size = 2 ^ (32 - prefix_length)
    return math.floor(address_number / subnet_size)
        == math.floor(subnet_number / subnet_size)
end

local function get_upnp_route_info()
    local info = {gateway = ''}
    info.wan_route = run_command('ip route show default', true, true)
    local default_route = info.wan_route and ('\n' .. info.wan_route):match('\n(default[^\r\n]*)')
    if not default_route then return info, "B73361 cannot determine default route" end
    info.wan_if = default_route:match('%sdev%s+(%S+)')
    info.gateway = default_route:match('%svia%s+(%S+)') or ''
    if not info.wan_if or info.wan_if == '' then return info, "B86262 cannot determine wan_if" end
    local route_target = info.gateway ~= '' and info.gateway or '239.255.255.250'
    info.source_route = run_command('ip -4 route get ' .. shell_quote(route_target), true, true)
    info.source_ip = info.source_route and info.source_route:match('%ssrc%s+(%d+%.%d+%.%d+%.%d+)')
    local source_if = info.source_route and info.source_route:match('%sdev%s+(%S+)')
    if source_if and source_if ~= info.wan_if then return info, "B60570 route interfaces differ" end
    local ip_output = run_command('ip -4 address show dev ' .. shell_quote(info.wan_if), true, true)
    for address, prefix in tostring(ip_output or ''):gmatch('%sinet%s+(%d+%.%d+%.%d+%.%d+)/(%d+)') do
        if not info.source_ip then info.source_ip = address end
        if address == info.source_ip then info.source_prefix = tonumber(prefix) break end
    end
    if not info.source_ip then return info, "B60571 cannot determine UPnP source IP" end
    if not info.source_prefix then return info, "B35288 cannot determine UPnP source subnet" end
    return info
end

local function mac_bytes(address)
    if not address or not address:match('^[%x][%x]:[%x][%x]:[%x][%x]:[%x][%x]:[%x][%x]:[%x][%x]$') then
        return nil
    end
    local octets = {}
    for octet in address:gmatch('[%x][%x]') do octets[#octets + 1] = tonumber(octet, 16) end
    if #octets ~= 6 then return nil end
    return string.char(octets[1], octets[2], octets[3], octets[4], octets[5], octets[6])
end

local min_upnp_probe_packets = 2
local max_upnp_probe_packets = 11
local max_upnp_replay_span_us = 15000000
local max_upnp_capture_packets = 128
local max_upnp_capture_seconds = 30
local max_upnp_locations = 8
local max_upnp_igds = 8
local max_upnp_description_seconds = 12
local max_upnp_result_bytes = 18000

local function upnp_url_policy(url, gateway, source_ip, source_prefix)
    -- return allowed, gateway match, and host; allowed is nil for malformed URLs
    if type(url) ~= 'string' or #url > 2048 or url:find('[%c%s]') or not url:match('^https?://') then
        return nil, false, nil
    end
    local host = http_url_host(url)
    if not host then return nil, false, nil end
    local host_number = ipv4_number(host)
    local gateway_number = ipv4_number(gateway)
    local matches_gateway = host_number and gateway_number and host_number == gateway_number
    local matches_subnet = ipv4_in_subnet(host, source_ip, source_prefix)
    return matches_gateway or matches_subnet, matches_gateway, host
end

local function filter_upnp_locations(
        candidates, gateway, source_ip, source_prefix
    )
    -- enforce the common URL, gateway/subnet, uniqueness, ordering, and count limits
    local locations = {}
    local fallback_locations = {}
    local seen_locations = {}
    local response_count = 0
    local rejected_location_count = 0
    local location_limit_warned = false
    for _, location in ipairs(candidates or {}) do
        local allowed, matches_gateway, location_host = upnp_url_policy(
            location,
            gateway,
            source_ip,
            source_prefix
        )
        if allowed ~= nil then response_count = response_count + 1 end
        if allowed ~= nil and not seen_locations[location] then
            seen_locations[location] = true
            if not allowed then
                rejected_location_count = rejected_location_count + 1
                if rejected_location_count <= 8 then
                    log_info(
                        "B14803 dropping UPnP LOCATION outside gateway/LAN subnet; "
                            .. "host=" .. displayable(
                                json_escape(location_host or '(invalid)'),
                                100
                            )
                    )
                elseif rejected_location_count == 9 then
                    log_warning("B92150 additional rejected UPnP LOCATION warnings omitted")
                end
            else
                local accepted_count = #locations + #fallback_locations
                if accepted_count >= max_upnp_locations and not location_limit_warned then
                    log_warning("B06812 UPnP LOCATION limit reached; ignoring additional URLs")
                    location_limit_warned = true
                end
                if matches_gateway and accepted_count >= max_upnp_locations
                        and #fallback_locations > 0 then
                    table.remove(fallback_locations)
                    accepted_count = accepted_count - 1
                end
                if accepted_count < max_upnp_locations then
                    local destination = matches_gateway and locations or fallback_locations
                    destination[#destination + 1] = location
                end
            end
        end
    end
    for _, location in ipairs(fallback_locations) do locations[#locations + 1] = location end
    return locations, response_count
end

local function read_upnp_control_url(location, seconds_left, gateway, source_ip, source_prefix)
    local xml_path = make_temp_path()
    if not xml_path then return nil, false end
    local request_timeout = math.max(1, math.min(3, seconds_left or 3))
    local connect_timeout = math.min(2, request_timeout)
    local curl_command = 'curl -fsS --globoff --connect-timeout '
        .. tostring(connect_timeout) .. ' --max-time '
        .. tostring(request_timeout) .. ' '
        .. '--max-filesize 1048576 -o '
        .. shell_quote(xml_path) .. ' ' .. shell_quote(location)
    local control_url = nil
    local description_read = false
    if run_command(curl_command, true, true) then
        local xml = read_text_file(xml_path, false, true)
        if xml and #xml <= 1048576 then
            description_read = true
            control_url = upnp_control_url(xml, location)
            if control_url then
                local allowed, _, control_host = upnp_url_policy(
                    control_url,
                    gateway,
                    source_ip,
                    source_prefix
                )
                if not allowed then
                    log_info(
                        "B24547 dropping UPnP control URL outside "
                            .. "gateway/LAN subnet or with invalid authority; host="
                            .. displayable(json_escape(control_host or '(invalid)'), 100)
                    )
                    control_url = nil
                end
            end
        end
    end
    remove_path(xml_path)
    return control_url, description_read
end

local function read_upnp_igds(locations, gateway, source_ip, source_prefix)
    local igds = {}
    local seen_control_urls = {}
    local descriptions_read = 0
    local description_deadline = os.time() + max_upnp_description_seconds
    local igd_limit_warned = false
    for _, location in ipairs(locations) do
        if #igds >= max_upnp_igds then
            if not igd_limit_warned then
                log_warning("B90866 UPnP IGD limit reached; ignoring additional descriptions")
                igd_limit_warned = true
            end
            break
        end
        local seconds_left = description_deadline - os.time()
        if seconds_left <= 0 then
            log_warning("B39908 UPnP device-description time limit reached")
            break
        end
        local control_url, description_read = read_upnp_control_url(
            location,
            seconds_left,
            gateway,
            source_ip,
            source_prefix
        )
        if description_read then descriptions_read = descriptions_read + 1 end
        local found_gateway_igd = false
        if control_url and not seen_control_urls[control_url] then
            seen_control_urls[control_url] = true
            igds[#igds + 1] = {location = location, control_url = control_url, }
            local _, matches_gateway = upnp_url_policy(location, gateway, source_ip, source_prefix)
            found_gateway_igd = matches_gateway
        end
        if found_gateway_igd then break end
    end
    return igds, descriptions_read
end

local function upnp_result_json(gateway, method, response_count, igds)
    local result_prefix = '{'
        .. '"gateway":"' .. json_escape(gateway) .. '",'
        .. '"method":"' .. json_escape(method) .. '",'
        .. '"response_count":' .. tostring(response_count) .. ','
        .. '"igds":['
    local igd_json = {}
    local result_bytes = #result_prefix + 2  -- closing ]}
    for _, igd in ipairs(igds) do
        local entry = '{'
            .. '"location":"' .. json_escape(igd.location) .. '",'
            .. '"control_url":"' .. json_escape(igd.control_url) .. '"'
            .. '}'
        local separator_bytes = #igd_json > 0 and 1 or 0
        if result_bytes + separator_bytes + #entry > max_upnp_result_bytes then
            log_warning("B75089 UPnP discovery result size limit reached; ignoring additional IGDs")
            break
        end
        igd_json[#igd_json + 1] = entry
        result_bytes = result_bytes + separator_bytes + #entry
    end
    return result_prefix .. table.concat(igd_json, ',') .. ']}'
end

local function uint32_at(data, offset, little_endian)
    local b1, b2, b3, b4 = data:byte(offset, offset + 3)
    if not b4 then return nil end
    if little_endian then
        return b1 + b2 * 256 + b3 * 65536 + b4 * 16777216
    end
    return b4 + b3 * 256 + b2 * 65536 + b1 * 16777216
end

local function pcap_header_info(data)
    if not data or #data < 24 then return nil, 'short header' end
    local magic = {data:byte(1, 4)}
    local little_endian =
        (magic[1] == 212 and magic[2] == 195 and magic[3] == 178 and magic[4] == 161)
        or (magic[1] == 77 and magic[2] == 60 and magic[3] == 178 and magic[4] == 161)
    local big_endian =
        (magic[1] == 161 and magic[2] == 178 and magic[3] == 195 and magic[4] == 212)
        or (magic[1] == 161 and magic[2] == 178 and magic[3] == 60 and magic[4] == 77)
    if not little_endian and not big_endian then
        return nil, string.format(
            'unrecognized magic %02x%02x%02x%02x', magic[1], magic[2], magic[3], magic[4]
        )
    end
    local nanosecond = (magic[1] == 77 and magic[2] == 60) or (magic[3] == 60 and magic[4] == 77)
    local network = uint32_at(data, 21, little_endian)
    if not network then return nil, 'short network field' end
    return {little_endian = little_endian, nanosecond = nanosecond, linktype = network % 65536, }
end

local function extract_upnp_msearch_payload(packet_data)
    local marker = 'M-SEARCH * HTTP/1.1'
    local search_from = 1
    local last_problem = 'M-SEARCH request not found'
    while true do
        local payload_start = packet_data:find(marker, search_from, true)
        if not payload_start then return nil, last_problem end
        local crlf_end = packet_data:find('\r\n\r\n', payload_start, true)
        local lf_end = packet_data:find('\n\n', payload_start, true)
        local payload_end = nil
        if crlf_end and (not lf_end or crlf_end < lf_end) then
            payload_end = crlf_end + 3
        elseif lf_end then
            payload_end = lf_end + 1
        end
        if not payload_end or payload_end - payload_start + 1 > 1472 then
            last_problem = 'M-SEARCH headers are missing, truncated, or too large'
        else
            local payload = packet_data:sub(payload_start, payload_end)
            local upper = payload:upper()
            local expected_host = upper:find('[\r\n]HOST:%s*239%.255%.255%.250:1900')
            local expected_man = upper:find('[\r\n]MAN:%s*"SSDP:DISCOVER"')
            local st = upper:match('[\r\n]ST:%s*([^\r\n]+)')
            if expected_host and expected_man and st and #st <= 256 then
                st = st:gsub('%s+$', '')
                local mx = tonumber(upper:match('[\r\n]MX:%s*(%d+)')) or 3
                mx = math.max(1, math.min(mx, 5))
                return payload, nil, mx, st
            end
            last_problem = 'M-SEARCH request has unexpected HOST, MAN, or ST headers'
        end
        search_from = payload_start + #marker
    end
end

local function extract_upnp_msearches(pcap_data)
    -- link-layer headers vary when tcpdump captures on 'any'; parse the PCAP
    -- record boundaries and timestamps, then extract every stable SSDP payload
    local header, header_problem = pcap_header_info(pcap_data)
    if not header then return nil, header_problem end
    local probes = {}
    local record_offset = 25
    local record_count = 0
    local first_sec = nil
    local first_fraction_us = nil
    local max_mx = 1
    while record_offset <= #pcap_data do
        if #pcap_data - record_offset + 1 < 16 then return nil, 'truncated PCAP record header' end
        record_count = record_count + 1
        local timestamp_sec = uint32_at(pcap_data, record_offset, header.little_endian)
        local timestamp_fraction = uint32_at(pcap_data, record_offset + 4, header.little_endian)
        local included_length = uint32_at(pcap_data, record_offset + 8, header.little_endian)
        if not timestamp_sec or not timestamp_fraction or not included_length then
            return nil, 'truncated PCAP record fields'
        end
        local fraction_limit = header.nanosecond and 1000000000 or 1000000
        if timestamp_fraction >= fraction_limit then
            return nil, 'invalid PCAP packet timestamp'
        end
        local packet_start = record_offset + 16
        local packet_end = packet_start + included_length - 1
        if included_length > #pcap_data or packet_end > #pcap_data then
            return nil, 'truncated PCAP packet data'
        end
        local packet_data = pcap_data:sub(packet_start, packet_end)
        if packet_data:find('M-SEARCH * HTTP/1.1', 1, true) then
            local payload, problem, mx, st = extract_upnp_msearch_payload(packet_data)
            if not payload then
                return nil, 'record ' .. tostring(record_count) .. ': ' .. tostring(problem)
            end
            local fraction_us = header.nanosecond
                and math.floor(timestamp_fraction / 1000)
                or timestamp_fraction
            if not first_sec then
                first_sec = timestamp_sec
                first_fraction_us = fraction_us
            end
            local delay_us = (timestamp_sec - first_sec) * 1000000 + fraction_us - first_fraction_us
            if delay_us < 0 then
                return nil, 'M-SEARCH packet timestamps are not monotonic'
            end
            if delay_us > max_upnp_replay_span_us then
                return nil, 'M-SEARCH replay span exceeds 15 seconds'
            end
            probes[#probes + 1] = {
                payload = payload,
                delay_us = delay_us,
                mx = mx,
                st = st,
                record_number = record_count,
            }
            if mx > max_mx then max_mx = mx end
        end
        record_offset = packet_end + 1
    end
    if #probes == 0 then return nil, 'M-SEARCH request not found' end
    return probes, nil, max_mx, record_count
end

local function write_upnp_replay_pcap(path, probes, source_ip, source_mac, source_port)
    local source_ip_bytes = ipv4_bytes(source_ip)
    local source_mac_bytes = mac_bytes(source_mac)
    if not source_ip_bytes then return nil, 'invalid source IP' end
    if not source_mac_bytes then return nil, 'invalid source MAC' end
    if not probes or #probes == 0 then return nil, 'no M-SEARCH packets' end
    local destination_ip_bytes = string.char(239, 255, 255, 250)
    local destination_mac_bytes = string.char(1, 0, 94, 127, 255, 250)
    local pcap_parts = {string.char(212, 195, 178, 161)
        .. uint16_le(2)
        .. uint16_le(4)
        .. uint32_le(0)
        .. uint32_le(0)
        .. uint32_le(65535)
        .. uint32_le(1)}  -- LINKTYPE_ETHERNET
    local replay_start_sec = os.time()
    for packet_number, probe in ipairs(probes) do
        local payload = probe.payload
        if not payload or #payload > 1472 then return nil, 'M-SEARCH payload is too large' end
        local udp_length = 8 + #payload
        local udp_without_checksum = uint16_be(source_port)
            .. uint16_be(1900)
            .. uint16_be(udp_length)
            .. uint16_be(0)
        local pseudo_header = source_ip_bytes
            .. destination_ip_bytes
            .. string.char(0, 17)
            .. uint16_be(udp_length)
        local udp_checksum = internet_checksum(pseudo_header .. udp_without_checksum .. payload)
        if udp_checksum == 0 then udp_checksum = 65535 end
        local udp_header = uint16_be(source_port)
            .. uint16_be(1900)
            .. uint16_be(udp_length)
            .. uint16_be(udp_checksum)
        local ip_total_length = 20 + udp_length
        local ip_id_offset = probe.ip_id_offset or packet_number
        local ip_prefix = string.char(69, 0)
            .. uint16_be(ip_total_length)
            .. uint16_be((replay_start_sec + ip_id_offset - 1) % 65536)
            .. uint16_be(16384)
            .. string.char(2, 17)
            .. uint16_be(0)
            .. source_ip_bytes
            .. destination_ip_bytes
        local ip_checksum = internet_checksum(ip_prefix)
        local ip_header = ip_prefix:sub(1, 10) .. uint16_be(ip_checksum) .. ip_prefix:sub(13)
        local ethernet_frame = destination_mac_bytes
            .. source_mac_bytes
            .. uint16_be(2048)
            .. ip_header
            .. udp_header
            .. payload
        local delay_us = math.floor(probe.delay_us or 0)
        local timestamp_sec = replay_start_sec + math.floor(delay_us / 1000000)
        local timestamp_us = delay_us % 1000000
        pcap_parts[#pcap_parts + 1] = uint32_le(timestamp_sec)
            .. uint32_le(timestamp_us)
            .. uint32_le(#ethernet_frame)
            .. uint32_le(#ethernet_frame)
            .. ethernet_frame
    end
    if not write_text_file(path, table.concat(pcap_parts), '0600') then
        return nil, 'cannot write replay PCAP'
    end
    return true
end

local function log_upnp_diagnostic(label, value, max_lines)
    value = tostring(value or '')
    max_lines = max_lines or 40
    if value == '' then
        log_info('UPnP diagnostic ' .. label .. ': (empty)')
        return
    end
    local line_number = 0
    for line in (value .. '\n'):gmatch('(.-)\n') do
        line_number = line_number + 1
        log_info('UPnP diagnostic ' .. label .. '[' .. line_number .. ']: ' .. displayable(line, 700))
        if line_number >= max_lines then
            log_info('UPnP diagnostic ' .. label .. ': (remaining lines omitted)')
            break
        end
    end
end

local function start_upnp_capture(interface, capture_path, status_path, filter)
    local command = 'tcpdump -nn -U -s4096 -c ' .. tostring(max_upnp_capture_packets)
        .. ' -i ' .. shell_quote(interface)
        .. ' -w -'
        .. ' ' .. shell_quote(filter)
        .. ' </dev/null >' .. shell_quote(capture_path)
        .. ' 2>>' .. shell_quote(status_path)
        .. ' & echo $!'
    local pid = normalize_pid(run_command(command, true, true))
    if not pid then return nil, 'cannot start tcpdump: ' .. display_text_file(status_path) end
    -- use only POSIX shell built-ins plus sleep/kill so this also works on small
    -- router distributions without a separate timeout utility; rechecking the
    -- process start time prevents the watchdog from signaling a reused PID
    local watchdog_command = '(IFS= read -r upnp_original_stat < /proc/'
        .. pid .. '/stat || exit 0; set -- $upnp_original_stat; shift 21; '
        .. 'upnp_original_start=$1; sleep ' .. tostring(max_upnp_capture_seconds) .. '; '
        .. 'IFS= read -r upnp_current_stat < /proc/' .. pid
        .. '/stat || exit 0; set -- $upnp_current_stat; shift 21; '
        .. 'if [ "$1" = "$upnp_original_start" ]; then printf "%s\\n" '
        .. shell_quote('bbbased: UPnP capture time limit reached')
        .. ' >>' .. shell_quote(status_path) .. '; kill -TERM ' .. pid
        .. ' 2>/dev/null; fi) </dev/null >/dev/null 2>&1 & echo $!'
    local watchdog_pid = normalize_pid(run_command(watchdog_command, true, true))
    if not watchdog_pid then
        kill_process(pid)
        return nil, 'cannot start tcpdump time-limit watchdog'
    end
    for _ = 1, 100 do
        local grep_command = 'grep -Eq '
            .. shell_quote('^(tcpdump: )?listening on ')
            .. ' ' .. shell_quote(status_path)
        if run_command(grep_command, true, true) then return pid end
        if not is_running(pid) then break end
        sleep(0.1)
    end
    local problem
    if is_running(pid) then
        problem = "tcpdump won't listen: " .. display_text_file(status_path)
        kill_process(pid)
    else
        problem = 'tcpdump refused to listen: ' .. display_text_file(status_path)
    end
    return nil, problem
end

local function decode_upnp_capture(path)
    return run_command('tcpdump -nn -e -vv -s0 -A -r ' .. shell_quote(path), true, true)
end

local function capture_interface_drops(status)
    return tonumber(tostring(status or ''):match(
        '(%d+)%s+packet[s]* dropped by interface'
    )) or 0
end

local function capture_packet_count(status)
    return tonumber(tostring(status or ''):match(
        '(%d+)%s+packet[s]* captured'
    ))
end

local function capture_time_limit_reached(status)
    return tostring(status or ''):find('bbbased: UPnP capture time limit reached', 1, true) ~= nil
end

local function upnp_probe_summary(probes, record_count)
    local gaps = {}
    local targets = {}
    for index, probe in ipairs(probes or {}) do
        if index > 1 then
            gaps[#gaps + 1] = tostring(probe.delay_us - probes[index - 1].delay_us)
        end
        targets[#targets + 1] = probe.st
    end
    local span_us = probes and #probes > 0 and probes[#probes].delay_us or 0
    return tostring(#(probes or {})) .. ' M-SEARCH packets from '
        .. tostring(record_count or 0) .. ' PCAP records over '
        .. string.format('%.6f', span_us / 1000000) .. ' seconds; gaps(us)='
        .. (#gaps > 0 and table.concat(gaps, ',') or '(none)')
        .. '; ST=' .. table.concat(targets, ' | ')
end

local function discover_upnp_pcap(pcap_base64)
    -- return success flag and JSON discovery results, or failure details
    local encoded_path = nil
    local pcap_path = nil
    local replay_packet_paths = {}
    local capture_path = nil
    local capture_status_path = nil
    local tcpreplay_output_path = nil
    local tcpdump_pid = nil
    local capture_started = false
    local wan_route = nil
    local wan_if = nil
    local gateway = ''
    local source_route = nil
    local source_ip = nil
    local source_prefix = nil
    local source_mac = nil
    local source_port = nil
    local probe_packets = nil
    local probe_record_count = nil
    local probe_mx = nil
    local wait_seconds = nil
    local response_wait_seconds = nil
    local capture_deadline = nil
    local tcpreplay_output = nil
    local captured_packets = nil
    local replay_capture_summary = nil
    local replay_timing_problem = nil
    local capture_status = nil
    local diagnostic_reason = nil
    local success = false
    local result = "B04382 discover_upnp failed"
    repeat
        if not pcap_base64 or pcap_base64 == '' then result = "B86322 missing pcap data" break end
        pcap_base64 = pcap_base64:gsub('%s', '')
        if #pcap_base64 > 20000 or #pcap_base64 < 20 or #pcap_base64 % 4 ~= 0 then
            result = string.format("B64087 invalid pcap data size (%d bytes)", #pcap_base64)
            break
        end
        local padding = pcap_base64:match('(=*)$') or ''
        local base64_body = pcap_base64:sub(1, #pcap_base64 - #padding)
        if #padding > 2 or base64_body:find('=', 1, true) or base64_body:find('[^A-Za-z0-9+/]') then
            result = "B24078 invalid base64 pcap data"
            break
        end
        encoded_path = make_temp_path()
        pcap_path = make_temp_path()
        capture_path = make_temp_path()
        capture_status_path = make_temp_path()
        tcpreplay_output_path = make_temp_path()
        if not encoded_path or not pcap_path or not capture_path
                or not capture_status_path or not tcpreplay_output_path then
            result = "B85375 cannot create discover_upnp temporary files"
            break
        end
        if not write_text_file(encoded_path, pcap_base64, '0600') then
            result = "B46150 cannot write encoded pcap data"
            break
        end
        local decode_command = 'openssl base64 -d -A -in ' .. shell_quote(encoded_path)
            .. ' -out ' .. shell_quote(pcap_path)
        if not run_command(decode_command, true, true) then
            result = "B24079 invalid base64 pcap data"
            break
        end
        local pcap_data = read_text_file(pcap_path, false, true)
        if not pcap_data then result = "B60578 cannot read decoded UPnP probe PCAP" break end
        local extract_problem
        probe_packets, extract_problem, probe_mx, probe_record_count =
            extract_upnp_msearches(pcap_data)
        if not probe_packets then
            result = "B60573 invalid UPnP probe PCAP: " .. tostring(extract_problem)
            break
        end
        if #probe_packets < min_upnp_probe_packets or #probe_packets > max_upnp_probe_packets then
            result = "B16473 invalid UPnP probe PCAP: expected 2 through 11 "
                .. 'M-SEARCH packets, found '
                .. tostring(#probe_packets)
            break
        end
        if not install_one_of('iproute2 iproute ip-tiny ip-full', 'ip')
                or not install_one_of('tcpdump', 'tcpdump')
                or not install_one_of('tcpreplay', 'tcpreplay') then
            result = "B91912 cannot install discover_upnp dependencies"
            break
        end
        local route_info, route_problem = get_upnp_route_info()
        if route_problem then result = route_problem break end
        wan_route = route_info.wan_route
        wan_if = route_info.wan_if
        gateway = route_info.gateway
        source_route = route_info.source_route
        source_ip = route_info.source_ip
        source_prefix = route_info.source_prefix
        local link_output = run_command('ip link show dev ' .. shell_quote(wan_if), true, true)
        source_mac = link_output and link_output:match(
            'link/ether%s+([%x][%x]:[%x][%x]:[%x][%x]:[%x][%x]:[%x][%x]:[%x][%x])'
        )
        if not source_mac then
            result = "B60572 UPnP discovery requires an Ethernet source MAC"
            break
        end
        -- a capture made on Linux 'any' uses SLL/SLL2 rather than Ethernet,
        -- which some tcpreplay builds misinterpret; rebuild every captured
        -- M-SEARCH as Ethernet, preserving the original inter-packet delays and
        -- addressing all packets as this base; SSDP replies are unicast to the
        -- probe's source IP and UDP port
        source_port = 49152 + (os.time() % 16384)
        local packet_pcap_problem = nil
        for index, probe in ipairs(probe_packets) do
            local packet_path = make_temp_path()
            if not packet_path then
                packet_pcap_problem = 'cannot create packet ' .. tostring(index) .. ' replay PCAP'
                break
            end
            replay_packet_paths[#replay_packet_paths + 1] = packet_path
            local packet_written, packet_problem = write_upnp_replay_pcap(
                packet_path,
                {{
                    payload = probe.payload,
                    delay_us = 0,
                    ip_id_offset = index,
                }},
                source_ip,
                source_mac,
                source_port
            )
            if not packet_written then
                packet_pcap_problem = 'packet ' .. tostring(index) .. ': ' .. tostring(packet_problem)
                break
            end
        end
        if packet_pcap_problem then
            result = "B27416 cannot build UPnP packet replay PCAPs: " .. packet_pcap_problem
            break
        end
        wait_seconds = probe_mx + 1
        log_debug(
            "UPnP discovery probe source: " .. source_ip .. ":" .. source_port .. " on " .. wan_if
        )
        -- match replies by destination, not by an assumed responder source port
        local capture_filter = 'udp and ((src host ' .. source_ip .. ' and src port '
            .. tostring(source_port)
            .. ' and dst host 239.255.255.250 and dst port 1900)'
            .. ' or (dst host ' .. source_ip .. ' and dst port '
            .. tostring(source_port) .. '))'
        local capture_problem
        capture_deadline = os.time() + max_upnp_capture_seconds
        tcpdump_pid, capture_problem = start_upnp_capture(
            wan_if,
            capture_path,
            capture_status_path,
            capture_filter
        )
        if not tcpdump_pid then result = "B26445 " .. tostring(capture_problem) break end
        capture_started = true
        sleep(0.1)
        -- some older tcpreplay builds ignore the first inter-packet delay in a
        -- multi-packet PCAP; send one-packet PCAPs and reproduce every captured
        -- gap explicitly so all M-SEARCH requests retain their ordering and
        -- approximate original timing on those builds as well
        local replay_outputs = {}
        local replay_failure = nil
        for index, packet_path in ipairs(replay_packet_paths) do
            if index > 1 then
                local gap_us = probe_packets[index].delay_us - probe_packets[index - 1].delay_us
                local gap = gap_us / 1000000  -- in seconds
                if capture_deadline and os.time() + math.ceil(gap) > capture_deadline then
                    replay_failure = "B53263 UPnP replay would exceed capture time limit"
                    break
                end
                if not sleep(gap) then
                    replay_failure = "B83461 sleep failed before packet " .. tostring(index)
                    break
                end
            end
            local tcpreplay_command = '(tcpreplay '
                .. shell_quote('-i' .. wan_if)
                .. ' ' .. shell_quote(packet_path)
                .. ' >' .. shell_quote(tcpreplay_output_path) .. ' 2>&1)'
            local tcpreplay_ok = run_command(tcpreplay_command, true, true) ~= nil
            local packet_output = read_text_file(tcpreplay_output_path, true, true) or ''
            replay_outputs[#replay_outputs + 1] = 'packet ' .. tostring(index) .. ':\n' .. packet_output
            if not tcpreplay_ok then
                replay_failure = "B92999 tcpreplay packet "
                    .. tostring(index) .. " failed: "
                    .. displayable(packet_output, 300)
                break
            end
            local replay_output_lower = packet_output:lower()
            local failed_packets = tonumber(replay_output_lower:match('failed packets:%s*(%d+)')) or 0
            local truncated_packets = tonumber(
                replay_output_lower:match('truncated packets:%s*(%d+)')
            ) or 0
            local actual_packets = tonumber(replay_output_lower:match('actual:%s*(%d+)%s+packets'))
            local successful_packets = tonumber(
                replay_output_lower:match('successful packets:%s*(%d+)')
            )
            if replay_output_lower:find('warning:', 1, true)
                    or failed_packets > 0 or truncated_packets > 0
                    or (actual_packets and actual_packets ~= 1)
                    or (successful_packets and successful_packets ~= 1) then
                replay_failure = "B60575 tcpreplay packet "
                    .. tostring(index) .. " was unsafe: "
                    .. displayable(packet_output, 300)
                break
            end
        end
        tcpreplay_output = table.concat(replay_outputs, '\n')
        write_text_file(tcpreplay_output_path, tcpreplay_output, '0600')
        if replay_failure then result = replay_failure break end
        -- a device may delay a unicast response for any time from zero through
        -- the requested MX value; the payload parser clamps MX to UPnP's 1..5
        response_wait_seconds = math.max(0, math.min(wait_seconds, capture_deadline - os.time()))
        if response_wait_seconds < wait_seconds then
            log_warning("B05332 UPnP limited to " .. tostring(response_wait_seconds) .. " seconds")
        end
        if response_wait_seconds > 0 and not sleep(response_wait_seconds) then
            result = "B08078 cannot wait for UPnP discovery responses"
            break
        end
        if not is_running(tcpdump_pid) then
            capture_status = read_text_file(capture_status_path, true, true)
            if (capture_packet_count(capture_status) or 0) >= max_upnp_capture_packets then
                log_warning("B70017 hit " .. tostring(max_upnp_capture_packets) .. " packet limit")
            elseif capture_time_limit_reached(capture_status) then
                log_warning("B24410 hit " .. tostring(max_upnp_capture_seconds) .. " second limit")
            else
                result = "B25280 tcpdump failed: " .. display_text_file(capture_status_path)
                break
            end
        elseif not kill_process(tcpdump_pid) then
            result = "B88176 cannot stop tcpdump: " .. display_text_file(capture_status_path)
            break
        end
        tcpdump_pid = nil
        capture_status = capture_status or read_text_file(capture_status_path, true, true)
        captured_packets = decode_upnp_capture(capture_path)
        if captured_packets == nil then
            result = "B25543 cannot decode tcpdump capture: " .. display_text_file(capture_status_path)
            break
        end
        -- verify every transmitted payload and the timing produced by the one-packet tcpreplay calls
        local capture_data = read_text_file(capture_path, true, true)
        local observed_probes, observed_problem, _, observed_record_count =
            extract_upnp_msearches(capture_data)
        if not observed_probes then
            result = "B21948 cannot inspect captured UPnP replay: " .. tostring(observed_problem)
            break
        end
        replay_capture_summary = upnp_probe_summary(
            observed_probes,
            observed_record_count
        )
        if #observed_probes ~= #probe_packets then
            local output = run_command('LC_ALL=C tcpreplay --version', true)
            local version = nil
            if type(output) == 'string' then
                version = output:match('version:?%s+([0-9a-zA-Z:%.%-]+)')
            end
            local o_of_p = tostring(#observed_probes) .. " of " .. tostring(#probe_packets)
            local version_string = "tcpreplay " .. (version or 'unknown')
            if version == '4.5.2' then
                -- this is likely a bug in tcpreplay 4.5.2; note "TX_RING was silently dropping
                -- and reordering packets" on https://github.com/appneta/tcpreplay/releases/tag/v4.6.0
                result = "B71304 capture contains " .. o_of_p .. " packets; " .. version_string
            else
                result = "B19861 capture contains " .. o_of_p .. " packets; " .. version_string
            end
            break
        end
        local timing_differences = {}
        local replay_payload_problem = nil
        for index, observed in ipairs(observed_probes) do
            if observed.payload ~= probe_packets[index].payload then
                replay_payload_problem = tostring(index)
                break
            end
            if index > 1 then
                local expected_gap = probe_packets[index].delay_us - probe_packets[index - 1].delay_us
                local observed_gap = observed.delay_us - observed_probes[index - 1].delay_us
                if math.abs(observed_gap - expected_gap) > 500000 then
                    timing_differences[#timing_differences + 1] = 'packet '
                        .. tostring(index) .. ' gap was '
                        .. string.format('%.6f', observed_gap / 1000000)
                        .. ' seconds; expected '
                        .. string.format('%.6f', expected_gap / 1000000)
                        .. ' seconds'
                end
            end
        end
        if replay_payload_problem then
            result = "B40681 captured replay payload differs at packet " .. replay_payload_problem
            break
        end
        if #timing_differences > 0 then
            replay_timing_problem = 'captured replay timing differed: '
                .. table.concat(timing_differences, '; ')
        end
        local location_candidates = {}
        for line in captured_packets:gmatch('[^\r\n]+') do
            local location = line:match('^[Ll][Oo][Cc][Aa][Tt][Ii][Oo][Nn]:%s*(%S+)')
            if location then
                location_candidates[#location_candidates + 1] = location
            end
        end
        local locations, upnp_response_count = filter_upnp_locations(
            location_candidates,
            gateway,
            source_ip,
            source_prefix
        )
        local igds, descriptions_read = read_upnp_igds(
            locations,
            gateway,
            source_ip,
            source_prefix
        )
        if #locations > 0 and descriptions_read == 0 then
            result = "B60567 cannot read any UPnP device description"
            break
        end
        if #locations == 0 then
            local wan_capture_drops = capture_interface_drops(capture_status)
            if wan_capture_drops > 0 then
                diagnostic_reason = "no SSDP LOCATION response; WAN capture reported "
                    .. tostring(wan_capture_drops) .. " interface drops"
                result = "B74816 " .. diagnostic_reason
                break
            end
            if upnp_response_count > 0 then
                diagnostic_reason = "no UPnP LOCATION response matched the gateway/LAN subnet"
            else
                diagnostic_reason = "no SSDP LOCATION response was captured after "
                    .. tostring(#probe_packets) .. " timed M-SEARCH packets"
            end
        elseif #igds == 0 then
            diagnostic_reason = "no WANIPConnection/WANPPPConnection service in SSDP responses"
        end
        if replay_timing_problem then
            diagnostic_reason = diagnostic_reason
                and (diagnostic_reason .. "; " .. replay_timing_problem)
                or replay_timing_problem
        end
        success = true
        result = upnp_result_json(gateway, 'pcap', upnp_response_count, igds)
    until true
    if tcpdump_pid and not kill_process(tcpdump_pid) then
        log_warning("B38790 cleanup could not stop tcpdump")
    end
    tcpdump_pid = nil
    if capture_started and capture_status == nil and capture_status_path then
        capture_status = read_text_file(capture_status_path, true, true)
    end
    if capture_started and captured_packets == nil and capture_path then
        captured_packets = decode_upnp_capture(capture_path)
    end
    -- keep normal discovery quiet; on a failure or an empty discovery, retain
    -- the evidence that distinguishes bad task data, replay trouble, routing,
    -- and a LAN that simply did not answer
    if not success or diagnostic_reason then
        log_info('UPnP diagnostic triggered: ' .. displayable(diagnostic_reason or result, 700))
        if probe_packets then
            log_info(
                'UPnP diagnostic received probe packets: '
                    .. upnp_probe_summary(probe_packets, probe_record_count)
            )
        end
        log_upnp_diagnostic('default route', wan_route)
        log_upnp_diagnostic('source route', source_route)
        if source_ip or source_mac or source_port then
            log_info(
                'UPnP diagnostic selected source: '
                    .. tostring(source_mac or '(unknown MAC)') .. ' '
                    .. tostring(source_ip or '(unknown IP)') .. ':'
                    .. tostring(source_port or '(unknown port)')
                    .. ' on ' .. tostring(wan_if or '(unknown interface)')
            )
        end
        if wait_seconds then
            log_info(
                'UPnP diagnostic response wait: ' .. tostring(wait_seconds)
                    .. ' seconds (captured MX=' .. tostring(probe_mx) .. ')'
            )
        end
        if replay_capture_summary then
            log_info( 'UPnP diagnostic observed replay packets: ' .. replay_capture_summary)
        end
        if tcpreplay_output ~= nil then
            log_upnp_diagnostic(
                'tcpreplay output',
                tcpreplay_output or read_text_file(tcpreplay_output_path, true, true),
                80
            )
        end
        if capture_started and capture_path then
            log_upnp_diagnostic(
                'WAN tcpdump packets',
                captured_packets or decode_upnp_capture(capture_path),
                120
            )
        end
        if capture_started and capture_status_path then
            log_upnp_diagnostic(
                'WAN tcpdump status',
                capture_status or read_text_file(capture_status_path, true, true)
            )
        end
    end
    remove_paths(encoded_path, pcap_path)
    for _, packet_path in ipairs(replay_packet_paths) do remove_path(packet_path) end
    remove_paths(capture_path, capture_status_path, tcpreplay_output_path)
    return success, result
end

local function parse_miniupnpc_output(output)
    local first_line = type(output) == 'string' and output:match('[^\r\n]+') or nil
    if type(output) ~= 'string' then
        return nil, "B90429 invalid miniupnpc header: " .. tostring(first_line or '(empty)')
    end
    output = output:gsub('\r\n', '\n'):gsub('\r', '\n')
    if not output:lower():find('miniupnpc', 1, true) then
        return nil, "B20353 invalid miniupnpc header: " .. tostring(first_line or '(empty)')
    end
    local locations = {}
    local control_url = nil
    local no_valid_igd = false
    for line in (output .. '\n'):gmatch('(.-)\n') do
        local trimmed = line:match('^%s*(.-)%s*$')
        local lower_line = trimmed:lower()
        if lower_line:find('no igd upnp device found on the network', 1, true)
                or lower_line:find('no valid upnp internet gateway device found', 1, true) then
            no_valid_igd = true
        end
        local label, value = trimmed:match('^([^:]+):%s*(%S+)%s*$')
        if label then
            label = label:match('^%s*(.-)%s*$'):lower()
            if label == 'desc' then
                locations[#locations + 1] = value
            elseif label == 'found valid igd'
                    or label:match('^found an igd with a reserved ip address%s*%b()$') then
                if #value > 2048 or value:find('[%c%s]')
                        or not value:match('^https?://')
                        or not http_url_host(value) then
                    return nil, "B81290 invalid miniupnpc output: invalid control URL"
                end
                if control_url and control_url ~= value then
                    return nil, "B67840 invalid miniupnpc output: multiple valid IGDs"
                end
                control_url = value
            end
        end
    end
    if control_url and no_valid_igd then
        return nil, "B61412 invalid miniupnpc output: contradictory IGD result"
    end
    if not control_url and #locations == 0 and not no_valid_igd then
        return nil, "B96940 invalid miniupnpc output: missing IGD result"
    end
    if control_url and #locations == 0 then
        return nil,
            "B73862 invalid miniupnpc output: valid IGD has no description URL"
    end
    return { locations = locations, control_url = control_url, no_valid_igd = no_valid_igd, }
end

local function discover_upnp_miniupnpc()
    if not install_one_of('miniupnpc', 'upnpc') then
        return nil, "B87103 cannot install miniupnpc discovery dependency"
    end
    if not install_one_of('iproute2 iproute ip-tiny ip-full', 'ip') then
        return nil, "B27341 cannot install miniupnpc route dependency"
    end
    local route_info, route_problem = get_upnp_route_info()
    if route_problem then return nil, route_problem end
    local gateway = route_info.gateway
    local source_ip = route_info.source_ip
    local source_prefix = route_info.source_prefix
    -- upnpc may fetch advertised descriptions while selecting an IGD, before
    -- Lua can filter those URLs; bind it to the selected LAN address, ignore
    -- its selected control URL, then independently filter and read the printed
    -- description URLs through the bounded common path below; note that
    -- some platforms that have 'upnpc' do not have 'upnp-listdevices'
    local status_marker = '__BBBASED_UPNPC_EXIT__='
    local command = '(LC_ALL=C upnpc -m ' .. shell_quote(source_ip)
        .. ' -P; upnpc_status=$?; '
        .. 'printf "\\n' .. status_marker .. '%s\\n" '
        .. '"$upnpc_status"; exit 0)'
    local output = run_command(command, true, true)
    if output == nil then
        return nil, "B37602 cannot run upnpc -P"
    end
    local exit_code = tonumber(output:match('\n' .. status_marker .. '(%d+)$'))
    if not exit_code then
        return nil, "B39609 cannot determine upnpc -P exit status"
    end
    output = output:gsub('\n' .. status_marker .. '%d+$', '')
    local parsed, problem = parse_miniupnpc_output(output)
    if not parsed then return nil, problem end
    if exit_code ~= 0 and not parsed.no_valid_igd then
        return nil, "B56290 upnpc -P exited with status "
            .. tostring(exit_code)
    end
    local locations, response_count = filter_upnp_locations(
        parsed.locations,
        gateway,
        source_ip,
        source_prefix
    )
    local igds, descriptions_read = read_upnp_igds(
        locations,
        gateway,
        source_ip,
        source_prefix
    )
    if #locations > 0 and descriptions_read == 0 then
        return nil, "B10450 cannot read any UPnP device description"
    end
    local result = upnp_result_json(gateway, 'miniupnpc', response_count, igds)
    return true, result, #igds > 0
end

local function discover_upnp(pcap_base64)
    local ok, output, found_igd = discover_upnp_miniupnpc()
    if ok and found_igd then return ok, output end
    return discover_upnp_pcap(pcap_base64)
end

local function rewrite_service_runner(lua_path)
    local result = nil
    if platform == 'init' then
        local init_path = '/etc/init.d/' .. bbsubd
        result = install_service_file(init_path, init_service_text(lua_path), '0755')
    else
        local start_script_path = '/usr/local/sbin/' .. bbsubd .. '-start.sh'
        local start_text = systemd_service_texts(lua_path)
        result = install_service_file(start_script_path, start_text, '0755')
    end
    if result == nil then return nil end
    return true
end

local function handle_task(task_id, task_method, task_args)
    -- return true iff task was handled or no task was present
    if not task_id or not task_method then return true end
    log_info("received task " .. task_method .. " id=" .. task_id)
    if task_method == 'no_op' then
        return send_task_result(task_id, task_method, true, 'ok')
    end
    if task_method == 'update' then
        local staged_path = task_args and json_get_string(task_args, 'path') or nil
        if staged_path ~= 'hub/bbbased.lua' then
            return send_task_result(task_id, task_method, false,
                "B63317 invalid update path: " .. tostring(staged_path))
        end
        local next_ver = task_args and json_get_string(task_args, 'version') or nil
        if not next_ver or #next_ver ~= 7 or not next_ver:match('^[0-9a-z]+$') then
            return send_task_result(task_id, task_method, false,
                "B42164 invalid next version: " .. tostring(next_ver))
        end
        local running_path = arg and arg[0]
        if not running_path or not running_path:match('^/') then
            return send_task_result(task_id, task_method, false,
                "B09761 invalid arg[0]: " .. tostring(running_path))
        end
        local staged_path = make_temp_path(dirname(running_path))
        if not staged_path then
            return send_task_result(task_id, task_method, false, "B19042 cannot create temp file")
        end
        local command = 'curl -f --max-time 120 -o '
            .. shell_quote(staged_path) .. ' '
            .. shell_quote(download_url)
        if not run_command(command) then
            remove_path(staged_path)
            return send_task_result(task_id, task_method, false, "B18136 download failed")
        end
        local parse_attempt = 'STAGED_PATH=' .. shell_quote(staged_path) .. ' /usr/bin/lua -e '
            .. shell_quote('assert(loadfile(os.getenv("STAGED_PATH")))')
        local staged_code = read_text_file(staged_path, false, true)
        local new_commit_date = staged_code:match("\nlocal[ \t]+commit_date[ \t]*=[ \t]*'([^']+)'")
        local invalid_reason =
            not staged_code and 'unreadable'
            or #staged_code < 1000 and 'too short'
            or staged_code:sub(1, 18) ~= '#!/usr/bin/lua\n\n--' and 'wrong header'
            or new_commit_date < commit_date and 'downgrade'
            or not run_command(parse_attempt, true, true) and 'parse failed'
        if invalid_reason then
            remove_path(staged_path)
            return send_task_result(task_id, task_method, false,
                "B77812 bad download (" .. invalid_reason .. ")")
        end
        -- '0tkuo3w' is bridge release to using HUBCONF environment variable
        if commit_date == '0tkuo3w' and not rewrite_service_runner(running_path) then
            remove_path(staged_path)
            return send_task_result(task_id, task_method, false, "B56227 cannot rewrite service runner")
        end
        local task_data = task_id .. '\n' .. task_method .. '\n'
        if not write_text_file(deploy_result_path, task_data, '0600') then
            remove_path(staged_path)
            return send_task_result(task_id, task_method, false, "B33348 cannot save update state")
        end
        chmod(staged_path, get_mode(running_path))  -- ignore errors, but they do get logged
        if not run_command('mv ' .. shell_quote(staged_path) .. ' ' .. shell_quote(running_path)) then
            remove_paths(staged_path, deploy_result_path)
            return send_task_result(task_id, task_method, false, "B57225 mv failed")
        end
        log_info("update installed; restarting")
        restart_after_update()
    end
    if task_method == 'discover_upnp' then
        local pcap_base64 = task_args and json_get_string(task_args, 'pcap') or nil
        local ok, output = discover_upnp(pcap_base64)
        return send_task_result(task_id, task_method, ok, output)
    end
    if task_method == 'df' then
        local df_args = task_args
        if task_args and task_args:sub(1, 1) == '{' then
            df_args = json_get_string(task_args, 'args')
        end
        if df_args ~= '-hT' then
            return send_task_result(task_id, task_method, false, 'unsupported df args')
        end
        local output = run_command('df ' .. df_args, true, true)
        if not output then
            return send_task_result(task_id, task_method, false, 'df command failed')
        end
        return send_task_result(task_id, task_method, true, output)
    end
    return send_task_result(task_id, task_method, false, 'unsupported task_method')
end

local function do_ping()
    -- return the status response, or nil on failure
    log_debug("starting ping cycle")
    local request_body = build_ping_request()
    if not request_body then return nil end
    local response_body = send_signed_jsonrpc(request_body)
    if not response_body then return nil end
    log_debug("ping response body: " .. displayable(response_body, 300))
    local has_jsonrpc = response_body:match('"jsonrpc"%s*:%s*"2%.0"') ~= nil
    local has_error = response_body:match('"error"%s*:') ~= nil
    local status = json_get_string(response_body, 'status')
    if has_jsonrpc and not has_error and status == 'ok' then
        log_info("ping succeeded with status " .. displayable(status, 60))
        local task_args = json_get_string(response_body, 'task_args')
        if task_args == nil then
            task_args = json_get_object(response_body, 'task_args')
        end
        if not handle_task(
            json_get_string(response_body, 'task_id'),
            json_get_string(response_body, 'task_method'),
            task_args
        ) then
            return nil
        end
        return status
    end
    -- note the Berror code below is used in api.py
    log_error("B64445 ping rejected: " .. displayable(response_body, 300))
    return nil
end

--
-- install or reinstall as a service and exit
--

if get_uid() ~= 0 then
    log_error("B97106 must run as root")
    close_log()
    os.exit(2)
end
if cli_verb == 'install' then
    if not set_sleep_method() then close_log() os.exit(13) end
    -- flock is the only 'install' prerequisite, but it's probably already installed
    install_one_of('flock util-linux', 'flock')  -- ignore errors and hope it works anyhow
    local install_dir = find_install_dir()
    local install_path = install_dir and (install_dir .. bbsubd .. '.lua') or nil
    local exit_code = 99
    if install_path and install_daemon_service(install_path) then
        log_info("B32020 successfully installed; exiting")
        exit_code = 0
    else
        log_error("B21488 cannot install as a service")
        exit_code = 4
    end
    close_log()
    os.exit(exit_code)
end
if cli_verb ~= 'daemonize' then log_error("B38333 cli_verb == " .. cli_verb) os.exit(1) end

--
-- make sure prerequisites are installed
--

log_warning("B20392 BitBurrow base daemon, log level " .. logging_level
    .. ", version " .. file_version)
if not set_sleep_method() then cleanup_and_exit(13) end
install_one_of('curl', 'curl')
install_one_of('openssl openssl-util', 'openssl')

--
-- collect authentication details
--

mkdir(config_dir, '0700')
if not ensure_auth_keys() then
    log_error("B60585 cannot continue without key files; exiting")
    cleanup_and_exit(5)
end

--
-- register with hub
--

local adopt6c_result = do_adopt6c()
if adopt6c_result == nil or adopt6c_result == true then  -- fatal error or success
    delete_adopt5c_code('/etc/rc.local')
    remove_path(ott_path)
end
if adopt6c_result == nil then  -- fatal error, no point in retrying
    log_error("B36017 cannot continue with uploading keys")
    cleanup_and_exit(6)
end

--
-- loop forever
--

local retry_wait = 7
local retries_left = 2
log_info("entering main ping loop")
while true do
    if not send_deploy_result() then
        log_error("B35355 cannot send deploy results")
    end
    local ok = do_ping()
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
