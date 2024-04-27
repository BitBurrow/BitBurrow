"""Class PersistentWebsocket adds to WebSockets auto-reconnect and auto-resend of lost messages.

This class adds to WebSockets (client and server) the ability to automatically reconnect,
including for IP address changes, as well as resending any messages which may have been
lost. To accomplish this, it uses a custom protocol which adds 2 bytes to the beginning
of each WebSocket message and uses signals for acknowledgement and resend requests.
"""

import asyncio
import collections
import hub.logs as logs
import hub.net as net
import random
from timeit import default_timer as timer
import traceback
import typing
import websockets

try:
    from starlette.websockets import WebSocketDisconnect
except ImportError:
    from websockets.exceptions import ConnectionClosed as WebSocketDisconnect

### notes:
#   * important: mirror changes in corresponding Dart code--search "bMjZmLdFv"
#   * important: for breaking changes, increment rpc_ver
#   * messages always arrive and in order; signals can be lost if WebSocket disconnects
#   * all bytes are in big-endian byte order; use: int.from_bytes(chunk[0:2], 'big')
#   * 'chunk' refers to a full WebSocket item, including the first 2 bytes
#   * 'message' refers to chunk[2:] of a chunk that is not a signal (details below)
#   * index → chunk number; first chunk sent is chunk 0
#   * i_lsb → index mod max_lsb, i.e. the 14 least-significant bits
#   * jet_bit → specify channel; 0=RPC channel; 1=jet channel for TCP and other streams
#   * ping and pong (below) are barely implemented because we rely on WebSocket keep-alives
### on-the-wire format:
#   * chunk containing a message (chunk[0:2] in range 0..32767):
#       * chunk[0:2] bits 0..13 i_lsb
#       * chunk[0:2] bit 14     jet_bit
#       * chunk[0:2] bit 15     0
#       * chunk[2:]   message
#   * chunk containing a jet channel command (chunk[0:2] in range 49152..65535):
#       * chunk[0:2] bits 0..13 i_lsb
#       * chunk[0:2] bit 14     1
#       * chunk[0:2] bit 15     1
#       * chunk[2:]  command, e.g. 'forward_to 192.168.8.1:80' or 'disconnect'
#   * a signaling chunk (chunk[0:2] in range 32768..49151):
_sig_ack = 0x8010  # "I have received n total chunks"
#       * chunk[2:4]  i_lsb of next expected chunk
_sig_resend = 0x8011  # "Please resend chunk n and everything after it"
#       * chunk[2:4]  i_lsb of first chunk to resend
_sig_resend_error = 0x8012  # "I cannot resend the requested chunks"
#       * chunk[2:]   (optional, ignored)
_sig_ping = 0x8020  # "Are you alive?"
#       * chunk[2:]   (optional)
_sig_pong = 0x8021  # "Yes, I am alive."
#       * chunk[2:]   chunk[2:] from corresponding ping

max_lsb = 16384  # always 16384 (2**14) except for testing (tested 64, 32)
lsb_mask = 16383  # aka         0b0011111111111111
jet_bit = 16384  # bit 14,      0b0100000000000000
signal_bit = 32768  # bit 15,   0b1000000000000000
jet_cmd = 49152  # bits 14, 15, 0b1100000000000000
max_send_buffer = 100  # not sure what a reasonable number here would be
assert max_lsb > max_send_buffer * 3  # avoid wrap-around


def lsb(index, set_bit_mask: int = 0b0000000000000000) -> bytes:
    """Convert index to on-the-wire format; see i_lsb description."""
    return (index % max_lsb | set_bit_mask).to_bytes(2, 'big')


def const_otw(c) -> bytes:
    """Convert _sig constant to on-the-wire format."""
    assert c >= signal_bit
    assert c <= 65535  # 0xFFFF
    return c.to_bytes(2, 'big')


def unmod(xx, xxxx, w=max_lsb) -> int:
    """Undelete upper bits of xx by assuming it's near xxxx.

    Put another way: find n where n%w is xx and abs(xxxx-n) <= w/2. For
    example, unmod(yy, yyyy_today, 100) will convert a 2-digit year yy to
    a 4-digit year by assuming yy is within 50 years of the current year.
    The input w is the window size (must be even), i.e. the number of
    possible values for xx.
    """
    assert xx < w
    splitp = (xxxx + w // 2) % w  # split point
    return xx + xxxx + w // 2 - splitp - (w if xx > splitp else 0)


class Timekeeper:
    """Call a method after a specified number of seconds.

    Seconds can be fractional. Repeating timers are possible using periodic() or
    exponential(). For example usage, see test_timekeeper() in "tests/" directory."""

    # based on https://stackoverflow.com/a/45430833
    def __init__(self, timeout, callback, is_periodic=False, scaling=1.0, max_timeout=30.0) -> None:
        self._timeout = timeout
        self._callback = callback
        self._is_periodic = is_periodic
        self._scaling = scaling
        self._max_timeout = max_timeout
        self._task = asyncio.create_task(self._job())

    @staticmethod
    def periodic(timeout, callback) -> 'Timekeeper':
        return Timekeeper(timeout, callback, is_periodic=True, scaling=1.0, max_timeout=9999999)

    @staticmethod
    def exponential(timeout, callback, scaling=2.0, max_timeout=30.0) -> 'Timekeeper':
        return Timekeeper(
            timeout, callback, is_periodic=True, scaling=scaling, max_timeout=max_timeout
        )

    async def _job(self) -> None:
        while True:
            await asyncio.sleep(self._timeout)
            self._timeout *= self._scaling
            if self._timeout > self._max_timeout:
                self._timeout = self._max_timeout
                self._scaling = 1.0
            await self._callback()  # time spent in callback delays next callback
            if not self._is_periodic:
                break

    def cancel(self) -> None:
        self._is_periodic = False
        self._task.cancel()


class PWUnrecoverableError(Exception):
    def __init__(self, message="") -> None:
        self.message = message
        super().__init__(self.message)


lkocc_string = '__login_key_or_coupon_code__'


def printable_hex(chunk) -> str:
    """Make binary data more readable for humans."""
    out = list()
    quote = list()  # quoted ascii text
    for item in chunk:
        if 32 <= item <= 126 and item != 39:  # printable character, but not single quote
            quote.append(chr(item))
        else:  # non-printable character
            if quote:
                if len(quote) <= 3:  # isolated short strings remain as hex
                    out.extend([f"{ord(e):02X} " for e in quote])
                else:
                    out.append(f"'{''.join(quote)}' ")
                quote.clear()
            out.append(f"{item:02X} ")
    if quote:
        out.append(f"'{''.join(quote)}'")
    return ''.join(out).strip()


class PersistentWebsocket:
    """Adds to WebSockets auto-reconnect and auto-resend of lost messages.

    See the top of this file for details.
    """

    def __init__(self, log_id: str, log) -> None:
        self.log_id = log_id  # uniquely identify this connection in log file
        self.log = log
        self._ws = None
        self.starlette = False  # True meand using starlette.websockets
        self._url = None
        self._in_index = 0  # index of next inbound chunk, aka number of chunks received
        self._in_last_ack = 0  # index of most recently-sent _sig_ack
        self._in_last_ack_timer = None  # count-down timer to send _sig_ack
        self._in_last_resend = 0  # index of most recently-sent _sig_resend
        self._in_last_resend_time = 0  # time that most recent _sig_resend was sent
        # https://docs.python.org/3/library/collections.html#collections.deque
        self._journal = collections.deque()  # chunks sent but not yet confirmed by remote
        self._journal_index = 0  # index of the next outbound chunk, ...
        self._journal_timer = None  # count-down timer to resend _journal
        # aka index + 1 of right end (newest) of _journal
        self.connects = 0
        self.chaos = 0  # level of chaos to intentionaly introduce for testing, 50 recommended
        self.connect_lock = asyncio.Lock()
        self._tcp_connect = TcpConnector(self)

    async def connected(self, ws) -> typing.AsyncGenerator[bytes, None]:
        """Handle a new inbound WebSocket connection, yield inbound messages.

        This is the primary API entry point for a WebSocket SERVER. Signals from
        the client will be appropriately handled and inbound messages will be
        returned to the caller via `yield`.
        """
        try:
            if self.connect_lock.locked():
                self.log.warning(f"B30102 {self.log_id} waiting for current WebSocket to close")
                # PersistentWebsocket is not reentrant; if we don't lock here, messages
                # can arrive out-of-order
            async with self.connect_lock:
                self._url = None  # make it clear we are now a server
                self.starlette = hasattr(ws, 'send_bytes')
                self.set_online_mode(ws)
                async for m in self.listen():
                    yield m
        except PWUnrecoverableError:
            raise  # needs to be handled
        finally:
            await self.set_offline_mode()

    async def connect(self, url: str) -> typing.AsyncGenerator[bytes, None]:
        """Begin a new outbound WebSocket connection, yield inbound messages.

        This is the primary API entry point for a WebSocket CLIENT. Signals from
        the server will be appropriately handled and inbound messages will be
        returned to the caller via `yield`.
        """
        if self.connect_lock.locked():
            self.log.warning(f"B18449 {self.log_id} waiting for current WebSocket to close")
            # PersistentWebsocket is not reentrant; if we don't lock here, messages
            # can arrive out-of-order
        async with self.connect_lock:
            self._url = url
            try:
                # https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html
                self.log.debug(f"B35536 {self.log_id} waiting for WebSocket to connect")
                self.starlette = False
                async for ws in websockets.connect(self._url):  # keep reconnecting
                    self.set_online_mode(ws)
                    async for m in self.listen():
                        yield m
            except asyncio.exceptions.CancelledError:  # ctrl-C
                self.log.warning(f"B32045 {self.log_id} WebSocket canceled")
            except PWUnrecoverableError:
                raise  # needs to be handled
            except Exception:
                self.log.error(
                    f"B34752 {self.log_id} wsexception, {traceback.format_exc().rstrip()}"
                )
            finally:
                await self.set_offline_mode()

    async def listen(self) -> typing.AsyncGenerator[bytes, None]:
        """Accept chunks on the WebSocket connection and yield messages."""
        self._in_last_resend_time = 0  # reset for new connection
        await self._send_resend()  # chunks were probably lost in the reconnect
        try:
            async for chunk in self._ws.iter_bytes() if self.starlette else self._ws:
                self.log.debug("B18042 %s received: %r", self.log_id, logs.r(printable_hex, chunk))
                message = await self.process_inbound(chunk)
                if self.chaos > 0 and self.chaos > random.randint(0, 999):
                    self.log.warning(
                        f"B66740 {self.log_id} randomly closing WebSocket to test recovery"
                    )
                    await asyncio.sleep(random.randint(0, 2))
                    await self.set_offline_mode()
                    await asyncio.sleep(random.randint(0, 2))
                if message is not None:
                    yield message
            self.log.info(f"B99953 {self.log_id} WebSocket closed")
        except websockets.ConnectionClosed:
            self.log.warning(f"B60441 {self.log_id} WebSocket closed")
        except asyncio.exceptions.CancelledError:  # ctrl-C
            raise asyncio.exceptions.CancelledError
        except WebSocketDisconnect as e:
            if e == 1000:  # WS_1000_NORMAL_CLOSURE in starlette/status.py
                self.log.info(f"B94731 {self.log_id} WebSocket closed by remote")
            elif e == 1012:  # WS_1012_SERVICE_RESTART in starlette/status.py
                raise asyncio.exceptions.CancelledError
            else:
                self.log.error(f"B53771 {self.log_id} exception, {traceback.format_exc().rstrip()}")
        except RuntimeError as e:
            if e.args[0] != 'WebSocket is not connected. Need to call "accept" first.':
                # ignore 'not connected' because it's a result of intentional testing; see B66740
                self.log.error(f"B81148 {self.log_id} exception, {traceback.format_exc().rstrip()}")
        except PWUnrecoverableError:
            raise  # propagate out
        except AttributeError:
            if self.is_online():  # ignore if it is because WebSocket closed
                self.log.warning(
                    f"B26471 {self.log_id} wsexception, {traceback.format_exc().rstrip()}"
                )
        except Exception:
            self.log.error(f"B59584 {self.log_id} wsexception, {traceback.format_exc().rstrip()}")
        finally:
            await self.set_offline_mode()

    async def send(self, message: str | bytes, set_bit_mask: int = 0b0000000000000000) -> None:
        """Send a message to the remote when possible, resending if necessary.

        To send on jet channel, use a set_bit_mask of jet_bit."""
        flow_control_delay = 1
        while len(self._journal) > max_send_buffer:
            if flow_control_delay == 1:
                self.log.info(f"B60013 {self.log_id} outbound buffer is full--waiting")
            await asyncio.sleep(flow_control_delay)
            if flow_control_delay < 30:
                flow_control_delay += 1
        if flow_control_delay > 1:
            self.log.debug(f"B64414 {self.log_id} resuming send")
        if isinstance(message, str):  # convert message to bytes
            chunk = lsb(self._journal_index, set_bit_mask) + message.encode()
        else:  # message is already in bytes
            chunk = lsb(self._journal_index, set_bit_mask) + message
        self._journal_index += 1
        self._journal.append(chunk)
        await self._send_raw(chunk)
        self.enable_journal_timer()
        if self.chaos > 0 and self.chaos > random.randint(0, 999):
            self.log.warning(f"B14263 {self.log_id} randomly closing WebSocket to test recovery")
            await asyncio.sleep(random.randint(0, 3))
            await self.set_offline_mode()
            await asyncio.sleep(random.randint(0, 3))

    async def jet_send(self, message: str | bytes) -> None:
        """Same as 'send()', but sends on the jet channel."""
        # if len(message) > 90:
        if False:
            await self.send(message[0:-1] + b'b', jet_bit)
        else:
            await self.send(message, jet_bit)

    async def _resend_one(self) -> None:
        """Resend the oldest chunk."""
        journal_len = len(self._journal)
        if journal_len > 0:
            # sending all chunks now may cause congestion, and we should get a
            # _sig_resend upon reconnect anyhow
            tail_index = self._journal_index - journal_len
            await self._resend(tail_index, tail_index + 1)

    async def _resend(self, start_index, end_index=None) -> None:
        """Resend queued chunks."""
        if end_index is None:
            end_index = self._journal_index
        if start_index == end_index:
            return
        tail_index = self._journal_index - len(self._journal)
        if end_index < start_index or start_index < tail_index:
            self.log.error(
                f"B38394 {self.log_id} remote wants journal[{start_index}:{end_index}] "
                + f"but we only have journal[{tail_index}:{self._journal_index}]"
            )
            await self._send_raw(const_otw(_sig_resend_error))
            raise PWUnrecoverableError(f"B34922 {self.log_id} impossible resend request")
        self.log.info(f"B57684 {self.log_id} resending journal[{start_index}:{end_index}]")
        # send requested chunks from oldest to newest, e.g. range(-2, 0) for most recent 2 chunks
        for i in range(start_index - self._journal_index, end_index - self._journal_index):
            await self._send_raw(self._journal[i])

    async def _send_raw(self, chunk: bytes) -> None:
        """Send chunk of bytes if we can."""
        if self.is_offline():
            return
        try:
            if self.starlette:
                # https://www.starlette.io/websockets/#sending-data
                await self._ws.send_bytes(chunk)
            else:
                # https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html#websockets.client.WebSocketClientProtocol.send
                await self._ws.send(chunk)
            self.log.debug("B41789 %s sent: %r", self.log_id, logs.r(printable_hex, chunk))
        except (
            WebSocketDisconnect,
            websockets.exceptions.ConnectionClosedError,
            websockets.exceptions.ConnectionClosedOK,
            RuntimeError,  # probably: Cannot call "send" once a close message has been sent.
        ):
            self.log.info(f"B44793 {self.log_id} WebSocket disconnect")
            await self.set_offline_mode()
        except PWUnrecoverableError:
            raise
        except Exception:
            # unhandled exceptions must not propagate out; we need to yield all messages
            self.log.error(f"B42563 {self.log_id} wsexception, {traceback.format_exc().rstrip()}")

    async def process_inbound(self, chunk) -> bytes | None:
        """Test and respond to chunk, returning a message or None."""
        if hasattr(self, '_ipi'):
            # connect_lock should prevent this, but we check to be safe; this
            # happens if WebSocket is closed during send() within process_inbound()
            # and causes messages to be delivered out of order
            self.log.error(f"B14725 {self.log_id} process_inbound is not reentrant")
            await asyncio.sleep(1)  # avoid uninterruptible loop
            return
        self._ipi = True  # see above
        try:
            i_lsb = int.from_bytes(chunk[0:2], 'big')  # first 2 bytes of chunk
            is_jet = i_lsb & jet_bit != 0
            if i_lsb < signal_bit or i_lsb >= jet_cmd:  # message chunk or jet command
                index = unmod(
                    i_lsb & lsb_mask,  # i_lsb with jet bit cleared
                    self._in_index,
                )  # expand 14 bits to full index
                if index == self._in_index:  # valid
                    self._in_index += 1  # have unacknowledged message(s)
                    # occasionally call _send_ack() so remote can clear _journal
                    self.enable_in_timer()  # acknowledge receipt after 1 second
                    if self._in_index - self._in_last_ack >= 16:
                        # acknowledge receipt after 16 messages
                        await self._send_ack()
                        # (TESTING) await self.send(f"We've received {self._in_index} messages")  # TESTING
                    if is_jet:
                        if i_lsb >= jet_cmd:  # jet channel command
                            await self._process_jet_command(chunk[2:].decode())
                        else:  # jet channel data
                            self._tcp_connect.write(chunk[2:])
                    else:
                        return chunk[2:]  # message
                elif index > self._in_index:
                    await self._send_resend()  # request the other end resend what we're missing
                else:  # index < self._in_index
                    self.log.info(f"B73822 {self.log_id} ignoring duplicate chunk {index}")
            else:  # signal
                if i_lsb == _sig_ack or i_lsb == _sig_resend:
                    ack_index = unmod(int.from_bytes(chunk[2:4], 'big'), self._journal_index)
                    tail_index = self._journal_index - len(self._journal)
                    if tail_index < ack_index:
                        self.log.debug(
                            f"B60966 {self.log_id} clearing journal[{tail_index}:{ack_index}]"
                        )
                    if self._journal_timer is not None:
                        self._journal_timer.cancel()  # got ack; no need to resend
                        self._journal_timer = None
                    if len(self._journal) < (ack_index - tail_index):
                        self.log.error(
                            f"B19144 {self.log_id} error: "
                            + f"{len(self._journal)} < ({ack_index} - {tail_index})"
                        )
                        raise PWUnrecoverableError(f"B44311 {self.log_id} impossible ack")
                    for i in range(tail_index, ack_index):
                        self._journal.popleft()
                    self.enable_journal_timer()  # set a new timer for remainder of _journal
                    if i_lsb == _sig_resend:
                        await self._resend(ack_index)
                elif i_lsb == _sig_resend_error:
                    self.log.error(f"B75561 {self.log_id} received resend error signal")
                    raise PWUnrecoverableError(f"B91221 {self.log_id} received resend error signal")
                elif i_lsb == _sig_ping:
                    await self._send_raw(const_otw(_sig_pong) + chunk[2:])
                elif i_lsb == _sig_pong:
                    pass
                else:
                    self.log.error(f"B32405 {self.log_id} unknown signal {i_lsb}")
            return None
        except PWUnrecoverableError:
            raise
        except Exception:
            self.log.error(f"B88756 {self.log_id} wsexception, {traceback.format_exc().rstrip()}")
        finally:
            del self._ipi

    async def _send_ack(self) -> None:
        self._in_last_ack = self._in_index
        if self._in_last_ack_timer is not None:  # kill the count-down timer if running
            self._in_last_ack_timer.cancel()
            self._in_last_ack_timer = None
        await self._send_raw(const_otw(_sig_ack) + lsb(self._in_index))

    async def _send_resend(self) -> None:
        now_time = round(timer() * 1000)
        # wait a bit before sending a duplicate resend requets again
        if self._in_index == self._in_last_resend:
            if now_time - self._in_last_resend_time < 500:  # in milliseconds
                return
        self._in_last_resend = self._in_index
        self._in_last_resend_time = now_time
        await self._send_raw(const_otw(_sig_resend) + lsb(self._in_index))

    async def ping(self, data) -> None:
        await self._send_raw(const_otw(_sig_ping) + data)

    async def _process_jet_command(self, cmd: str) -> None:
        try:
            word, parms = cmd.split(' ', maxsplit=1)
        except ValueError:
            word = cmd
            parms = ''
        if word == 'forward_to':
            ip_address, port = net.parse_ip_port(parms)
            self.log.debug(
                f"B99176 {self.log_id} received forward_to command"
                + f"for {ip_address} port {port}"
            )
            # if we are a peer, open new TCP connection; else do nothing
            await self._tcp_connect.open_peer_connection(ip_address, port)
        elif word == "disconnect":
            self.log.debug(f"B50142 {self.log_id} received disconnect command")
            self._tcp_connect.close()
        else:
            self.log.warning(f"B67536 {self.log_id} unknown jet command: {cmd}")

    async def _send_tcp_connect(self, ip_address, port) -> None:
        await self.send(f'forward_to {net.format_ip_port(ip_address, port)}', jet_cmd)

    async def _send_tcp_disconnect(self) -> None:
        await self.send('disconnect', jet_cmd)

    def is_online(self) -> bool:
        return self._ws is not None

    def is_offline(self) -> bool:
        return self._ws is None

    async def set_offline_mode(self) -> None:
        """Close the WebSocket connection; can be called multiple times."""
        if self.is_offline():
            return
        try:
            await self._ws.close()
            self.log.info(f"B89445 {self.log_id} WebSocket closed")
        except RuntimeError as e:
            # probably websocket close after close
            self.log.debug(f"B79020 {self.log_id} WebSocket error {e}")
        except Exception:
            self.log.error(f"B39425 {self.log_id} wsexception, {traceback.format_exc().rstrip()}")
        finally:
            self._ws = None
            if self._journal_timer is not None:
                self._journal_timer.cancel()
                self._journal_timer = None
            if self._in_last_ack_timer is not None:
                self._in_last_ack_timer.cancel()
                self._in_last_ack_timer = None

    def set_online_mode(self, ws) -> None:
        assert self.is_offline(), f"B39653 {self.log_id} cannot go online twice"
        self._ws = ws
        self.log.info(f"B17183 {self.log_id} WebSocket reconnect {self.connects}")
        self.connects += 1
        self.enable_journal_timer()
        self.enable_in_timer()

    def enable_journal_timer(self) -> None:
        """Set a timer to resend any unacknowledged outbound chunks"""
        if self.is_offline():
            return  # run timers only when online
        if len(self._journal) > 0 and self._journal_timer is None:
            self._journal_timer = Timekeeper.exponential(2.0, self._resend_one, 2.0, 30.0)

    def enable_in_timer(self) -> None:
        """Set a timer to acknowledge receipt of received chunks"""
        if self.is_offline():
            return  # run timers only when online
        if self._in_index > self._in_last_ack and self._in_last_ack_timer is None:
            self._in_last_ack_timer = Timekeeper(1, self._send_ack)

    async def exec_and_forward_tcp(
        self,
        exec_args: list[str],
        host_ip_address: str,
        host_port: int,
        peer_ip_address: str,
        peer_port: int,
    ) -> None:
        await self._tcp_connect.exec_and_forward_tcp(
            exec_args, host_ip_address, host_port, peer_ip_address, peer_port
        )

    def allow_port_forwarding(self, allowed) -> None:
        self._tcp_connect.allow_port_forwarding(allowed)


class TcpConnector:
    """Open a TCP connection and shuffle data between it and the jet channel.

    Typical usage is for the 'host' end of a PersistentWebsocket connection to call
    exec_and_forward_tcp() and the 'peer' end to call allow_port_forwarding().
    """

    def __init__(self, pws: PersistentWebsocket):
        self._pws = pws
        self._allow_port_forwarding = False  # for security, denied by default
        self._connections = list()  # list of ActiveTcpConnection objects to call write(), close()
        self._to_host = False  # True==to local TCP port that we opened; False==to remote machine
        self._peer_ip_address = ''  # destination peer remote IP (stored by both host and peer)
        self._peer_port = ''  # destination peer remote port (stored by both host and peer)
        self._peer_connection = None

    def allow_port_forwarding(self, allowed):
        self._allow_port_forwarding = allowed  # for security, denied by default

    async def exec_and_forward_tcp(
        self,
        exec_args: list[str],
        host_ip_address: str,
        host_port: int,
        peer_ip_address: str,
        peer_port: int,
    ) -> None:
        """Set up port forwarding, similar to 'ssh -L', and run an external program."""
        try:
            self._peer_ip_address = peer_ip_address  # to be used later, when exec connects
            self._peer_port = peer_port
            open_host_port = await self.open_host_tcp_port(host_ip_address, host_port)
            output = await net.run_external_async(exec_args)
            if output:
                self._pws.log.info(f"B19653 output of: {output}")
        finally:
            open_host_port.close()

    async def open_host_tcp_port(self, ip_address: str, port: int) -> asyncio.Server:
        """Begin listening on the specified local TCP port."""
        self._to_host = True  # calling this method is the only way to make us host rather than peer
        loop = asyncio.get_running_loop()
        open_host_port = await loop.create_server(
            lambda: ActiveTcpConnection(
                self._connections,
                pws=self._pws,
                peer_ip_address=self._peer_ip_address,
                peer_port=self._peer_port,
            ),
            ip_address,
            port,
        )
        return open_host_port

    async def open_peer_connection(self, ip_address: str, port: int) -> None:
        """Initiate TCP connection out. Link data both ways with the jet channel."""
        if self._allow_port_forwarding and self._to_host == False:
            loop = asyncio.get_running_loop()
            self._peer_connection, _ = await loop.create_connection(
                lambda: ActiveTcpConnection(
                    self._connections,
                    pws=self._pws,
                ),
                ip_address,
                port,
            )
        # do nothing if remote connections are not permitted or if we are not a peer

    def write(self, data: bytes) -> None:
        if self._connections:
            self._connections[0].write(data)

    def close(self) -> None:
        """Close TCP connection (host or peer). For host, keep listening port open."""
        if self._connections:
            self._pws.log.debug("B54010 closing TCP connection")
            self._connections[0].close()
        if self._to_host == False and self._peer_connection:  # peer
            self._pws.log.debug("B26968 closing TCP peer connection")
            self._peer_connection.close()
            self._peer_connection = None
        # do nothing if remote connections are not permitted or if we are not a peer


class ActiveTcpConnection(asyncio.Protocol):
    """Represents one active TCP connection (host or peer)."""

    # docs: https://docs.python.org/3/library/asyncio-protocol.html
    def __init__(
        self,
        connections,
        pws: PersistentWebsocket,
        peer_ip_address: str = '',
        peer_port: int = 0,
    ):
        self._pws = pws
        self._connections = connections  # list of connections like this one
        self._peer_ip_address = peer_ip_address
        self._peer_port = peer_port

    def connection_made(self, transport):
        """New TCP connection established."""
        self._transport = transport
        peername = self._transport.get_extra_info('peername')
        self._pws.log.debug(f"B40828 {self._pws.log_id} TCP connection from {peername}")
        self._connections.append(self)
        if len(self._connections) > 1:  # allow at most 1 connection because there is 1 jet channel
            self._transport.close()  # terminate this new connection
        asyncio.create_task(self._pws._send_tcp_connect(self._peer_ip_address, self._peer_port))

    def data_received(self, data):
        """Called when TCP connection receives data."""
        asyncio.create_task(self._pws.jet_send(data))

    def connection_lost(self, exc):
        """Called when TCP connection is closed by us or by the TCP client."""
        self._pws.log.debug(f"B33276 {self._pws.log_id} TCP connection lost")
        self._connections.remove(self)
        asyncio.create_task(self._pws._send_tcp_disconnect())

    def write(self, data):
        self._transport.write(data)

    def close(self):
        """Close this connection but keep listening."""
        self._transport.close()  # close this connection but keep listening
