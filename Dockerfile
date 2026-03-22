FROM python:3.12-slim

WORKDIR /app

# 시스템 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 의존성 먼저 설치 (캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY . .

# 로그/데이터 디렉토리 생성
RUN mkdir -p logs data

EXPOSE 8004

# 기본: 웹 관리자 모드로 실행
CMD ["python", "main.py", "--mode", "web"]
