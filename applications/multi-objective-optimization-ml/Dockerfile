FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /opt/kelvin/app
COPY requirements.txt /opt/kelvin/app/
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt
COPY . /opt/kelvin/app

ENTRYPOINT ["python", "main.py"]
