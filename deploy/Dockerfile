FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY command_center_unified.py .

CMD ["python", "command_center_unified.py"]
