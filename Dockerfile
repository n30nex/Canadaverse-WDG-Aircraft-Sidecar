# syntax=docker/dockerfile:1
FROM debian:trixie-slim AS dump1090

ARG DUMP1090_REF=0339a57b89cd6e61856cbb13ae342c31ae7be5ac
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates git librtlsdr-dev libncurses-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN git init /src/dump1090 \
    && git -C /src/dump1090 remote add origin https://github.com/flightaware/dump1090.git \
    && git -C /src/dump1090 fetch --depth=1 origin "${DUMP1090_REF}" \
    && git -C /src/dump1090 checkout --detach FETCH_HEAD \
    && make -C /src/dump1090 -j"$(nproc)"

FROM python:3.13-slim-trixie

ARG VERSION=1.1.0
LABEL org.opencontainers.image.title="Canadaverse WDG Aircraft Sidecar" \
      org.opencontainers.image.description="Passive ADS-B logger and WDG Wars uploader" \
      org.opencontainers.image.source="https://github.com/n30nex/WDG-Aircraft-Sidecar" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libncurses6 librtlsdr0 libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin sidecar \
    && install -d -o sidecar -g sidecar /app /data

COPY --from=dump1090 /src/dump1090/dump1090 /usr/local/bin/dump1090
COPY --chown=sidecar:sidecar src/sidecar.py /app/sidecar.py

USER 10001:10001
VOLUME ["/data"]
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python3", "/app/sidecar.py", "--healthcheck"]
ENTRYPOINT ["python3", "/app/sidecar.py"]
