FROM python:3.11-slim

WORKDIR /app

# Requirements: command-center ha python-telegram-bot in più
COPY deploy/requirements.txt requirements_cc.txt
COPY deploy-agents/requirements.txt requirements_agents.txt
RUN pip install --no-cache-dir -r requirements_cc.txt -r requirements_agents.txt

# Moduli da deploy-agents (necessari per import diretto csuite)
COPY deploy-agents/core ./core
COPY deploy-agents/csuite ./csuite
COPY deploy-agents/intelligence ./intelligence

# Entry point command-center
COPY deploy/command_center_unified.py .

CMD ["python", "command_center_unified.py"]
