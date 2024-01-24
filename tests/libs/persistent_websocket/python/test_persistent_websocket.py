import random
import libs.persistent_websocket.python.persistent_websocket as persistent_websocket


def test_unmod() -> None:
    for win in [10, 100, 1000, 10_000, 16384, 32768, 8322]:
        for _ in range(0, 100_000):
            short = random.randint(0, win - 1)
            long = random.randint(0, 0xFFFFFF)
            n = persistent_websocket.unmod(short, long, win)
            assert n % win == short
            assert abs(long - n) <= win // 2
            # print(f"unmod({short}, {long}, {win}) == {n}")
