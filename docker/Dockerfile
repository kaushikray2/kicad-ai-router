FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive TZ=America/Toronto
RUN apt-get update \
 && apt-get install -y --no-install-recommends software-properties-common ca-certificates tzdata \
 && add-apt-repository -y ppa:kicad/kicad-10.0-releases \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      kicad=10.0.8~ubuntu24.04.1 kicad-footprints=10.0.8~ubuntu24.04.1 kicad-symbols=10.0.8~ubuntu24.04.1 \
      python3 python3-numpy python3-scipy \
 && rm -rf /var/lib/apt/lists/*
COPY ai_router /opt/ai-router/ai_router
COPY docker/server.py docker/run_job.py docker/config_page.html /opt/ai-router/
COPY docker/selftest /opt/ai-router/selftest
ENV AIR_DATA_DIR=/data AIR_PORT=8731
EXPOSE 8731
CMD ["python3", "/opt/ai-router/server.py"]
