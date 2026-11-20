import argparse, asyncio, os, time, statistics, httpx
from pathlib import Path
async def main(a):
    raw=Path(a.audio).read_bytes()
    async with httpx.AsyncClient(timeout=300) as c:
        async def one():
            t=time.perf_counter()
            r=await c.post(a.url+'/v1/audio/transcriptions',headers={'Authorization':'Bearer '+os.environ['STT_API_KEY']},files={'file':(Path(a.audio).name,raw)},data={'language':'pt'})
            r.raise_for_status();return (time.perf_counter()-t)*1000
        await one()
        for n in (1,4,10):
            results=await asyncio.gather(*(one() for _ in range(n*a.rounds)),return_exceptions=True) if a.rounds==1 else []
            if a.rounds!=1:
                for _ in range(a.rounds): results.extend(await asyncio.gather(*(one() for _ in range(n)),return_exceptions=True))
            ok=sorted(x for x in results if isinstance(x,float));print(n,'concorrentes', 'erros',len(results)-len(ok),'p50 ms',round(statistics.median(ok)) if ok else None,'p95 ms',round(ok[int(npceil(.95*len(ok)))-1]) if ok else None)
def npceil(x): return -int(-x//1)
p=argparse.ArgumentParser();p.add_argument('audio');p.add_argument('--url',default='http://localhost:8000');p.add_argument('--rounds',type=int,default=3);asyncio.run(main(p.parse_args()))
