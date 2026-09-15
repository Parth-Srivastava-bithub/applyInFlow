# Base image: Python 3.11 slim on Debian Bookworm
FROM python:3.11-slim-bookworm

# Prevent interactive dialogs during apt installs
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install XeLaTeX, TeX Live packages, fonts, and utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    texlive-xetex \
    texlive-latex-recommended \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    fonts-font-awesome \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root system user for secure sandboxed execution
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY main.py .

# Switch to non-root user
USER appuser

EXPOSE 8000

# Start FastAPI server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
