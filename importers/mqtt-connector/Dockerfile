FROM --platform=${TARGETPLATFORM:-linux/amd64} python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /opt/kelvin/app

# Install dependencies
COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the remaining project files
COPY . /opt/kelvin/app

ENTRYPOINT ["python", "main.py"]