import asyncio
import queue
from fastapi import WebSocketDisconnect
import logging


logger = logging.getLogger(__name__)


class MQueue(queue.SimpleQueue):
    def __init__(self):
        self._index = 0  # index of next chunk we will send
        self._tail_index = 0  # index of oldest chunk in queue

    def index(self):
        return self._index

    def enqueue(self, content):
        self.put_nowait(content)
        self._index += 1

    def dequeue(self, stop_before_index):
        try:
            for i in range(self._tail_index, stop_before_index):
                content = self.get_nowait()
                index15 = int.from_bytes(content[0:1], 'big')
                assert index15 < 0x8000
                assert i == unmod(index15, self._recv_index, 0x8000)
        except queue.Empty:
            assert False
        self._tail_index = stop_before_index

    def to_list(self, start):
        if start < 0:  # -3 means 'most recent 3 chunks'
            queue_index = start + self._index - self._tail_index
        else:
            queue_index = start - self._tail_index
        with self.mutex:  # https://stackoverflow.com/a/35800497
            return list(self.queue)[queue_index:]


# FIXME: scan through for 15-bit overflow in math, comparisons, etc.


def index_otw(index):  # convert index to on-the-wire format (index mod 32768 with bit 15 set to 0)
    return (index % 0x8000).to_bytes(2, 'big')


def const_otw(c):  # convert _sig constant to on-the-wire format
    assert c >= 0x8000
    assert c <= 0xFFFF
    return c.to_bytes(2, 'big')


def unmod(xx, xxxx, w):  # undelete upper bits of xx by assuming it's near xxxx
    # put another way: find n where n%w is xx and abs(xxxx-n) <= w/2
    # if w==100, this can convert 2-digit years to 4-digit years, assuming within 50 years of today
    assert xx < w  # w is the window size (must be even), i.e. the number of possible values for xx
    splitp = (xxxx + w // 2) % w  # split point
    return xx + xxxx + w // 2 - splitp - (w if xx > splitp else 0)


# import random
# def unmod_test():
#    for win in [10, 100, 1000, 10_000, 0x8000, 8322]:
#        for _ in range(0, 1_000_000):
#            short = random.randint(0, win - 1)
#            long = random.randint(0, 0xFFFFFF)
#            n = unmod(short, long, win)
#            assert n % win == short
#            assert abs(long - n) <= win // 2
#            #print(f"unmod({short}, {long}, {win}) == {n}")


class PersistentWebsocket:
    # important: mirror changes in corresponding Dart code--search "bMjZmLdFv"
    _sig_ping = 0x8001  # "Are you there?" from WebSocket client
    _sig_pong = 0x8002  # "Yes I am here" response from WebSocket server
    _sig_ack = 0x8010  # "I have received n total chunks" where n is next 2 bytes
    _sig_resend = 0x8011  # "Please resend chunk n and everything after it" where n is next 2 bytes
    _ack_every = 16  # send _sig_ack after successfully receiving n chunks
    ### on-the-wire format (big-endian byte order)
    # bytes 0-1
    #     bit 15 → signal flag; see _sig constants
    #     bits 0-14 → chunk index mod 32768 (first chunk sent is chunk 0) or _sig constant
    # bytes 2+
    #     ack or resend index (if signal) or data (otherwise)

    def __init__(self):
        self._ws = None
        self._recv_index = 0  # index of next expected chunk
        self._recv_last_ack = 0  # index of most recently-sent ask
        self._send_index_to_retry = 0  # from where we should resend after reconnect
        self._send = MQueue()

    async def connected(self, ws):  # new WebSocket connection
        self._ws = ws
        await self._send_queued(self._send_index_to_retry)  # resend what was likely lost
        try:
            while self._ws is not None:
                await asyncio.sleep(60)  # FIXME: find a cleaner way to return when ws closes
        except asyncio.exceptions.CancelledError:  # ctrl-C
            logger.info(f"B32045 WebSocket canceled")

    async def _send_raw(self, otw):  # send chunk of bytes if we can; otw is on-the-wire
        try:
            await self._ws.send_bytes(otw)
        except AttributeError:
            pass  # _ws is probably None
        except WebSocketDisconnect:
            self._ws = None
            redo = 2  # chunks to resend without being asked (okay to adjust)
            self._send_index_to_retry = self._send.index - redo if self._send.index > redo else 0
            logger.info(f"B44793 WebSocket disconnect")

    async def send(self, data):  # save data to resend if needed; send if we can
        if isinstance(data, str):
            data = data.encode()  # data needs to be of type: bytes
        otw = index_otw(self._send.index) + data
        self._send.enqueue(otw)
        await self._send_raw(otw)

    async def _send_queued(self, start_index):  # resend queued chunks
        for otw in self._send.to_list(start_index):
            await self._send_raw(otw)

    async def receive(self):
        while True:
            try:
                otw = await self._ws.receive_bytes()
                index15 = int.from_bytes(otw[0:1], 'big')  # lower 15 bits of index
                if index15 < 0x8000:  # not a signal
                    index = unmod(index15, self._recv_index, 0x8000)  # expand 15 bits to full index
                    if index < self._recv_index:  # dup of chunk we already have → ignore it
                        continue
                    if index == self._recv_index:  # valid
                        self._recv_index += 1
                        if self._recv_index - self._recv_last_ack > self._ack_every:
                            await self._send_raw(
                                const_otw(self._sig_ack) + index_otw(self._recv_index)
                            )
                        break
                    # request the other end resend what we're missing
                    await self._send_raw(const_otw(self._sig_resend) + index_otw(self._recv_index))
                else:  # signal
                    if index15 == self._sig_ping:
                        await self._send_raw(const_otw(self._sig_pong))
                        logger.info(f"wss: ping-pong")
                    if index15 == self._sig_ack:
                        ack_index = unmod(int.from_bytes(otw[2:3]), self._send.index, 0x8000)
                        self._send.dequeue(ack_index)
                    if index15 == self._sig_resend:
                        resend_index = unmod(int.from_bytes(otw[2:3]), self._send.index, 0x8000)
                        await self._send(resend_index)
            except AttributeError:
                await asyncio.sleep(5)  # _ws is probably None
            except WebSocketDisconnect:
                self._ws = None
                logger.info(f"B44792 WebSocket disconnect")
        return otw[2:]  # 'data'

    async def message_handler(self):
        while True:
            data = await self.receive()
            print(f"received: {data}")
