# The ephemera CLI for CI systems without Python:
#   docker run --rm -e EPHEMERA_TOKEN ghcr.io/sabiut/ephemera-cli preview deploy ...
FROM python:3.12-slim
COPY . /src
RUN pip install --no-cache-dir /src && rm -rf /src
USER 65534
ENTRYPOINT ["ephemera"]
