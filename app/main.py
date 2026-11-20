import asyncio, io, os, secrets, time
from contextlib import asynccontextmanager
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from faster_whisper import WhisperModel
from pathlib import Path

KEY=os.getenv('STT_API_KEY','')
MODEL=os.getenv('STT_MODEL','small')
workers=int(os.getenv('STT_WORKERS','2'))
slots=asyncio.Semaphore(workers)
sessions=asyncio.Semaphore(int(os.getenv('STT_SESSIONS','10')))
MAX=25*1024*1024

def authorized(key):
    return bool(KEY and key and secrets.compare_digest(key, 'Bearer '+KEY))

def auth(authorization: str | None):
    if not authorized(authorization): raise HTTPException(401,'Invalid API key')

@asynccontextmanager
async def lifespan(app):
    if not KEY or KEY=='replace-with-random-secret': raise RuntimeError('Configure STT_API_KEY')
    app.state.model=await asyncio.to_thread(WhisperModel, MODEL, device=os.getenv('STT_DEVICE','cuda'), compute_type=os.getenv('STT_COMPUTE','int8_float32'), num_workers=workers)
    yield
app=FastAPI(title='CortexLab STT', lifespan=lifespan)

def infer(audio, language, prompt=None):
    segs, info=app.state.model.transcribe(audio, language=language or None, initial_prompt=prompt, vad_filter=True, vad_parameters={'min_silence_duration_ms':350}, beam_size=1, condition_on_previous_text=False)
    items=[{'start':s.start,'end':s.end,'text':s.text} for s in segs]
    return {'text':''.join(s['text'] for s in items).strip(),'segments':items,'language':info.language}

async def transcribe(audio, language, prompt=None):
    async with slots:
        job=asyncio.create_task(asyncio.to_thread(infer,audio,language,prompt))
        try: return await asyncio.shield(job)
        except asyncio.CancelledError:
            await job
            raise

@app.get('/health')
async def health(): return {'status':'ok','model':MODEL}
@app.get('/v1/models')
async def models(authorization: str|None=Header(None)):
    auth(authorization); return {'object':'list','data':[{'id':MODEL,'object':'model'}]}
@app.post('/v1/audio/transcriptions')
async def rest(file:UploadFile=File(...), model:str=Form(MODEL),language:str=Form('pt'),prompt:str|None=Form(None),authorization:str|None=Header(None)):
    auth(authorization)
    if model not in (MODEL,'whisper-1'): raise HTTPException(400,'Unsupported model')
    data=await file.read(MAX+1)
    if len(data)>MAX: raise HTTPException(413,'Audio too large')
    async with sessions:
        start=time.perf_counter()
        try: result=await transcribe(io.BytesIO(data),language,prompt)
        except Exception: raise HTTPException(422,'Unable to transcribe audio')
        result['processing_ms']=round((time.perf_counter()-start)*1000)
        return result

@app.websocket('/v1/audio/stream')
async def stream(ws:WebSocket):
    await ws.accept()
    acquired=False
    try:
        cfg=await asyncio.wait_for(ws.receive_json(),5)
        if not authorized('Bearer '+str(cfg.get('api_key',''))):
            await ws.close(1008); return
        try: await asyncio.wait_for(sessions.acquire(),0.2)
        except asyncio.TimeoutError: await ws.close(1013); return
        acquired=True
        language=cfg.get('language','pt'); buf=bytearray(); last=0; started=time.monotonic()
        await ws.send_json({'type':'ready','sample_rate':16000})
        while time.monotonic()-started<120:
            msg=await asyncio.wait_for(ws.receive(),20)
            if msg['type']=='websocket.disconnect': break
            chunk=msg.get('bytes')
            final=msg.get('text')=='finish'
            if chunk:
                if len(chunk)%2 or len(chunk)>320000: await ws.close(1009); return
                buf.extend(chunk)
            if len(buf)>3840000: await ws.close(1009); return
            if final or len(buf)-last>=64000:
                if buf:
                    audio=np.frombuffer(bytes(buf),dtype='<i2').astype(np.float32)/32768
                    result=await transcribe(audio,language)
                else: result={'text':'','segments':[]}
                await ws.send_json({'type':'final' if final else 'partial',**result})
                last=len(buf)
            if final: await ws.close(); return
    except (WebSocketDisconnect, asyncio.TimeoutError): pass
    except Exception:
        try: await ws.close(1011)
        except Exception: pass
    finally:
        if acquired: sessions.release()

@app.get('/',response_class=HTMLResponse)
async def ui(): return Path(__file__).with_name('index.html').read_text()
