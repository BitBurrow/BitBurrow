import asyncio
import io
import sys
import random
import time
from libs.persistent_websocket.python.persistent_websocket import unmod, Timekeeper, printable_hex


def test_unmod() -> None:
    for win in [10, 100, 1000, 10_000, 16384, 32768, 8322]:
        for _ in range(0, 100_000):
            short = random.randint(0, win - 1)
            long = random.randint(0, 0xFFFFFF)
            n = unmod(short, long, win)
            assert n % win == short
            assert abs(long - n) <= win // 2
            # print(f"unmod({short}, {long}, {win}) == {n}")


def test_timekeeper():
    start = time.time()

    def log(s):
        print(f"{time.time()-start:3.0f}s: {s}")

    async def four_seconds():
        log("            four seconds")

    async def five_seconds():
        log("                         five seconds")

    async def two_seconds():
        log("two seconds")

    async def demo_timekeeper():
        a = Timekeeper.periodic(4, four_seconds)
        b = Timekeeper(5, five_seconds)
        c = Timekeeper.exponential(2, two_seconds, 2, 45)
        log("zero seconds")
        await asyncio.sleep(30)
        log("            canceling four")
        a.cancel()
        await asyncio.sleep(7)
        log("done")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    original_stdout = sys.stdout
    string_buffer = io.StringIO()
    sys.stdout = string_buffer
    loop.run_until_complete(demo_timekeeper())
    sys.stdout = original_stdout
    expected = """
      0s: zero seconds
      2s: two seconds
      4s:             four seconds
      5s:                          five seconds
      6s: two seconds
      8s:             four seconds
     12s:             four seconds
     14s: two seconds
     16s:             four seconds
     20s:             four seconds
     24s:             four seconds
     28s:             four seconds
     30s:             canceling four
     30s: two seconds
     37s: done
    """
    unindented = '\n'.join([l[4:] if l.startswith('    ') else l for l in expected.splitlines()])
    assert string_buffer.getvalue().strip() == unindented.strip()
    string_buffer.close()


def test_printable_hex() -> None:
    chunk_test = (
        "1234\x0056789\x01\x02abcd\nefg\nhi\nhello\n\n"
        "hello\n\n\nshouldn't \\ backslash\xe2\x9c\x94 done\n"
    )
    chunk_test_out = (
        "'1234' 00 '56789' 01 02 'abcd' 0A 65 66 67 0A 68 69 0A 'hello' 0A 0A "
        "'hello' 0A 0A 0A 'shouldn' 27 't \\ backslash' E2 9C 94 ' done' 0A"
    )
    assert printable_hex([ord(c) for c in chunk_test]) == chunk_test_out
