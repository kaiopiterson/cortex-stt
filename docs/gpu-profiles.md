# Perfis CUDA — 4 GB de VRAM

Comece com small, int8_float16, um worker e beam_size=1.
Avalie dois workers somente após medir VRAM e P95.
large-v3-turbo com int8_float16 e um worker é experimental em 4 GB;
não há garantia de que caiba na GPU. Evite dois workers até medir.
A GTX 1650 pode não oferecer int8_float16 no CTranslate2; confira
get_supported_compute_types("cuda", 0). Se indisponível, use int8_float32.
CPU: int8. O arquivo .env existente não é alterado automaticamente.
NVML é opcional (pacote nvidia-ml-py); logs de VRAM são best effort
e o índice NVML pode diferir do índice CUDA em ambientes remapeados.

## Fallback automático (AUD-16)

Padrão: `STT_WORKERS=1` (cada worker replica o modelo na VRAM).
Ordem de carga: compute configurado → `int8_float32` na CUDA → CPU `int8`
(desligue o último com `STT_CPU_FALLBACK=false`). Em modo degradado o serviço
continua respondendo, registra um aviso com as tentativas, expõe
`stt_degraded 1` em /metrics, informa `processing.degraded: true` e o
`device` real nas respostas. O alerta `SttDegradedCompute` dispara no
Prometheus. Em CPU a latência é muito maior: trate como contingência.

## Filtro de alucinações (AUD-17)

Segmentos com `no_speech_prob ≥ STT_HALLUCINATION_NO_SPEECH_THRESHOLD` (0,6)
e `avg_logprob ≤ STT_HALLUCINATION_LOGPROB_THRESHOLD` (-1,0) são descartados,
assim como frases típicas de alucinação do Whisper em PT-BR
(`STT_HALLUCINATION_BLOCKLIST`, JSON; comparação sem acentuação de caixa e
pontuação, frase inteira ou prefixo). Quando algo é filtrado, a resposta
traz `filtered_segments > 0` e `needs_review: true`, e o contador
`stt_hallucination_segments_filtered_total` aumenta. Desligue com
`STT_HALLUCINATION_FILTER=false`. Limiares não foram calibrados com áudio
real nesta versão: ajuste após medir WER e falsos positivos.
