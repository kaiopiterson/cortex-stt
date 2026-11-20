# CortexLab STT Engine — v0.3.0 RC

Motor próprio de transcrição, API-first, com FastAPI e Faster-Whisper.
Servidor Windows/Docker Desktop com GPU NVIDIA; edge e SDK são futuros.

## Instalação
Use Docker Desktop Linux/WSL2 e driver NVIDIA compatível.
Copie .env.example somente em uma instalação nova. Configure STT_API_KEY
e opcionalmente STT_ADMIN_KEY com segredos independentes.
Execute docker compose config --quiet, docker compose build stt e
docker compose up -d --no-deps stt. Consulte /health e os logs.
Primeiro boot baixa o modelo. Não exponha administração no túnel público.

## Contratos
REST POST /v1/audio/transcriptions: multipart file, model, language, prompt,
mode (command/dictation) e hotwords JSON opcional. Bearer obrigatório.
WebSocket /v1/audio/stream: handshake JSON api_key, language, mode
(stream/utterance), hotwords opcionais. Aguarde ready, envie PCM16 mono
16 kHz binário, finish em texto para finalizar. Protocolo 1.1 preservado.
POST /v1/auth/token troca chave individual por token temporário (900s).
Clientes antigos podem usar chave legada, nunca embutida em APK público.
Consulte docs/contract.md e docs/gateway.md.

## Operação
/admin: painel de gestão; /metrics e /v1/admin/usage protegidos.
Cotas por identidade, SQLite WAL, um worker Uvicorn.
Veja docs/dashboard.md, docs/observability.md e docs/release-v0.3.md.

## Validação
Instale requirements-dev.txt com --only-binary=:all:; execute pip check,
pytest, ruff check app tests, ruff format --check app tests, mypy app.
CI e benchmark GPU são gates de release, não resultados presumidos.
