FROM python:3.13-slim
WORKDIR /app
COPY . .
# Editable install keeps scenarios/ and data/ next to the code, where the app looks for them
RUN pip install --no-cache-dir -e .
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "landpromo crm serve --host 0.0.0.0 --port ${PORT:-8000} --db /data/crm.sqlite3"]
