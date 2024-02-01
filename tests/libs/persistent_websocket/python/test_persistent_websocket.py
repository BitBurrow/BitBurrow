import asyncio
import logging
import sys
import websockets

import libs.persistent_websocket.python.persistent_websocket as persistent_websocket

display_messages = False  # print PersistentWebSocket messages sent and received
pws_log = logging.getLogger('persistent_websocket')
pws_log.setLevel(logging.INFO)  # use logging.DEBUG to see details
console_handler = logging.StreamHandler()
formatter = logging.Formatter(
    '%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s',
    datefmt='%H:%M:%S',
)
console_handler.setFormatter(formatter)
pws_log.addHandler(console_handler)
pws_log.debug("err message")

item_count = 250  # directly affects time required for test


async def test_messaging():
    """Set up 2-node testing lab and run tests."""
    data = nodes()
    #
    ### node_2
    # connection_list_remote = list()
    data.listener_tasks.append(asyncio.create_task(data.node_2_websocket_server()))
    # await asyncio.sleep(0.5)  # make sure node_2 is listening
    #
    ### node_3
    messages3 = persistent_websocket.PersistentWebsocket('nd_3', pws_log)
    url = 'ws://127.0.0.1:18732'
    data.listener_tasks.append(asyncio.create_task(data.node_x_listener(3, None, messages3, url)))
    #
    ### waiting
    print("waiting for speaker tasks to begin")
    while len(data.speaker_tasks) < 2:
        # await asyncio.sleep(0.1)  # wait for tasks to start
        await asyncio.sleep(3.1)  # wait for tasks to start
        print(f"still waiting (have {len(data.speaker_tasks)} tasks)")
    print("waiting for speaker tasks to finish")
    await asyncio.gather(*data.speaker_tasks, return_exceptions=True)  # let speakers finish
    print("waiting up to 15 seconds for success from listener tasks")
    wait_time = 0.0
    while len(data.successes) < 2 and wait_time < 15.0:
        await asyncio.sleep(0.1)
        wait_time += 0.1
    print("canceling listeners")
    for t in data.listener_tasks:
        t.cancel()
    await asyncio.gather(*data.listener_tasks, return_exceptions=True)  # wait for them to die
    if len(data.errors) > 0:
        assert False, data.errors[0]
    if len(data.successes) < 2:
        assert False, "a listener task didn't didn't receive all of its items"
    print("done waiting on tasks")


class nodes:
    """Simulated nodes for testing communications between BitBurrow pieces.

    node_2: plays role of the BitBurrow hub
        PersistentWebSocket in from node_3 port 18732
    node_3: plays role of the BitBurrow app
        PersistentWebSocket out to node_2 port 18732
    """

    def __init__(self):
        self.speaker_tasks = list()
        self.listener_tasks = list()
        self.successes = list()
        self.next_n = dict()  # {node: next_n}
        self.errors = list()

    async def node_2_websocket_server(self):
        messages = persistent_websocket.PersistentWebsocket('nd_2', pws_log)  # outlives WebSocket
        print("node_2 is listening (WebSocket)")
        async with websockets.serve(
            lambda ws, path: self.node_x_listener(2, ws, messages, ''), '127.0.0.1', 18732
        ):
            await asyncio.Future()  # run forever

    async def node_x_listener(self, node, ws, messages, url):
        self.speaker_tasks.append(asyncio.create_task(self.node_x_speaker(node, messages)))
        print(f"started node_{node}_speaker (now have {len(self.speaker_tasks)} tasks)")
        items_received = 0
        async for m in (messages.connected(ws) if ws else messages.connect(url)):
            n = int(m.decode())
            if node in self.next_n and n != self.next_n[node]:
                self.errors.append(
                    f"items out of order at node_{node} "
                    + f"(expecting {self.next_n[node]}, got {n})"
                )
            self.next_n[node] = n + 1
            if display_messages:
                column = " " * 29 * (node - 2)
                print(f"            {column}{n}")
            items_received += 1
            if items_received == item_count:
                self.successes.append(f'node_{node}')  # successfully received all items

    async def node_x_speaker(self, node, messages: persistent_websocket.PersistentWebsocket):
        if node == 2:
            await asyncio.sleep(0.500)  # for testing, offset one of the streams
        begin = 10_000_000 * node
        for n in range(begin, begin + item_count):
            await asyncio.sleep(0.010)
            skip = -1  # to test the test, set skip to `item_count - 1` or `100`
            if n != begin + skip:
                await messages.send(str(n))
                if display_messages:
                    column = " " * 29 * (3 - node)
                    print(f"{column}{n} → ")


if __name__ == '__main__':
    try:
        if len(sys.argv) == 1:  # run directly (similar to running `pytest`)
            asyncio.run(test_messaging())
        else:
            print("invalid command-line arguments")
    except KeyboardInterrupt:
        print("keyboard interrupt")
