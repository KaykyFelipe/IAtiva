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

# Microsoft ODBC Driver 18 para SQL Server (leitura do ERP; Etapa 3 só metadados).
# ACCEPT_EULA=Y aceita a licença do driver da Microsoft, exigida pelo pacote.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/ubuntu/24.04/prod.list -o /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc libgssapi-krb5-2 \
    && apt-mark manual libgssapi-krb5-2 \
    && apt-get purge -y curl gnupg && apt-get autoremove -y && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && ! ldd /opt/microsoft/msodbcsql18/lib64/libmsodbcsql-18*.so* | grep -q "not found"

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
