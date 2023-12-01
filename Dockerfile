FROM python:3.12

WORKDIR /usr/src/app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
ENV PORT=9000 \
    BILI_API="https://api.bilibili.com" \
    DATABASE_URL="sqlite://cache.db" \
    DOMAIN="" \
    TOKEN="" \
    VIDEO_SIZE_LIMIT="" \
    API_BASE_URL="" \
    API_BASE_FILE_URL="" \
    LOCAL_MODE=""
ENTRYPOINT ["python"]
CMD ["main.py"]
