-- UPnP discovery uses the daemon's shared helpers
local base = ...
local display_text_file = base.display_text_file
local displayable = base.displayable
local download_file = base.download_file
local install_one_of = base.install_one_of
local json = base.json
local log_debug = base.log_debug
local log_info = base.log_info
local log_warning = base.log_warning
local make_temp_path = base.make_temp_path
local normalize_pid = base.normalize_pid
local read_text_file = base.read_text_file
local remove_path = base.remove_path
local remove_paths = base.remove_paths
local run_command = base.run_command
local run_command_for = base.run_command_for
local shell_quote = base.shell_quote
local sleep = base.sleep
local trim = base.trim
local write_text_file = base.write_text_file

local function xml_unescape(value)
    if not value then return nil end
    value = value:gsub('&#x([0-9A-Fa-f]+);', function(n)
        n = tonumber(n, 16)
        return n and n >= 0 and n < 256 and string.char(n) or ''
    end)
    value = value:gsub('&#([0-9]+);', function(n)
        n = tonumber(n, 10)
        return n and n >= 0 and n < 256 and string.char(n) or ''
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
    reference = trim(reference)
    if #reference > 2048 then return nil end
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
    local function element(text, tag, offset)
        local first = text:find('<' .. tag .. '[%s>]', offset)
        local open_end = first and text:find('>', first, true)
        if not open_end then return nil end
        local close, last = text:find('</' .. tag .. '%s*>', open_end + 1)
        if not close then return nil end
        return text:sub(open_end + 1, close - 1), last + 1
    end
    local url_base = element(xml, 'URLBase')
    if url_base then url_base = trim(url_base) end
    if url_base and #url_base > 2048 then return nil end
    local base_url = xml_unescape(url_base) or location
    local service_names = {'WANIPConnection', 'WANPPPConnection'}
    for _, service_name in ipairs(service_names) do
        local offset = 1
        while true do
            local service
            service, offset = element(xml, 'service', offset)
            if not service then break end
            local service_type = element(service, 'serviceType')
            if service_type and service_type:find(
                        'urn:schemas%-upnp%-org:service:' .. service_name .. ':%d+',
                        1
                    ) then
                local control_url = element(service, 'controlURL')
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
    if address ~= table.concat(octets, '.') then return nil end
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
        if address == info.source_ip then
            info.source_prefix = tonumber(prefix)
            break
        end
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

local function filter_upnp_locations(candidates, gateway, source_ip, source_prefix)
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
                    local message = "B14803 dropping UPnP LOCATION outside gateway/LAN subnet; host="
                            .. displayable(json.quotestring(location_host or '(invalid)'), 100)
                    log_info(message)
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
    local curl_command = 'curl -q -fsS --globoff --noproxy "*" --connect-timeout '
            .. tostring(connect_timeout) .. ' --max-time '
            .. tostring(request_timeout) .. ' ' .. shell_quote(location)
    local control_url = nil
    local description_read = false
    if download_file(curl_command, xml_path, 1048576, true) then
        local xml = read_text_file(xml_path, false, true)
        if xml then
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
                    local message = "B24547 dropping UPnP control URL outside "
                            .. "gateway/LAN subnet or with invalid authority; host="
                            .. displayable(json.quotestring(control_host or '(invalid)'), 100)
                    log_info(message)
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
        if control_url and not seen_control_urls[control_url] then
            seen_control_urls[control_url] = true
            igds[#igds + 1] = {location = location, control_url = control_url,}
            local _, matches_gateway = upnp_url_policy(location, gateway, source_ip, source_prefix)
            if matches_gateway then break end  -- we found a gateway IGD
        end
    end
    return igds, descriptions_read
end

local function upnp_result_json(gateway, method, response_count, igds)
    local result = {gateway = gateway, method = method, response_count = response_count, igds = {}}
    local result_bytes = #json.encode(result)
    for _, igd in ipairs(igds) do
        local entry = {location = igd.location, control_url = igd.control_url}
        local entry_bytes = #json.encode(entry)
        local separator_bytes = #result.igds > 0 and 1 or 0
        if result_bytes + separator_bytes + entry_bytes > max_upnp_result_bytes then
            log_warning("B75089 UPnP discovery result size limit reached; ignoring additional IGDs")
            break
        end
        result.igds[#result.igds + 1] = entry
        result_bytes = result_bytes + separator_bytes + entry_bytes
    end
    return json.encode(result)
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
    return {little_endian = little_endian, nanosecond = nanosecond, linktype = network % 65536,}
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
            local st = payload:match('[\r\n][Ss][Tt]:[ \t]*([^\r\n]+)')
            st = st and st:gsub('[ \t]+$', '')
            if expected_host and expected_man and st and #st <= 256
                    and st:match('^[%w:._%-]+$') then
                local mx = tonumber(upper:match('[\r\n]MX:%s*(%d+)')) or 3
                mx = math.max(1, math.min(mx, 5))
                -- to limit what a compromised hub is able to do, don't replay extra hub-supplied
                -- headers, such as LOCATION, or an unbounded MX
                payload = table.concat({
                    marker, 'HOST: 239.255.255.250:1900', 'MAN: "ssdp:discover"',
                    'MX: ' .. tostring(mx), 'ST: ' .. st, '', '',
                }, '\r\n')
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
            if not payload or mx == nil then
                return nil, 'record ' .. tostring(record_count) .. ': '
                        .. tostring(problem or 'missing MX value')
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

local function read_stat_fields(pid)
    -- strip the PID and parenthesized comm; field 22 then becomes ${20}
    return 'IFS= read -r stat 2>/dev/null </proc/' .. pid .. '/stat && set -- ${stat##*) }'
            .. ' && case ${20} in ""|*[!0-9]*) false;; *) true;; esac'
end

local function capture_command(capture, signal)
    local read_stat = read_stat_fields(capture.pid)
    local matches = '[ "$1" != Z ] && [ "${20}" = ' .. capture.start_time .. ' ]'
    if signal then
        return read_stat .. ' && ' .. matches .. ' && kill -' .. signal .. ' ' .. capture.pid
    end
    return 'if ' .. read_stat .. '; then ' .. matches .. '; else kill -0 ' .. capture.pid .. '; fi'
end

local function capture_running(capture)
    return run_command(capture_command(capture), false, true) ~= nil
end

local function stop_capture(capture)
    for _, signal in ipairs({'TERM', 'KILL'}) do
        run_command(capture_command(capture, signal), false, true)
        for _ = 1, 20 do
            if not capture_running(capture) then return true end
            sleep(0.1)
        end
    end
    return not capture_running(capture)
end

local function start_upnp_capture(interface, capture_path, status_path, filter)
    -- report launcher's identity before exec so an early exit cannot make us record a replacement process as the capture
    local command = read_stat_fields('$$') .. ' || exit 1; '
            .. 'printf "%s %s\\n" "$$" "${20}" || exit 1; '
            .. 'exec tcpdump -nn -U -s4096 -c ' .. tostring(max_upnp_capture_packets)
            .. ' -i ' .. shell_quote(interface) .. ' -w - ' .. shell_quote(filter)
            .. ' >' .. shell_quote(capture_path)
    local pipe = io.popen('sh -c ' .. shell_quote(command) .. ' 9>&- </dev/null 2>>'
        .. shell_quote(status_path) .. ' &', 'r')
    local identity = pipe and pipe:read('*l') or ''
    if pipe then pipe:close() end
    local pid, start_time = identity:match('^(%d+) (%d+)$')
    pid = normalize_pid(pid)
    if not pid then return nil, 'cannot start tcpdump: ' .. display_text_file(status_path) end
    local capture = {pid = pid, start_time = start_time}
    local watchdog_command = '(exec 9>&-; sleep ' .. tostring(max_upnp_capture_seconds)
            .. ' || exit 1; if ' .. capture_command(capture, '0') .. '; then printf "%s\\n" '
            .. shell_quote('upnp.lua: UPnP capture time limit reached') .. ' >>'
            .. shell_quote(status_path)
            .. '; ' .. capture_command(capture, 'TERM') .. '; fi) </dev/null >/dev/null 2>&1 & echo $!'
    local watchdog_pid = normalize_pid(run_command(watchdog_command, true, true))
    if not watchdog_pid then
        stop_capture(capture)
        return nil, 'cannot start tcpdump time-limit watchdog'
    end
    for _ = 1, 100 do
        local grep_command = 'grep -Eq '
                .. shell_quote('^(tcpdump: )?listening on ')
                .. ' ' .. shell_quote(status_path)
        if run_command(grep_command, true, true) then return capture end
        if not capture_running(capture) then break end
        sleep(0.1)
    end
    local problem
    if capture_running(capture) then
        problem = "tcpdump won't listen: " .. display_text_file(status_path)
        stop_capture(capture)
    else
        problem = 'tcpdump refused to listen: ' .. display_text_file(status_path)
    end
    return nil, problem
end

local function decode_upnp_capture(path, filter)
    return run_command('tcpdump -nn -e -vv -s0 -A -r ' .. shell_quote(path)
        .. (filter and ' ' .. shell_quote(filter) or ''), true, true)
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
    return tostring(status or ''):find('upnp.lua: UPnP capture time limit reached', 1, true) ~= nil
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
    local capture = nil
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
        if not pcap_base64 or pcap_base64 == '' then
            result = "B86322 missing pcap data"
            break
        end
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
        if not encoded_path or not pcap_path or not capture_path
                or not capture_status_path then
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
        if not pcap_data then
            result = "B60578 cannot read decoded UPnP probe PCAP"
            break
        end
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
        if route_problem then
            result = route_problem
            break
        end
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
        capture, capture_problem = start_upnp_capture(
            wan_if,
            capture_path,
            capture_status_path,
            capture_filter
        )
        if not capture then
            result = "B26445 " .. tostring(capture_problem)
            break
        end
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
            local remaining = math.min(max_upnp_capture_seconds, capture_deadline - os.time())
            if remaining <= 0 then
                replay_failure = "B53264 UPnP replay would exceed capture time limit"
                break
            end
            local replay = 'tcpreplay ' .. shell_quote('-i' .. wan_if) .. ' '
                    .. shell_quote(packet_path)
            local captured, problem, _, timed_out = run_command_for(replay, remaining, true, true)
            local packet_output = captured or problem or ''
            replay_outputs[#replay_outputs + 1] = 'packet ' .. tostring(index) .. ':\n' .. packet_output
            if not captured then
                replay_failure = "B92999 tcpreplay packet "
                        .. tostring(index) .. (timed_out and " timed out: " or " failed: ")
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
        if replay_failure then
            result = replay_failure
            break
        end
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
        if not capture_running(capture) then
            capture_status = read_text_file(capture_status_path, true, true)
            if (capture_packet_count(capture_status) or 0) >= max_upnp_capture_packets then
                log_warning("B70017 hit " .. tostring(max_upnp_capture_packets) .. " packet limit")
            elseif capture_time_limit_reached(capture_status) then
                log_warning("B24410 hit " .. tostring(max_upnp_capture_seconds) .. " second limit")
            else
                result = "B25280 tcpdump failed: " .. display_text_file(capture_status_path)
                break
            end
        elseif not stop_capture(capture) then
            result = "B88176 cannot stop tcpdump: " .. display_text_file(capture_status_path)
            break
        end
        capture = nil
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
        -- only inbound replies to our probe port may supply LOCATION; outgoing probes cannot;
        -- the UDP payload must start with "HTTP/1.1 200 " or "HTTP/1.0 200 "
        local response_filter = 'udp and dst host ' .. source_ip .. ' and dst port '
                .. tostring(source_port) .. ' and udp[8:4] = 0x48545450'
                .. ' and (udp[12:4] = 0x2f312e31 or udp[12:4] = 0x2f312e30)'
                .. ' and udp[16:4] = 0x20323030 and udp[20] = 32'
        local response_packets = decode_upnp_capture(capture_path, response_filter)
        if not response_packets then
            result = "B62384 cannot decode UPnP replies"
            break
        end
        local location_candidates = {}
        for line in response_packets:gmatch('[^\r\n]+') do
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
    if capture and not stop_capture(capture) then
        log_warning("B38790 cleanup could not stop tcpdump")
    end
    capture = nil
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
            log_info('UPnP diagnostic observed replay packets: ' .. replay_capture_summary)
        end
        if tcpreplay_output ~= nil then
            log_upnp_diagnostic(
                'tcpreplay output',
                tcpreplay_output,
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
    remove_paths(capture_path, capture_status_path)
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
        local trimmed = trim(line)
        local lower_line = trimmed:lower()
        if lower_line:find('no igd upnp device found on the network', 1, true)
                or lower_line:find('no valid upnp internet gateway device found', 1, true) then
            no_valid_igd = true
        end
        local label, value = trimmed:match('^([^:]+):%s*(%S+)%s*$')
        if label then
            label = trim(label):lower()
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
    return {locations = locations, control_url = control_url, no_valid_igd = no_valid_igd,}
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
    local command = 'LC_ALL=C upnpc -m ' .. shell_quote(source_ip) .. ' -P'
    local output, problem, exit_code, timed_out = run_command_for(command, 30, true, true)
    if timed_out then return nil, "B37602 upnpc -P timed out" end
    if not exit_code then return nil, "B39609 cannot run upnpc -P: " .. tostring(problem) end
    output = output or problem
    if #output > 18000 then return nil, "B76210 upnpc -P output exceeded the capture limit" end
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
    local pcap_ok, pcap_output = discover_upnp_pcap(pcap_base64)
    if not ok and not pcap_ok then
        return false, "miniupnpc: " .. tostring(output) .. "; PCAP: " .. tostring(pcap_output)
    end
    return pcap_ok, pcap_output
end

return discover_upnp
