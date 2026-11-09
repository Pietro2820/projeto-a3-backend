FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 1. Contorna o bloqueio 403 do CDN Fastly (deb.debian.org) 
# usando o mirror direto brasileiro (ou ftp.debian.org como fallback)
RUN sed -i 's/deb.debian.org/ftp.br.debian.org/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/ftp.debian.org/g' /etc/apt/sources.list

# 2. Instalação do FFmpeg e certificados de segurança
RUN apt-get update --fix-missing && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 3. Instalação das dependências Python (isolado do seu PC)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copia o código (o .dockerignore impede que o .venv local suba)
COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]