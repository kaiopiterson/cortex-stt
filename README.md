# CortexLab STT Engine — v0.1.0 MVP

Motor próprio de transcrição de voz. Visão: Edge-First, Cloud-Assisted, Offline-Capable, API-First. O MVP é exclusivamente servidor; SDK e inferência local estão no roadmap.

## Instalação (Windows / PowerShell)
Docker Desktop com containers Linux, backend WSL2 e driver NVIDIA compatível.
```powershell
Copy-Item .env.example .env
notepad .env
# Configure uma chave aleatória longa.
nvidia-smi
docker compose up -d --build
docker compose logs -f stt
```
Interface: http://localhost:8000; Swagger: /docs; saúde: /health. Primeiro boot baixa o modelo. Para CPU, remova gpus: all e configure STT_DEVICE=cpu e STT_COMPUTE=int8.

## API
POST /v1/audio/transcriptions: multipart file, model (small ou whisper-1), language (pt ou vazio para auto), prompt opcional. Bearer obrigatório. Retorna text, segments, language, processing_ms. Compatibilidade OpenAI parcial. Limite de 25 MiB.
WebSocket /v1/audio/stream: primeira mensagem JSON {"api_key":"...","language":"pt"}, aguarde ready, envie PCM signed 16-bit little-endian mono 16 kHz binário. partial substitui o texto anterior; envie finish para final. Limite de 120s e inatividade de 20s. Retranscreve o buffer inteiro a cada ~2s. ScriptProcessor legado na interface. Sem CORS externo por padrão.

## Benchmark
```powershell
py -m pip install httpx
$env:STT_API_KEY='sua-chave'
py tests/benchmark.py amostra.wav --rounds 5
```
Mede REST após warmup com 1/4/10 requisições concorrentes. Não validado em GPU. Metas de latência não comprovadas. Não exponha diretamente à internet antes de revisar segurança. O túnel opcional requer configuração própria e HTTPS.

Veja [ROADMAP.md](ROADMAP.md), [ARCHITECTURE.md](ARCHITECTURE.md) e [SECURITY.md](SECURITY.md).
