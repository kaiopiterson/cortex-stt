# Dependências e locks

- `requirements.txt` / `requirements-dev.txt`: dependências diretas, fixadas
  nas versões validadas.
- `requirements.lock` / `requirements-dev.lock`: todas as dependências
  (inclusive transitivas) com hashes SHA-256, resolvidas de forma universal
  para Python ≥ 3.11 e apenas wheels.
- O Dockerfile e o CI instalam somente a partir dos locks, com
  `--require-hashes --only-binary :all:`.

## Runtime Python
Python 3.11 em produção e no CI (matriz 3.11 e 3.12). O Python 3.10 saiu de
suporte em outubro de 2026 e não recebe as correções de segurança de
dependências como `filelock` e `fastapi`. A imagem continua baseada em
`nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` e instala um Python 3.11
standalone via `uv` (versão fixada), sem alterar CUDA/cuDNN.

## Atualizar
```
python -m pip install uv==0.11.32
# 1) edite a versão em requirements*.txt
# 2) regenere os locks
uv pip compile --universal --python-version 3.11 --generate-hashes \
  --only-binary :all: --no-header -o requirements.lock requirements.txt
uv pip compile --universal --python-version 3.11 --generate-hashes \
  --only-binary :all: --no-header -o requirements-dev.lock requirements-dev.txt
# 3) valide
python -m pip install --require-hashes --only-binary=:all: -r requirements-dev.lock
pip-audit -r requirements.lock --require-hashes
python -m pytest
```
O job `dependency-audit` falha se os locks não corresponderem aos arquivos
`requirements*.txt` ou se houver vulnerabilidade conhecida em produção.

## Compatibilidade verificada (20/11/2026)
fastapi 0.165.0, starlette 1.10.0, python-multipart 0.0.34,
requests 2.34.2, slowapi 0.1.9 + limits 5.8.0 (rate limit testado),
pytest 9.2.0. Sem vulnerabilidades conhecidas no lock de produção.
