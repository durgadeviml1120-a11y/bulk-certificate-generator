FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
# Single worker on purpose: background jobs run in-process (see README, "Design decisions").
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
