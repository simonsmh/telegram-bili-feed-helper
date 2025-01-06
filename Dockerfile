FROM python:3.13

WORKDIR /usr/src/app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && apt-get clean
COPY pyproject.toml poetry.lock ./
RUN pip install poetry && poetry install --only main --no-root --no-directory --no-cache
COPY . .
ENTRYPOINT ["poetry", "run", "python", "-m", "biliparser"]
