import asyncio
import collections
import logging
import traceback
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


def lsb(index):  # convert index to on-the-wire format; see i_lsb description
    return (index % 32768).to_bytes(2, 'big')


def const_otw(c):  # convert _sig constant to on-the-wire format
    assert c >= 32768  # 0x8000
    assert c <= 65535  # 0xFFFF
    return c.to_bytes(2, 'big')


def unmod(xx, xxxx, w):  # undelete upper bits of xx by assuming it's near xxxx
    # put another way: find n where n%w is xx and abs(xxxx-n) <= w/2
    # if w==100, this can convert 2-digit years to 4-digit years, assuming within 50 years of today
    assert xx < w  # w is the window size (must be even), i.e. the number of possible values for xx
    splitp = (xxxx + w // 2) % w  # split point
    return xx + xxxx + w // 2 - splitp - (w if xx > splitp else 0)


# import random
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
    # important: mirror changes in corresponding Dart code--search "bMjZmLdFv"
    ### on-the-wire format:
    # notes:
    #     messages always arrive and in order; signals can be lost if WebSocket disconnects
    #     all bytes are in big-endian byte order; use: int.from_bytes(chunk[0:2], 'big')
    #     index → full chunk index; first chunk sent is chunk 0
    #     i_lsb → index mod 32768, i.e. the 15 least-significant bits
    # a message (chunk[0:2] < 32768):
    #     chunk[0:2]  i_lsb
    #     chunk[2:]   message content
    # a signal (chunk[0:2] >= 32768):
    _sig_ack = 0x8010  # "I have received n total chunks"
    #     chunk[2:4]  i_lsb of next expected chunk
    _sig_resend = 0x8011  # "Please resend chunk n and everything after it"
    #     chunk[2:4]  i_lsb of first chunk to resend
    _sig_resend_error = 0x8012  # "I cannot resend the requested chunks"
    #     chunk[2:]   (optional, ignored)
    _sig_ping = 0x8020  # "Are you alive?"
    #     chunk[2:]   (optional)
    _sig_pong = 0x8021  # "Yes, I am alive."
    #     chunk[2:]   chunk[2:] from corresponding ping

    def __init__(self):
        self._ws = None
        self._inbound = None
        self._url = None
        self._recv_index = 0  # index of next expected chunk
        self._recv_last_ack = 0  # index of most recently-sent ack
        # https://docs.python.org/3/library/collections.html#collections.deque
        self._journal = collections.deque()  # chunks sent but not yet confirmed by remote
        self._journal_index = 0  # chunk index + 1 of right end (newest) of _journal

    async def connected(self, ws):  # as SERVER, handle a new WebSocket connection, loop
        self._url = None  # make it clear we are now a server
        self._ws = ws
        logger.info(f"B17183 WebSocket connected")
        async for m in self.listen():
            yield m

    async def connect(self, url):  # as CLIENT, begin a new connection, loop
        self._url = url
        try:
            # https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html
            logger.debug(f"B35536 waiting for WebSocket to connect")
            async for ws in websockets.connect(self._url):
                logger.info(f"B91334 WebSocket connect")
                self._ws = ws
                async for m in self.listen():
                    yield m

        except asyncio.exceptions.CancelledError:  # ctrl-C
            logger.warn(f"B32045 WebSocket canceled")
        except Exception:
            logger.error(f"B34752 WebSocket exception, {traceback.format_exc().rstrip()}")
        await self.ensure_closed()

    async def listen(self):
        # chunks were probably lost in reconnect, so ask the other end to resend
        await self._send_raw(const_otw(self._sig_resend) + lsb(self._recv_index))
        try:
            async for chunk in (self._ws.iter_bytes() if using_starlette else self._ws):
                message = await self.process_inbound(chunk)
                if message is not None:
                    logger.debug(f"B18042 received: {message.decode()}")
                    yield message
            logger.info(f"B39653 WebSocket closed")
        except websockets.ConnectionClosed:
            logger.warn(f"B60441 WebSocket closed")
        except asyncio.exceptions.CancelledError:  # ctrl-C
            raise asyncio.exceptions.CancelledError
        except WebSocketDisconnect as e:
            if e == 1000:  # WS_1000_NORMAL_CLOSURE in starlette/status.py
                logger.info(f"B94731 WebSocket closed by remote")
            elif e == 1012:  # WS_1012_SERVICE_RESTART in starlette/status.py
                raise asyncio.exceptions.CancelledError
            else:
                logger.error(f"B53771 WebSocket exception, {traceback.format_exc().rstrip()}")
        except Exception:
            logger.error(f"B59584 WebSocket exception, {traceback.format_exc().rstrip()}")

    async def send(self, message):  # save message to resend when needed; send if we can
        if isinstance(message, str):
            chunk = lsb(self._journal_index) + message.encode()  # convert message to bytes
        else:
            chunk = lsb(self._journal_index) + message  # message is already in bytes
        self._journal_index += 1
        self._journal.append(chunk)
        await self._send_raw(chunk)

    async def _resend(self, start_index):  # resend queued chunks
        if start_index == self._journal_index:
            return
        tail_index = self._journal_index - len(self._journal)
        if self._journal_index < start_index or start_index < tail_index:
            # FIXME: need to stop trying to reconnect, pass control to outer layer
            logger.error(
                f"B38394 remote wants journal[{start_index}:] "
                + f"but we only have journal[{tail_index}:{self._journal_index}]"
            )
            await self._send_raw(const_otw(self._sig_resend_error))
            return
        logger.info(f"B57684 resending journal[{start_index}:{self._journal_index}]")
        # send requested chunks from oldest to newest, e.g. range(-2, 0) for most recent 2 chunks
        for i in range(start_index - self._journal_index, 0):
            await self._send_raw(self._journal[i])

    async def _send_raw(self, chunk):  # send chunk of bytes if we can
        if self._ws is None:
            return
        try:
            if using_starlette:
                # https://www.starlette.io/websockets/#sending-data
                await self._ws.send_bytes(chunk)
            else:
                # https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html#websockets.client.WebSocketClientProtocol.send
                await self._ws.send(chunk)
            logger.debug(f"B41789 sent: {chunk.hex(' ', -1)}")
        except WebSocketDisconnect:
            self._ws = None
            logger.info(f"B44793 WebSocket disconnect")

    async def process_inbound(self, chunk):
        i_lsb = int.from_bytes(chunk[0:2], 'big')  # first 2 bytes of chunk
        if i_lsb < 32768:  # message
            index = unmod(i_lsb, self._recv_index, 32768)  # expand 15 bits to full index
            if index == self._recv_index:  # valid
                self._recv_index += 1
                # send _sig_ack after successfully receiving 16 chunks so remote can clear _journal
                if self._recv_index - self._recv_last_ack >= 16:
                    await self._send_raw(const_otw(self._sig_ack) + lsb(self._recv_index))
                    self._recv_last_ack = self._recv_index
                    await self.send(f"We've received {self._recv_index} messages")  # TESTING
                return chunk[2:]  # message contents
            elif index > self._recv_index:  # request the other end resend what we're missing
                await self._send_raw(const_otw(self._sig_resend) + lsb(self._recv_index))
            logger.info(f"B73822 ignoring duplicate chunk {index}")
        else:  # signal
            if i_lsb == self._sig_ack:
                ack_index = unmod(int.from_bytes(chunk[2:4], 'big'), self._journal_index, 32768)
                tail_index = self._journal_index - len(self._journal)
                logger.info(f"B60966 clearing journal[{tail_index}:{ack_index}]")
                for i in range(tail_index, ack_index):
                    self._journal.popleft()
            elif i_lsb == self._sig_resend:
                resend_index = unmod(int.from_bytes(chunk[2:4], 'big'), self._journal_index, 32768)
                await self._resend(resend_index)
            elif i_lsb == self._sig_resend_error:
                await self.ensure_closed()
                # FIXME: need to stop trying to reconnect, pass control to outer layer
                logger.error(f"B75561 broken connection")
            elif i_lsb == self._sig_ping:
                await self._send_raw(const_otw(self._sig_pong) + chunk[2:])
            elif i_lsb == self._sig_pong:
                pass
            else:
                logger.error(f"B32405 unknown signal {i_lsb}")
        return None

    async def ping(self, data):
        await self._send_raw(const_otw(self._sig_ping) + data)

    async def ensure_closed(self):
        if self._ws is None:
            return
        try:
            await self._ws.close()
            logger.info("B89445 WebSocket closed")
        except RuntimeError as e:
            logger.debug("B79020 WebSocket error {e}")  # probably websocket close after close
        except Exception:
            logger.error(f"B39425 WebSocket exception, {traceback.format_exc().rstrip()}")
        self._ws = None
