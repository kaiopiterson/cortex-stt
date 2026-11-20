# CortexLab STT Engine — v0.3.0 RC

Motor próprio de transcrição, API-first, com FastAPI e Faster-Whisper.
Servidor Windows/Docker Desktop com GPU NVIDIA; edge e SDK são futuros.

## Instalação
Use Docker Desktop Linux/WSL2 e driver NVIDIA compatível.
Copie .env.example somente em uma instalação nova. Configure STT_API_KEY
e opcionalmente STT_ADMIN_KEY com segredos independentes.
Execute docker compose config --quiet, docker compose build stt e
docker compose up -d --no-deps stt. Consulte /ready (modelo e banco) e os logs.
Interface de teste em / (microfone via AudioWorklet ou arquivo).
Primeiro boot baixa o modelo. Não exponha administração no túnel público.

## Contratos
REST POST /v1/audio/transcriptions: multipart file, model, language, prompt,
mode (command/dictation) e hotwords JSON opcional. Bearer obrigatório.
WebSocket /v1/audio/stream: handshake JSON api_key, language, mode
(stream/utterance), hotwords opcionais. Aguarde ready, envie PCM16 mono
16 kHz binário, finish em texto para finalizar. Protocolo 1.1 preservado.
POST /v1/auth/token troca chave individual por token temporário (900s),
com `subject` opcional (usuário ou dispositivo) para cotas individuais.
Clientes antigos podem usar chave legada, nunca embutida em APK público.
Consulte docs/contract.md e docs/gateway.md.

## Operação
/admin: painel de gestão (somente redes permitidas); /metrics com token
somente leitura; auditoria em /v1/admin/audit. Cotas por app e por sujeito,
SQLite WAL com gravação em lote, retenção e backup (python -m app.db_backup),
um worker Uvicorn. Stack Prometheus/Grafana: perfil `observability`.
Veja docs/dashboard.md, docs/observability.md e docs/release-v0.3.md.

## Validação
Python 3.11+. Instale com
`python -m pip install --require-hashes --only-binary=:all: -r requirements-dev.lock`
e execute pip check, pytest, ruff check app tests, ruff format --check app tests,
mypy app e pip-audit -r requirements.lock --require-hashes. Veja docs/dependencies.md.
CI e benchmark GPU são gates de release, não resultados presumidos.
