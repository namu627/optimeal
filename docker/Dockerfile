# docker/Dockerfile
# Python 3.12.13 개발 환경 (mise.toml 기준 버전 고정)

FROM python:3.12.13-slim

# 시스템 의존성 (psycopg2-binary, matplotlib 등)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# requirements.txt 먼저 복사 → 의존성 캐시 레이어 분리
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드는 volume으로 마운트 (docker-compose.yml 참고)
# 개발 환경에서는 COPY 불필요

# 개발 환경 기본 대기 명령 (docker-compose command로 재정의 가능)
CMD ["tail", "-f", "/dev/null"]
