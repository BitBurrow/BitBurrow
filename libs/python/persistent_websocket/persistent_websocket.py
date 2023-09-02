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


def index_otw(index):  # convert index to on-the-wire format (index mod 32768 with bit 15 set to 0)
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
    _sig_ack = 0x8010  # "I have received n total chunks" where n is next 2 bytes
    _sig_resend = 0x8011  # "Please resend chunk n and everything after it" where n is next 2 bytes
    _sig_resend_error = 0x8012  # "I cannot resend the requested messages"
    ### on-the-wire format (big-endian byte order)
    # chunk[0:2]
    #     bit 15 → signal flag; see _sig constants
    #     bits 0-14 → chunk index mod 32768 (first chunk sent is chunk 0) or _sig constant
    # chunk[2:]
    #     ack or resend index (if signal) or message (otherwise)

    def __init__(self):
        self._ws = None
        self._recv_index = 0  # index of next expected chunk
        self._recv_last_ack = 0  # index of most recently-sent ack
        # https://docs.python.org/3/library/collections.html#collections.deque
        self._journal = collections.deque()  # chunks sent but not yet confirmed by remote
        self._journal_index = 0  # chunk index + 1 of right end (newest) of _journal

    async def connected(self, ws):  # as SERVER, handle a new WebSocket connection, loop
        self._url = None
        self._ws = ws
        logger.info(f"B17183 WebSocket connected")
        # request the other end resend what we're missing
        await self._send_raw(const_otw(self._sig_resend) + index_otw(self._recv_index))
        try:
            # while True:
            #     chunk = await ws.receive_bytes()
            async for chunk in ws.iter_bytes():
                message = await self.process_inbound(chunk)
                if message is not None:
                    logger.debug(f"B88406 received: {message}")
            logger.info(f"B39653 WebSocket closed")
        except asyncio.exceptions.CancelledError:  # ctrl-C
            logger.info(f"B32045 WebSocket canceled")
        except WebSocketDisconnect as e:
            if e == 1000:  # WS_1000_NORMAL_CLOSURE in starlette/status.py
                logger.info(f"B94731 WebSocket closed by remote")
            elif e == 1012:  # WS_1012_SERVICE_RESTART in starlette/status.py
                logger.warn(f"B84487 WebSocket canceled")  # ctrl-C
            else:
                logger.error(f"B53771 WebSocket exception, {traceback.format_exc().rstrip()}")
        except Exception:
            logger.error(f"B66312 WebSocket exception, {traceback.format_exc().rstrip()}")
        await PersistentWebsocket.ensure_closed(self._ws)

    async def connect(self, url):  # as CLIENT, begin a new connection, loop
        self._url = url
        # https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html
        redo = 2  # chunks to resend without being asked (okay to adjust)
        try:
            async for ws in websockets.connect(url):
                logger.info(f"B91334 connect")
                self._ws = ws
                # request the other end resend what we're missing
                await self._send_raw(const_otw(self._sig_resend) + index_otw(self._recv_index))
                try:
                    async for chunk in ws:
                        await self.process_inbound(chunk)
                except websockets.ConnectionClosed:
                    logger.warn(f"B60441 WebSocket closed; retrying")
                    continue
                except Exception:
                    logger.error(f"B59584 WebSocket exception, {traceback.format_exc().rstrip()}")
                    continue
        except Exception:
            logger.error(f"B34752 WebSocket exception, {traceback.format_exc().rstrip()}")
        await PersistentWebsocket.ensure_closed(self._ws)

    async def send(self, message):  # save message to resend when needed; send if we can
        if isinstance(message, str):
            chunk = index_otw(self._journal_index) + message.encode()  # convert message to bytes
        else:
            chunk = index_otw(self._journal_index) + message  # message is already in bytes
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
        bits0_15 = int.from_bytes(chunk[0:2], 'big')  # first 2 bytes of chunk
        if bits0_15 < 32768:  # not a signal
            index = unmod(bits0_15, self._recv_index, 32768)  # expand 15 bits to full index
            if index == self._recv_index:  # valid
                self._recv_index += 1
                # send _sig_ack after successfully receiving 16 chunks so remote can clear _journal
                if self._recv_index - self._recv_last_ack >= 16:
                    await self._send_raw(const_otw(self._sig_ack) + index_otw(self._recv_index))
                    self._recv_last_ack = self._recv_index
                return chunk[2:].decode()  # message
            elif index > self._recv_index:  # request the other end resend what we're missing
                await self._send_raw(const_otw(self._sig_resend) + index_otw(self._recv_index))
            logger.info(f"B73822 ignoring duplicate chunk {index}")
        else:  # signal
            if bits0_15 == self._sig_ack:
                ack_index = unmod(int.from_bytes(chunk[2:4], 'big'), self._journal_index, 32768)
                tail_index = self._journal_index - len(self._journal)
                logger.info(f"B60966 clearing journal[{tail_index}:{ack_index}]")
                for i in range(tail_index, ack_index):
                    self._journal.popleft()
            elif bits0_15 == self._sig_resend:
                resend_index = unmod(int.from_bytes(chunk[2:4], 'big'), self._journal_index, 32768)
                await self._resend(resend_index)
            elif bits0_15 == self._sig_resend_error:
                await PersistentWebsocket.ensure_closed(self._ws)
                # FIXME: need to stop trying to reconnect, pass control to outer layer
                logger.error(f"B75561 broken connection")
            else:
                logger.error(f"B32405 unknown signal {bits0_15}")
        return None

    @staticmethod
    async def ensure_closed(ws):
        try:
            await ws.close()
        except RuntimeError:
            pass  # probably: 'websocket.close', after sending 'websocket.close'
        except Exception:
            logger.error(f"B39425 WebSocket exception, {traceback.format_exc().rstrip()}")
