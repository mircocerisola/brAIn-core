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

# Playwright browser install (Chromium only, per HTML screenshots)
RUN playwright install chromium --with-deps 2>/dev/null || echo "Playwright install skipped"

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
COPY utils/ ./utils/

# Utente non-root — Claude Code rifiuta --dangerously-skip-permissions come root
RUN useradd -m -u 1001 brainuser && chown -R brainuser:brainuser /app
USER brainuser

CMD ["python", "agents_runner.py"]
