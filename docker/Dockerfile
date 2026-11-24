FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive TZ=America/Toronto
RUN apt-get update \
 && apt-get install -y --no-install-recommends software-properties-common ca-certificates tzdata \
 && add-apt-repository -y ppa:kicad/kicad-10.0-releases \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      kicad=10.0.8~ubuntu24.04.1 kicad-footprints=10.0.8~ubuntu24.04.1 kicad-symbols=10.0.8~ubuntu24.04.1 \
      python3 python3-numpy python3-scipy python3-pip curl unzip git fontconfig \
 && rm -rf /var/lib/apt/lists/*
# v1.9.5 (Phase 1.2): portfolio engines. Freerouting 2.5.0 JVM
# distribution (the native CLI build crashes on KiCad DSNs) and
# KiCadRoutingTools at a pinned commit + its grid_router binary.
# Both are optional at runtime: the worker reports unavailable
# and the portfolio stage skips them when absent.
RUN curl -sL -o /tmp/fr.zip https://github.com/freerouting/freerouting/releases/download/v2.5.0/freerouting-2.5.0-linux-x64.zip \
 && echo "e8ffca14914e5307a2fa9aea723fb93d11b99bebda20d126472da5460c03196e  /tmp/fr.zip" | sha256sum -c - \
 && unzip -q /tmp/fr.zip -d /opt && mv /opt/freerouting-2.5.0-linux-x64 /opt/freerouting && rm /tmp/fr.zip
RUN git clone -q https://github.com/drandyhaas/KiCadRoutingTools.git /opt/krt \
 && cd /opt/krt && git checkout -q 61565f35053aeeb3a005fafc9f137f9c026e04df \
 && python3 build_router.py --tag v0.24.2 \
 && python3 -m pip install --break-system-packages -q "shapely==2.2.0"
COPY ai_router /opt/ai-router/ai_router
COPY docker/server.py docker/run_job.py docker/config_page.html /opt/ai-router/
COPY docker/selftest /opt/ai-router/selftest
ENV AIR_DATA_DIR=/data AIR_PORT=8731
EXPOSE 8731
CMD ["python3", "/opt/ai-router/server.py"]
