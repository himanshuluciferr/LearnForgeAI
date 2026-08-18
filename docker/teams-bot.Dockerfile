FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY teams_bot/ ./teams_bot/

EXPOSE 3978
CMD ["python", "-m", "teams_bot.app"]
