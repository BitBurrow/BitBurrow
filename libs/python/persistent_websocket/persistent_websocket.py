import asyncio
import collections
import logging
import random
import traceback
from typing import AsyncGenerator
import websockets

try:
    from starlette.websockets import WebSocketDisconnect

    using_starlette = True
except ImportError:
    from websockets.exceptions import ConnectionClosed as WebSocketDisconnect

    using_starlette = False

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


# FIXME: scan through for 15-bit overflow in math, comparisons, etc.


def lsb(index):
    """Convert index to on-the-wire format; see i_lsb description."""
    return (index % 32768).to_bytes(2, 'big')


def const_otw(c):
    """Convert _sig constant to on-the-wire format."""
    assert c >= 32768  # 0x8000
    assert c <= 65535  # 0xFFFF
    return c.to_bytes(2, 'big')


def unmod(xx, xxxx, w):
    """Undelete upper bits of xx by assuming it's near xxxx.

    Put another way: find n where n%w is xx and abs(xxxx-n) <= w/2. For
    example, unmod(yy, yyyy_today, 100) will convert a 2-digit year yy to
    a 4-digit year by assuming yy is within 50 years of the current year.
    """
    assert xx < w  # w is the window size (must be even), i.e. the number of possible values for xx
    splitp = (xxxx + w // 2) % w  # split point
    return xx + xxxx + w // 2 - splitp - (w if xx > splitp else 0)


# def unmod_test():
#    for win in [10, 100, 1000, 10_000, 32768, 8322]:
#        for _ in range(0, 1_000_000):
#            short = random.randint(0, win - 1)
#            long = random.randint(0, 0xFFFFFF)
#            n = unmod(short, long, win)
#            assert n % win == short
#            assert abs(long - n) <= win // 2
#            #print(f"unmod({short}, {long}, {win}) == {n}")


class Timer:  # based on https://stackoverflow.com/a/45430833
    def __init__(self, timeout, callback, is_periodic=False):
        self._timeout = timeout
        self._callback = callback
        self._is_periodic = is_periodic
        self._task = asyncio.create_task(self._job())

    @staticmethod
    def periodic(timeout, callback):
        return Timer(timeout, callback, is_periodic=True)

    async def _job(self):
        while True:
            await asyncio.sleep(self._timeout)
            await self._callback()  # time spent in callback delays next callback
            if not self._is_periodic:
                break

    def cancel(self):
        self._is_periodic = False
        self._task.cancel()


# # class Timer usage:
#
# start = time.time()
#
# def log(s):
#     print(f"{time.time()-start:3.0f}s: {s}")
#
# async def four_seconds():
#     log("four seconds")
#     await asyncio.sleep(7)
#
# async def five_seconds():
#     log("five seconds")
#
# async def main():
#     a = Timer.periodic(4, four_seconds)
#     b = Timer(5, five_seconds)
#     log("zero seconds")
#     await asyncio.sleep(30)
#     log("thirty seconds")
#     a.cancel()
#     await asyncio.sleep(30)
#     log("done")
#
# loop = asyncio.new_event_loop()
# asyncio.set_event_loop(loop)
# loop.run_until_complete(main())


class PersistentWebsocket:
    """Adds to WebSockets auto-reconnect and auto-resend of lost messages.

    This class adds to WebSockets (client and server) the ability to automatically reconnect,
    including for IP address changes, as well as resending any messages which may have been
    lost. To accomplish this, it uses a custom protocol which adds 2 bytes to the beginning
    of each WebSocket message and uses signals for acknowledgement and resend requests.
    """

    ### notes:
    #   * important: mirror changes in corresponding Dart code--search "bMjZmLdFv"
    #   * messages always arrive and in order; signals can be lost if WebSocket disconnects
    #   * all bytes are in big-endian byte order; use: int.from_bytes(chunk[0:2], 'big')
    #   * 'chunk' refers to a full WebSocket item, including the first 2 bytes
    #   * 'message' refers to chunk[2:] of a chunk that is not a signal (details below)
    #   * index → chunk number; first chunk sent is chunk 0
    #   * i_lsb → index mod 32768, i.e. the 15 least-significant bits
    #   * ping and pong (below) are barely implemented because we rely on WebSocket keep-alives
    ### on-the-wire format:
    #   * chunk containing a message (when chunk[0:2] < 32768):
    #       * chunk[0:2]  i_lsb
    #       * chunk[2:]   message
    #   * a signaling chunk (when chunk[0:2] >= 32768):
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

    def __init__(self, id: str):
        self.id = id  # uniquely identify this connection in log file
        self._ws = None
        self._url = None
        self._recv_index = 0  # index of next inbound chunk, aka number of chunks received
        self._recv_last_ack = 0  # index of most recently-sent _sig_ack
        # https://docs.python.org/3/library/collections.html#collections.deque
        self._journal = collections.deque()  # chunks sent but not yet confirmed by remote
        self._journal_index = 0  # index of the next outbound chunk, ...
        # aka index + 1 of right end (newest) of _journal
        self.connects = 0
        self.chaos = 0  # level of chaos to intentionaly introduce for testing, 50 recommended

    async def connected(self, ws) -> AsyncGenerator[bytes, None]:
        """Handle a new inbound WebSocket connection, yield inbound messages.

        This is the primary API entry point for a WebSocket SERVER. Signals from
        the client will be appropriately handled and inbound messages will be
        returned to the caller via `yield`.
        """
        self._url = None  # make it clear we are now a server
        self._ws = ws
        logger.info(f"B17183 {self.id} WebSocket reconnect {self.connects}")
        self.connects += 1
        async for m in self.listen():
            yield m

    async def connect(self, url: str) -> AsyncGenerator[bytes, None]:
        """Begin a new outbound WebSocket connection, yield inbound messages.

        This is the primary API entry point for a WebSocket CLIENT. Signals from
        the server will be appropriately handled and inbound messages will be
        returned to the caller via `yield`.
        """
        self._url = url
        try:
            # https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html
            logger.debug(f"B35536 {self.id} waiting for WebSocket to connect")
            async for ws in websockets.connect(self._url):
                self._ws = ws
                logger.info(f"B91334 {self.id} WebSocket reconnect {self.connects}")
                self.connects += 1
                async for m in self.listen():
                    yield m

        except asyncio.exceptions.CancelledError:  # ctrl-C
            logger.warn(f"B32045 {self.id} WebSocket canceled")
        except Exception:
            logger.error(f"B34752 {self.id} WebSocket exception, {traceback.format_exc().rstrip()}")
        await self.ensure_closed()

    async def listen(self) -> AsyncGenerator[bytes, None]:
        """Accept chunks on the WebSocket connection and yield messages."""
        # chunks were probably lost in reconnect, so ask the other end to resend
        await self._send_raw(const_otw(self._sig_resend) + lsb(self._recv_index))
        try:
            async for chunk in (self._ws.iter_bytes() if using_starlette else self._ws):
                if self.chaos > 0 and self.chaos > random.randint(0, 999):
                    logger.warn(f"B66740 {self.id} ❗❗ closing WebSocket to test recovery ❗❗")
                    await asyncio.sleep(random.randint(0, 3))
                    await self.ensure_closed()
                    await asyncio.sleep(random.randint(0, 3))
                message = await self.process_inbound(chunk)
                if message is not None:
                    logger.debug(f"B18042 {self.id} received: {message.decode()}")
                    yield message
            logger.info(f"B39653 {self.id} WebSocket closed")
        except websockets.ConnectionClosed:
            logger.warn(f"B60441 {self.id} WebSocket closed")
        except asyncio.exceptions.CancelledError:  # ctrl-C
            raise asyncio.exceptions.CancelledError
        except WebSocketDisconnect as e:
            if e == 1000:  # WS_1000_NORMAL_CLOSURE in starlette/status.py
                logger.info(f"B94731 {self.id} WebSocket closed by remote")
            elif e == 1012:  # WS_1012_SERVICE_RESTART in starlette/status.py
                raise asyncio.exceptions.CancelledError
            else:
                logger.error(f"B53771 {self.id} exception, {traceback.format_exc().rstrip()}")
        except RuntimeError as e:
            if e.args[0] != 'WebSocket is not connected. Need to call "accept" first.':
                # ignore 'not connected' because it's a result of intentional testing; see B66740
                logger.error(f"B81148 {self.id} exception, {traceback.format_exc().rstrip()}")
        except Exception:
            logger.error(f"B59584 {self.id} WebSocket exception, {traceback.format_exc().rstrip()}")

    async def send(self, message: str | bytes):
        """Send a message to the remote when possible, resending if necessary."""
        if isinstance(message, str):
            chunk = lsb(self._journal_index) + message.encode()  # convert message to bytes
        else:
            chunk = lsb(self._journal_index) + message  # message is already in bytes
        self._journal_index += 1
        self._journal.append(chunk)
        await self._send_raw(chunk)
        if self.chaos > 0 and self.chaos > random.randint(0, 999):
            logger.warn(f"B14263 {self.id} ❗❗ closing WebSocket to test recovery ❗❗")
            await asyncio.sleep(random.randint(0, 3))
            await self.ensure_closed()
            await asyncio.sleep(random.randint(0, 3))

    async def _resend(self, start_index):
        """Resend queued chunks."""
        if start_index == self._journal_index:
            return
        tail_index = self._journal_index - len(self._journal)
        if self._journal_index < start_index or start_index < tail_index:
            # FIXME: need to stop trying to reconnect, pass control to outer layer
            logger.error(
                f"B38394 {self.id} remote wants journal[{start_index}:] "
                + f"but we only have journal[{tail_index}:{self._journal_index}]"
            )
            await self._send_raw(const_otw(self._sig_resend_error))
            return
        logger.info(f"B57684 {self.id} resending journal[{start_index}:{self._journal_index}]")
        # send requested chunks from oldest to newest, e.g. range(-2, 0) for most recent 2 chunks
        for i in range(start_index - self._journal_index, 0):
            await self._send_raw(self._journal[i])

    async def _send_raw(self, chunk):
        """Send chunk of bytes if we can."""
        if self._ws is None:
            return
        try:
            if using_starlette:
                # https://www.starlette.io/websockets/#sending-data
                await self._ws.send_bytes(chunk)
            else:
                # https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html#websockets.client.WebSocketClientProtocol.send
                await self._ws.send(chunk)
            logger.debug(f"B41789 {self.id} sent: {chunk.hex(' ', -1)}")
        except WebSocketDisconnect:
            self._ws = None
            logger.info(f"B44793 {self.id} WebSocket disconnect")

    async def process_inbound(self, chunk) -> bytes | None:
        """Test and respond to chunk, returning a message or None."""
        i_lsb = int.from_bytes(chunk[0:2], 'big')  # first 2 bytes of chunk
        if i_lsb < 32768:  # message chunk
            index = unmod(i_lsb, self._recv_index, 32768)  # expand 15 bits to full index
            if index == self._recv_index:  # valid
                self._recv_index += 1
                # send _sig_ack after successfully receiving 16 chunks so remote can clear _journal
                if self._recv_index - self._recv_last_ack >= 16:
                    await self._send_raw(const_otw(self._sig_ack) + lsb(self._recv_index))
                    self._recv_last_ack = self._recv_index
                    await self.send(f"We've received {self._recv_index} messages")  # TESTING
                return chunk[2:]  # message
            elif index > self._recv_index:  # request the other end resend what we're missing
                await self._send_raw(const_otw(self._sig_resend) + lsb(self._recv_index))
            logger.info(f"B73822 {self.id} ignoring duplicate chunk {index}")
        else:  # signal
            if i_lsb == self._sig_ack:
                ack_index = unmod(int.from_bytes(chunk[2:4], 'big'), self._journal_index, 32768)
                tail_index = self._journal_index - len(self._journal)
                logger.info(f"B60966 {self.id} clearing journal[{tail_index}:{ack_index}]")
                for i in range(tail_index, ack_index):
                    self._journal.popleft()
            elif i_lsb == self._sig_resend:
                resend_index = unmod(int.from_bytes(chunk[2:4], 'big'), self._journal_index, 32768)
                await self._resend(resend_index)
            elif i_lsb == self._sig_resend_error:
                await self.ensure_closed()
                # FIXME: need to stop trying to reconnect, pass control to outer layer
                logger.error(f"B75561 {self.id} broken connection")
            elif i_lsb == self._sig_ping:
                await self._send_raw(const_otw(self._sig_pong) + chunk[2:])
            elif i_lsb == self._sig_pong:
                pass
            else:
                logger.error(f"B32405 {self.id} unknown signal {i_lsb}")
        return None

    async def ping(self, data):
        await self._send_raw(const_otw(self._sig_ping) + data)

    async def ensure_closed(self):
        """Close the WebSocket connection; can be called multiple times."""
        if self._ws is None:
            return
        try:
            await self._ws.close()
            logger.info(f"B89445 {self.id} WebSocket closed")
        except RuntimeError as e:
            # probably websocket close after close
            logger.debug(f"B79020 {self.id} WebSocket error {e}")
        except Exception:
            logger.error(f"B39425 {self.id} WebSocket exception, {traceback.format_exc().rstrip()}")
        self._ws = None
