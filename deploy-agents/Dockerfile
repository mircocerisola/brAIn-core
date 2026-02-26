FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia agents_runner.py + tutti i moduli
COPY agents_runner.py .
COPY core/ ./core/
COPY intelligence/ ./intelligence/
COPY execution/ ./execution/
COPY marketing/ ./marketing/
COPY memory/ ./memory/
COPY finance/ ./finance/
COPY csuite/ ./csuite/
COPY ethics/ ./ethics/

CMD ["python", "agents_runner.py"]
