FROM python:3.11-slim

# Install required packages and Chromium browser + driver
RUN apt-get update && apt-get install -y \
  chromium \
  chromium-driver \
  cron \
  unzip \
  wget \
  && rm -rf /var/lib/apt/lists/*

# Create yfinance cache directory with proper permissions
RUN mkdir -p /tmp/yfinance-cache && chmod 777 /tmp/yfinance-cache

# Set environment variables for the script
ENV CONFIG_PATH=/app/config.yaml \
  ACCOUNTS_PATH=/app/accounts.yaml \
  PYTHONUNBUFFERED=1 \
  CRON_SCHEDULE="*/5 * * * *" \
  YFINANCE_CACHE_DIR="/tmp/yfinance-cache"

# Copy files and install dependencies
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create an entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Use the entrypoint script
ENTRYPOINT ["/entrypoint.sh"]
