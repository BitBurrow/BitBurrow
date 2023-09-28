#!/usr/bin/env python3
#
# To run this file:
#   cd libs/python/persistent_websocket/
#   poetry init --no-interaction
#   poetry add websockets
#   poetry run ./test_client.py wss://vxm.example.org:8443/v1/pw/55

import asyncio
import logging
import sys
import persistent_websocket
import traceback

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d_%H:%M:%S',
)


async def listener(messenger, url):
    try:
        async for m in messenger.connect(url):
            print(f"------------------------------------------------ incoming: {m.decode()}")
    except Exception as err:
        print(f"B38924 error: {err}")
        sys.exit(1)


async def speaker(messenger):
    to_send = 26_000_000
    while True:
        await asyncio.sleep(0.100)
        print(f"sending: {to_send}")
        await messenger.send(str(to_send))
        to_send += 1


async def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <WebSocket URL>")
        return
    messenger = persistent_websocket.PersistentWebsocket("")
    # to stress-test PersistentWebsocket, use any combination of these 3:
    # * uncomment this line: messenger.chaos = 50  # 5% chance of closing WebSocket on each send or receive
    # * do the same on the server immediately after PersistentWebsocket(...)
    # * run this on server with Bash:
    # *     while true; do
    # *         echo "$(date --utc '+%Y-%m-%d_%H:%M:%S') DOWN"
    # *         sudo ip link set eth0 down
    # *         sleep $((1 + RANDOM % 200))
    # *         echo "$(date --utc '+%Y-%m-%d_%H:%M:%S') UP"
    # *         sudo ip link set eth0 up
    # *         sleep $((1 + RANDOM % 300))
    # *     done
    persistent_websocket.logger.setLevel(logging.DEBUG)
    listening = asyncio.create_task(listener(messenger, sys.argv[1]))
    speaking = asyncio.create_task(speaker(messenger))
    await listening
    speaking.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Exiting because ctrl-C was pressed")
