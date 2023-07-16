FROM python:3.11

WORKDIR /usr/src/app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
ENV BILI_API="https://api.bilibili.com" PORT=9000 DATABASE_URL="sqlite://cache.db" DOMAIN="" TOKEN=""
ENTRYPOINT ["python", "main.py"]
