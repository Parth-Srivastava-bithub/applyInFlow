FROM python:3.11-slim-bookworm

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system utilities, Xvfb, fluxbox window manager, VNC, noVNC, and fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    gnupg \
    ca-certificates \
    xvfb \
    fluxbox \
    x11vnc \
    novnc \
    websockify \
    fonts-liberation \
    fonts-noto-color-emoji \
    libnss3 \
    libxss1 \
    libasound2 \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Install official Google Chrome Stable
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

# Configure noVNC index redirect with autoconnect & auto-scale enabled by default
RUN echo '<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0; url=vnc.html?autoconnect=true&resize=scale" /></head><body></body></html>' > /usr/share/novnc/index.html

# Set working directory
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 5 -r requirements.txt

# Copy application source code
COPY . .

# Ensure entrypoint script is executable
RUN chmod +x /app/docker-entrypoint.sh

# Volumes for persistent cookies/session and application data
VOLUME ["/data/chrome_profile", "/app/output"]

# Expose ports:
# 5000: AutoApply Dashboard Web UI
# 6080: noVNC Web Viewer for 1-time LinkedIn visual login
# 9222: Chrome DevTools Protocol (CDP)
EXPOSE 5000 6080 9222

ENTRYPOINT ["/app/docker-entrypoint.sh"]
