FROM python:3

WORKDIR /usr/src/app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
VOLUME /mnt
ENV TOKEN ""
CMD ["python", "main.py"]
