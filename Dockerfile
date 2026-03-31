FROM python:3.12-slim

LABEL org.opencontainers.image.title="Web Scraping Gateway"
LABEL org.opencontainers.image.description="Pay-per-page web scraping for AI agents, powered by Mainlayer"
LABEL org.opencontainers.image.version="1.0.0"

# System dependencies for lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    libxslt1.1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY src/ ./src/

# Non-root user for security
RUN addgroup --system gateway && adduser --system --ingroup gateway gateway
USER gateway

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
