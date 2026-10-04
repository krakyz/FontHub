FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV FONTHUB_DATA=/data FONTHUB_HOST=0.0.0.0
CMD ["python", "app.py"]
