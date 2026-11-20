# Observabilidade v0.3

Configure STT_METRICS_TOKEN (somente leitura, ≥ 32 caracteres, distinto das
demais chaves) para o Prometheus. /metrics aceita esse token ou STT_ADMIN_KEY;
o token de métricas recebe 401 em todas as rotas /v1/admin/*. Sem nenhum dos
dois, /metrics responde 404. Acesso limitado a STT_ADMIN_ALLOWED_NETWORKS.
Não exponha essas rotas no túnel público: negue /metrics e /v1/admin/* no
ingress antes da regra geral do STT, ou use rede de gerenciamento privada.
Nenhuma regra de túnel é alterada automaticamente.

## Stack pronta (perfil `observability`)

    openssl rand -hex 32 > observability/secrets/stt_metrics_token   # mesmo valor de STT_METRICS_TOKEN
    openssl rand -base64 24 > observability/secrets/grafana_admin_password
    docker compose --profile observability up -d

Prometheus v3.5.0 (127.0.0.1:9090, retenção 30 dias, alertas em
observability/alerts.yml) e Grafana 12.1.0 (127.0.0.1:3000, usuário admin,
dashboard "CortexLab STT" provisionado). Os segredos ficam em
observability/secrets/, ignorado pelo Git. O perfil não altera o túnel.

## Métricas (prometheus_client)

| Série | Labels | Uso |
|---|---|---|
| stt_http_requests_total | route, method, status | volume e erros por rota |
| stt_http_request_duration_seconds | route | latência fim a fim do servidor |
| stt_stream_first_partial_seconds | — | 1º áudio → 1º parcial (meta 500 ms) |
| stt_stream_final_latency_seconds | mode | finish → final (meta 1 s) |
| stt_inference_duration_seconds, stt_inference_rtf | — | custo da inferência |
| stt_queue_wait_seconds | — | espera por worker |
| stt_rejections_total | reason | unauthorized, forbidden_scope, rate_limited, quota_exceeded, server_busy, lagging, unavailable |
| stt_auth_failures_total | surface | rest, websocket, token, admin, metrics |
| stt_tokens_issued_total, stt_stream_sessions_total | —, mode | emissão e sessões |
| stt_hallucination_segments_filtered_total | — | filtro de alucinações |
| stt_inference_queue_depth, stt_inference_active, stt_gpu_*, stt_degraded | — | gauges |

Labels usam o template da rota (rotas desconhecidas viram `unmatched`);
app_id, sujeito, tokens e texto nunca aparecem em métricas.

PromQL:

    histogram_quantile(0.95, sum by (le) (rate(stt_stream_final_latency_seconds_bucket[5m])))
    histogram_quantile(0.95, sum by (le) (rate(stt_stream_first_partial_seconds_bucket[5m])))
    sum by (reason) (rate(stt_rejections_total[5m]))
    stt_gpu_memory_used_bytes / stt_gpu_memory_total_bytes

Contadores são locais ao processo e reiniciam com o serviço.
NVML é opcional (nvidia-ml-py, driver NVIDIA e acesso a NVML no container).
GPU indisponível não equivale a utilização zero.

## Logs

JSON por linha (`STT_LOG_FORMAT=json`, padrão; `text` para desenvolvimento),
com `request_id` (recebido em `X-Request-ID` válido ou gerado, e devolvido na
resposta). O log de acesso registra método, rota, status e duração; nunca
credenciais, áudio ou transcrições. O access log do Uvicorn é desativado.

## Auditoria

GET /v1/admin/usage?limit=100 retorna buckets persistentes de consumo
(limite máximo 1000), não uma trilha de eventos individuais. Dias e meses
contêm as mesmas amostras agregadas em períodos diferentes: não somá-los.
Não inclui áudio, texto, chaves ou tokens. Com sujeito, cada linha traz `subject`.
Retenção: ver docs/gateway.md. Eventos administrativos: GET /v1/admin/audit.
