FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py app.yaml ./
COPY ui_schemas/ ui_schemas/
ENTRYPOINT ["python", "main.py"]
