#!/usr/bin/env python3

import asyncio
import logging
import sys
import os

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(base_dir, "libs", "python"))
import persistent_websocket.persistent_websocket as persistent_websocket

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%d_%H:%M:%S')

async def listener(messenger, url):
    async for m in messenger.connect(url):
        print(f"------------------------------------------------ incoming: {m.decode()}")

async def speaker(messenger):
    to_send = 22212
    while True:
        await asyncio.sleep(1.01)
        print(f"sending: {to_send}")
        await messenger.send(str(to_send))
        to_send += 1
    try:
        await messenger.close()
    except Exception:
        print("ctrl-C abort ...")
        pass

async def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <WebSocket URL>")
        return
    messenger = persistent_websocket.PersistentWebsocket()
    listening = asyncio.create_task(listener(messenger, sys.argv[1]))
    speaking = asyncio.create_task(speaker(messenger))
    await listening
    speaking.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Exiting because ctrl-C was pressed")

