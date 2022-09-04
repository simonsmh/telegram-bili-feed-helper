FROM python:3.10

WORKDIR /usr/src/app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
ENV BILI_API="https://api.bilibili.com" TOKEN="" PORT=8443
ENV DOMAIN=
ENV DATABASE_URL=
ENTRYPOINT ["python", "main.py"]
