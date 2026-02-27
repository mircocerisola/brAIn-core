FROM python:3.11-slim

WORKDIR /app

# System deps: git + Node.js 22 + Claude Code CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia agents_runner.py + cloud_code_runner.py + tutti i moduli
COPY agents_runner.py .
COPY cloud_code_runner.py .
COPY core/ ./core/
COPY intelligence/ ./intelligence/
COPY execution/ ./execution/
COPY marketing/ ./marketing/
COPY memory/ ./memory/
COPY finance/ ./finance/
COPY csuite/ ./csuite/
COPY ethics/ ./ethics/

CMD ["python", "agents_runner.py"]
