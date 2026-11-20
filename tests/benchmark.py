"""Manual HTTP latency benchmark."""

import argparse
import asyncio
import math
import os
import statistics
import time
from pathlib import Path

import httpx


async def main(args):
    raw = Path(args.audio).read_bytes()
    async with httpx.AsyncClient(timeout=300) as client:

        async def one():
            started = time.perf_counter()
            response = await client.post(
                args.url + "/v1/audio/transcriptions",
                headers={"Authorization": "Bearer " + os.environ["STT_API_KEY"]},
                files={"file": (Path(args.audio).name, raw)},
                data={"language": "pt"},
            )
            response.raise_for_status()
            return (time.perf_counter() - started) * 1000

        await one()
        for concurrency in (1, 4, 10):
            results = []
            for _ in range(args.rounds):
                results.extend(
                    await asyncio.gather(
                        *(one() for _ in range(concurrency)), return_exceptions=True
                    )
                )
            ok = sorted(value for value in results if isinstance(value, float))
            print(
                concurrency,
                "concorrentes",
                "erros",
                len(results) - len(ok),
                "p50 ms",
                round(statistics.median(ok)) if ok else None,
                "p95 ms",
                round(ok[math.ceil(0.95 * len(ok)) - 1]) if ok else None,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--rounds", type=int, default=3)
    asyncio.run(main(parser.parse_args()))
