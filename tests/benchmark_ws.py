"""Measure live STT WebSocket latency using PCM WAV input."""

import argparse
import asyncio
import json
import os
import time
import wave
from pathlib import Path

import websockets


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 3)


def load_pcm(path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
            raise ValueError("WAV must be 16 kHz mono PCM16")
        return wav.readframes(wav.getnframes())


async def session(args, pcm):
    result = {"ttft_ms": None, "final_ms": None, "rtf": None, "busy": 0, "ok": False}
    try:
        async with websockets.connect(args.url, open_timeout=10) as ws:
            await ws.send(json.dumps({"api_key": args.key, "mode": args.mode, "language": "pt"}))
            ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if ready.get("type") != "ready":
                result["busy"] += int(ready.get("code") == "server_busy")
                return result
            started = time.monotonic()
            finish = None

            async def send_audio():
                nonlocal finish
                for offset in range(0, len(pcm), 3200):
                    await ws.send(pcm[offset : offset + 3200])
                    if not args.accelerated:
                        await asyncio.sleep(len(pcm[offset : offset + 3200]) / 32000)
                finish = time.monotonic()
                await ws.send("finish")

            async def receive():
                while True:
                    event = json.loads(await ws.recv())
                    now = time.monotonic()
                    if event.get("code") == "server_busy":
                        result["busy"] += 1
                    if event["type"] == "partial" and result["ttft_ms"] is None:
                        result["ttft_ms"] = (now - started) * 1000
                    if event["type"] == "error":
                        return
                    if event["type"] == "final":
                        if finish is not None:
                            result["final_ms"] = (now - finish) * 1000
                            result["ok"] = True
                        result["rtf"] = event.get("rtf")
                        return

            sender = asyncio.create_task(send_audio())
            try:
                await asyncio.wait_for(receive(), args.timeout)
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
    except (OSError, TimeoutError, websockets.exceptions.WebSocketException):
        pass
    return result


def summarize(rows):
    summary = {}
    for metric in ("ttft_ms", "final_ms", "rtf"):
        values = [row[metric] for row in rows if row[metric] is not None]
        summary[metric] = {f"p{p}": percentile(values, p) for p in (50, 95, 99)}
    summary["sessions"] = len(rows)
    summary["successful"] = sum(row["ok"] for row in rows)
    summary["server_busy_rate"] = sum(row["busy"] > 0 for row in rows) / len(rows)
    return summary


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", nargs="+", type=Path)
    parser.add_argument("--url", default="ws://127.0.0.1:8000/v1/audio/stream")
    parser.add_argument("--mode", choices=["stream", "utterance"], default="stream")
    parser.add_argument("--sessions", type=int, default=1)
    parser.add_argument("--accelerated", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--json", type=Path, default=Path("benchmark-ws.json"))
    args = parser.parse_args()
    args.key = os.environ.get("STT_API_KEY")
    if not args.key or args.sessions < 1:
        parser.error("STT_API_KEY and positive --sessions required")
    clips = [load_pcm(path) for path in args.wav]
    rows = await asyncio.gather(
        *(session(args, clips[i % len(clips)]) for i in range(args.sessions))
    )
    report = summarize(rows)
    args.json.write_text(
        json.dumps({"mode": args.mode, "summary": report, "sessions": rows}, indent=2)
    )
    print(f"{'Metric':15} {'P50':>12} {'P95':>12} {'P99':>12}")
    for metric in ("ttft_ms", "final_ms", "rtf"):
        print(f"{metric:15}" + "".join(f"{str(report[metric][f'p{p}']):>12}" for p in (50, 95, 99)))
    print(
        f"Success: {report['successful']}/{report['sessions']}; server_busy: {report['server_busy_rate']:.1%}"
    )


if __name__ == "__main__":
    asyncio.run(main())
