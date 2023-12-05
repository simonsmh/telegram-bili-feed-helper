FROM python:3.12

WORKDIR /usr/src/app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
ENV PORT=9000 \
    TOKEN=""
ENTRYPOINT ["python"]
CMD ["main.py"]
