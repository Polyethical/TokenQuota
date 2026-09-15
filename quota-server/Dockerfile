FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TOKENQUOTA_CONFIG=/config/config.toml HOST=0.0.0.0 PORT=8080

# CI sets this to the library's main-branch tarball so the image can be built
# before the library is on PyPI. Releases leave it empty and use PyPI.
ARG TOKENQUOTA_URL=""

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN if [ -n "$TOKENQUOTA_URL" ]; then pip install --no-cache-dir "tokenquota[redis] @ $TOKENQUOTA_URL"; fi \
 && pip install --no-cache-dir . \
 && useradd --create-home --uid 10001 app

USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz')"
CMD ["tokenquota-server"]
