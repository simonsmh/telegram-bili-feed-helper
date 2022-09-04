FROM python:3.10

WORKDIR /usr/src/app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
ENV BILI_API="https://api.bilibili.com" PORT=8443 DATABASE_URL="sqlite://cache.db" DOMAIN="" TOKEN=""
ENTRYPOINT ["python", "main.py"]
