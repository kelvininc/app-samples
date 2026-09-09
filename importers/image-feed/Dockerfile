FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /opt/kelvin/app

# Install dependencies
COPY requirements.txt /opt/kelvin/app/
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy the remaining project files (including the images/ folder this connector replays)
COPY . /opt/kelvin/app

ENTRYPOINT ["python", "main.py"]
