# Release candidate v0.3.0

## Gates obrigatórios
- CI pytest, Ruff lint/format, mypy, pip check em Python 3.10/3.11.
- Build Docker e smoke de imports.
- Testes REST/WS com chave legada, individual e token.
- Testes de expiração, rotação, revogação, concorrência e cotas.
- Inspecionar dependências e imagem por vulnerabilidades; lock real pendente.
- Validar NVML, compute CUDA, latência e carga no GTX 1650.
- Revisar bloqueio de rotas administrativas no túnel.
Não publicar tag antes de passar os gates.

## Deploy e rollback
Antes de atualizar: registrar commit e ID da imagem anterior, revisar compose,
salvar .env fora do Git e fazer backup consistente SQLite (via API backup ou
serviço parado, incluindo WAL quando aplicável). Não apagar volumes.
Construir imagem candidata, subir apenas stt, verificar health, REST, WS,
admin e métricas. Em falha, restaurar imagem e configuração anteriores.
Restaurar banco somente se necessário, com serviço parado e ciência da
perda de alterações posteriores. Não executar docker compose down -v.
PR feature/v0.3-api-gateway -> develop; promoção main após homologação.
