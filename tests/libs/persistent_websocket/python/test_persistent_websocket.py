import asyncio
import logging
import random
import re
import sys
import time
import websockets

import libs.persistent_websocket.python.persistent_websocket as persistent_websocket

display_messages = False  # print PersistentWebSocket messages sent and received
pws_log = logging.getLogger('persistent_websocket')
pws_log.setLevel(logging.INFO)  # use logging.DEBUG to see details, or .INFO for basic
console_handler = logging.StreamHandler()
formatter = logging.Formatter(
    '%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s',
    datefmt='%H:%M:%S',
)
console_handler.setFormatter(formatter)
pws_log.addHandler(console_handler)
pws_log.debug("B20465 test debug message")


async def test_messaging():
    """Set up 2-node testing lab and run tests."""
    data = nodes(item_count=250)  # item_count directly affects time required for test
    #
    ### node_2
    messages2 = persistent_websocket.PersistentWebsocket('nd_2', pws_log)
    data.listener_tasks.append(asyncio.create_task(data.node_2_websocket_server(messages2)))
    # await asyncio.sleep(0.5)  # make sure node_2 is listening (optional)
    #
    ### node_3
    messages3 = persistent_websocket.PersistentWebsocket('nd_3', pws_log)
    url = 'ws://127.0.0.1:18732'
    data.listener_tasks.append(asyncio.create_task(data.node_x_listener(3, None, messages3, url)))
    #
    ### waiting
    pws_log.debug("B23095 waiting for speaker tasks to begin")
    while len(data.speaker_tasks) < 2:
        await asyncio.sleep(0.1)  # wait for tasks to start
    pws_log.debug("B95870 waiting for speaker tasks to finish")
    await asyncio.gather(*data.speaker_tasks, return_exceptions=True)  # let speakers finish
    pws_log.debug("B87240 waiting up to 15 seconds for success from listener tasks")
    wait_time = 0.0
    while len(data.successes) < 2 and wait_time < 15.0:
        await asyncio.sleep(0.1)
        wait_time += 0.1
    pws_log.debug("B98788 canceling listeners")
    for t in data.listener_tasks:
        t.cancel()
    await asyncio.gather(*data.listener_tasks, return_exceptions=True)  # wait for them to die
    if len(data.errors) > 0:
        assert False, data.errors[0]
    if len(data.successes) < 2:
        assert False, "a listener task didn't didn't receive all of its items"
    pws_log.debug("B91688 done waiting on tasks")


async def test_nodes_1_and_4():
    """Set up 2-node testing lab and run tests  node 1 ↔ 4."""
    print()  # cleaner pytest display
    data = nodes()
    #
    ### node_4
    node_4_ready = asyncio.Event()
    data.other_tasks.append(asyncio.create_task(data.node_4(listen_port=28920, ready=node_4_ready)))
    await node_4_ready.wait()  # make sure node_4 is listening on TCP port
    #
    ### node_1
    data.other_tasks.append(asyncio.create_task(data.node_1(outbound_port=28920)))
    #
    ### waiting
    # wait for them to die; for pytest, return_exceptions must be False--see
    # https://docs.python.org/3/library/asyncio-task.html#asyncio.gather
    await asyncio.gather(*data.other_tasks, return_exceptions=False)


async def test_nodes_1_through_4():
    """Set up 4-node testing lab and run tests  node 1 ↔ 2 ↔ 3 ↔ 4."""
    print()  # cleaner pytest display
    data = nodes(item_count=10)
    #
    ### node_2
    # connection_list_remote = list()
    messages2 = persistent_websocket.PersistentWebsocket('nd_2', pws_log)
    data.listener_tasks.append(asyncio.create_task(data.node_2_websocket_server(messages2)))
    #
    ### node_3
    messages3 = persistent_websocket.PersistentWebsocket('nd_3', pws_log)
    messages3.allow_port_forwarding(True)
    url = 'ws://127.0.0.1:18732'
    data.listener_tasks.append(asyncio.create_task(data.node_x_listener(3, None, messages3, url)))
    #
    ### node_4
    node_4_ready = asyncio.Event()
    data.other_tasks.append(asyncio.create_task(data.node_4(listen_port=28923, ready=node_4_ready)))
    await node_4_ready.wait()  # make sure node_4 is listening on TCP port
    #
    ### node_1 run via node_2
    # this_file = f'tests/libs/persistent_websocket/python/{os.path.basename(__file__)}'
    cmd = f'poetry run python3 exec_file node_1'.split()
    cmd[cmd.index('exec_file')] = __file__  # run *this* file
    await messages2.exec_and_forward_tcp(cmd, '127.0.0.1', 28922, '127.0.0.1', 28923)
    #
    ### waiting
    while len(data.speaker_tasks) < 2:
        await asyncio.sleep(0.1)  # wait for tasks to start
    await asyncio.gather(*data.speaker_tasks, return_exceptions=True)  # let speakers finish
    wait_time = 0.0
    while len(data.successes) < 2 and wait_time < 15.0:
        await asyncio.sleep(0.1)
        wait_time += 0.1
    for t in data.listener_tasks:
        t.cancel()
    await asyncio.gather(*data.listener_tasks, return_exceptions=True)  # wait for them to die


def number_stream_segment(begin, end):
    """Returns a string of numbers separated by spaces, but in random-length segments."""
    range_min = 3
    range_max = 300
    draft_yield = ''
    yield_length = random.randint(range_min, range_max)
    for n in range(begin, end):
        draft_yield += f'{n} '
        if len(draft_yield) >= yield_length:
            yield draft_yield[0:yield_length]
            draft_yield = draft_yield[yield_length:]
            yield_length = random.randint(range_min, range_max)
    yield draft_yield


class nodes:
    """Simulated nodes for testing communications between BitBurrow pieces.

    two-node test:

    node_1: plays role of the Ansible control node
        TCP out to node_4 port 28920
    node_4: plays role of the BitBurrow base (router and Ansible managed node)
        TCP in from node_1 port 28920

    four-node test:

    node_1: plays role of the Ansible control node
        TCP out to node_2 port 28922
    node_2: plays role of the BitBurrow hub
        PersistentWebSocket in from node_3 port 18732
        TCP in from node_1 port 28922
    node_3: plays role of the BitBurrow app
        PersistentWebSocket out to node_2 port 18732
        TCP out to node_4 port 28923
    node_4: plays role of the BitBurrow base (router and Ansible managed node)
        TCP in from node_3 port 28923
    """

    def __init__(self, item_count=0):
        self.item_count = item_count
        self.speaker_tasks = list()
        self.listener_tasks = list()
        self.other_tasks = list()
        self.successes = list()
        self.next_n = dict()  # {node: next_n}
        self.errors = list()

    async def node_1(self, outbound_port):
        node_num = 1
        loop = asyncio.get_running_loop()
        quit_notice = loop.create_future()
        connection_list = list()
        peer_connection = None
        last_rep = 5
        for rep in range(1, last_rep + 1):
            pws_log.info(f"B53991 connection test {rep} of {last_rep}")
            try:
                peer_connection, protocol = await loop.create_connection(
                    lambda: ActiveTcpConnection(quit_notice, connection_list, node_num=node_num),
                    '127.0.0.1',
                    outbound_port,
                )
                intro_string = "connected to node 4\n"
                while len(protocol.buffer[1]) < len(intro_string):
                    await asyncio.sleep(0.1)  # wait
                assert protocol.buffer[1].startswith(intro_string)
                protocol.buffer[1] = protocol.buffer[1][len(intro_string) :]
            except Exception as e:
                pws_log.info(f"B60192 exception {e}")
                peer_connection = None
            else:
                time_start = time.perf_counter()
                r1 = 11111 * rep
                r2 = r1 + random.randint(50000, 90000)
                if rep == 1 or rep >= 5:
                    # send number sequence in random-length segments
                    for segment in number_stream_segment(r1, r2):
                        await asyncio.sleep(0.001)
                        peer_connection.write(f"echo {segment}\n".encode())
                else:
                    segments = number_stream_segment(r1, r2)
                    while True:
                        await asyncio.sleep(0.001)
                        segment = ""
                        try:
                            for _ in range(rep):  # write multiple commands at once
                                segment += f"echo {next(segments)}\n"
                        except StopIteration:
                            break
                        finally:
                            peer_connection.write(segment.encode())
                expected = ' '.join(str(n) for n in range(r1, r2)) + ' '
                prior_len = 0
                ms_since_data_received = 0
                while True:  # wait until buff_len is big enough OR no data received for 15 seconds
                    buff_len = len(protocol.buffer[1])
                    if buff_len > prior_len:
                        ms_since_data_received = 0
                        prior_len = buff_len
                    if buff_len >= len(expected) or ms_since_data_received > 15000:
                        break
                    await asyncio.sleep(0.1)
                    ms_since_data_received += 100
                time_lap1 = time.perf_counter() - time_start
                assert protocol.buffer[1] == expected  # make sure we got back exactly what we sent
                pws_log.info(f"B11890     {round(time_lap1 * 1000)} ms, {len(expected)} chars")
                if rep < last_rep:
                    # test that we can tell when the remote closes the connection
                    peer_connection.write(f"close\n".encode())
                else:
                    peer_connection.write(f"quit\n".encode())
                while protocol.transport:  # wait for disconnect from node_4
                    await asyncio.sleep(0.00001)
                time_lap2 = time.perf_counter() - time_start - time_lap1
                pws_log.info(f"B80929     TCP disconnect took {round(time_lap2 * 1000000)} µs")
        pws_log.info(f"B97965 node_{node_num}_complete")

    async def node_2_websocket_server(self, messages):
        pws_log.info("B95789 node_2 is listening (WebSocket)")
        async with websockets.serve(
            lambda ws, path: self.node_x_listener(2, ws, messages, ''), '127.0.0.1', 18732
        ):
            await asyncio.Future()  # run forever

    async def node_x_listener(self, node, ws, messages, url):
        self.speaker_tasks.append(asyncio.create_task(self.node_x_speaker(node, messages)))
        pws_log.info(f"B70251 started node_{node}_speaker (have {len(self.speaker_tasks)} tasks)")
        items_received = 0
        async for m in messages.connected(ws) if ws else messages.connect(url):
            n = int(m.decode())
            if node in self.next_n and n != self.next_n[node]:
                self.errors.append(
                    f"items out of order at node_{node} "
                    + f"(expecting {self.next_n[node]}, got {n})"
                )
            self.next_n[node] = n + 1
            if display_messages:
                column = " " * 29 * (node - 2)
                pws_log.info(f"B00057             {column}{n}")
            items_received += 1
            if items_received == self.item_count:
                self.successes.append(f'node_{node}')  # successfully received all items

    async def node_x_speaker(self, node, messages: persistent_websocket.PersistentWebsocket):
        if node == 2:
            await asyncio.sleep(0.500)  # for testing, offset one of the streams
        begin = 10_000_000 * node
        for n in range(begin, begin + self.item_count):
            await asyncio.sleep(0.010)
            skip = -1  # to test the test, set skip to `self.item_count - 1` or `100`
            if n != begin + skip:
                await messages.send(str(n))
                if display_messages:
                    column = " " * 29 * (3 - node)
                    pws_log.info("B20340 {column}{n} → ")

    async def node_4(self, listen_port, ready):
        loop = asyncio.get_running_loop()
        quit_notice = loop.create_future()
        connection_list_local = list()
        local_open_port = await loop.create_server(
            lambda: ActiveTcpConnection(quit_notice, connection_list_local, node_num=4),
            '127.0.0.1',
            listen_port,
        )
        ready.set()  # tell caller that our set-up is complete
        try:
            await quit_notice
        finally:
            local_open_port.close()
            pws_log.info("B38045 node_4 complete")


class ActiveTcpConnection(asyncio.Protocol):
    """Represents one active TCP connection (host or peer)."""

    # docs: https://docs.python.org/3/library/asyncio-protocol.html
    def __init__(self, quit_notice, connections, node_num: int):
        self._quit_notice = quit_notice
        self._connections = connections  # list of connections like this one
        self.transport: asyncio.BaseTransport | None = None
        self._node_num = node_num
        self.buffer: dict[int, str] = dict()  # inbound data buffer for each node

    def connection_made(self, transport: asyncio.BaseTransport):
        """New TCP connection established."""
        self.transport = transport
        # peer = self._transport.get_extra_info('peername')
        # pws_log.debug(f"B24713 {peer} connected to " f"node {self._node_num}")
        self._connections.append(self)
        if len(self._connections) > 1:
            pws_log.info(f"B58943 node {self._node_num} too many connections")
            self.transport.close()
        self.buffer[self._node_num] = ''
        if self._node_num == 4:
            self.write((f"connected to node {self._node_num}\n"))

    def data_received(self, data: bytes):
        """Called when TCP connection receives data."""
        if self.transport is None:
            return
        # pws_log.debug(f"B72789 node {self._node_num} received: {data.decode()}")
        self.buffer[self._node_num] += data.decode()
        if self._node_num == 4:
            lines = re.split(r'\r\n|\r|\n', self.buffer[self._node_num])  # telnet-compatible
            self.buffer[self._node_num] = lines[-1]  # leave the incomplete line
            for line in lines[0:-1]:  # process all complete lines
                # pws_log.debug(f"B97171 processing line: {line}")
                try:
                    cmd, rest = line.split(' ', 1)
                except ValueError:
                    cmd = line
                    rest = ''
                match cmd:
                    case 'quit':  # quit listening on TCP port
                        self.transport.close()  # transport does not close without this
                        self._quit_notice.set_result(True)  # quit listening on TCP port
                    case 'close':  # close this connection but keep listening
                        self.transport.close()
                    case 'echo':
                        self.write(f'{rest}')
                    case '':
                        pass
                    case _:
                        pws_log.info(f"B88929 node_{self._node_num} unknown cmd: {repr(cmd)}\n")

    def connection_lost(self, exc):
        """Called when TCP connection is closed by us or by the TCP client."""
        # pws_log.debug(f"B59528 node {self._node_num} connection_lost")
        self._connections.remove(self)
        self.transport = None

    def write(self, data: str):
        if not isinstance(self.transport, asyncio.WriteTransport):
            pws_log.info(f"B17634 node {self._node_num} cannot write because connection is closed")
            return
        try:
            self.transport.write(data.encode())
        except Exception as e:
            pws_log.info(f"B45119 node {self._node_num} exception {e}")

    def close(self):
        """Close this connection but keep listening."""
        if self.transport is None:
            return
        self.transport.close()  # close this connection but keep listening


if __name__ == '__main__':
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "node_1":  # callled from exec_and_forward_tcp()
            data = nodes()
            result = asyncio.run(data.node_1(28922))
        else:
            pws_log.error("B98803 invalid command-line arguments; run via pytest")
    except KeyboardInterrupt:
        pws_log.info("B22858 keyboard interrupt")
