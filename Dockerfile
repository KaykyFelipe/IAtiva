# Imagem de DESENVOLVIMENTO do IAtiva (Etapa 1). Não é imagem de produção (ver Etapa 9).
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && apt-get clean \
    && python3 -m venv /opt/venv

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

# Usuário sem privilégios; só a pasta var/ (base sintética) é gravável.
RUN useradd --create-home --uid 10001 iativa \
    && mkdir -p /app/var \
    && chown -R iativa:iativa /app/var
USER iativa

EXPOSE 8000
