FROM python:3.10-slim

WORKDIR /opt/kelvin/app 
COPY . /opt/kelvin/app
RUN pip install -r requirements.txt

ENTRYPOINT python main.py