#!/bin/bash
set -e

# Get the run interval in minutes from environment variable, default to 60 minutes if not set
RUN_INTERVAL_MINUTES=${RUN_INTERVAL_MINUTES:-60}
RUN_INTERVAL_SECONDS=$((RUN_INTERVAL_MINUTES * 60))

# Check if this should be a one-time run
RUN_ONCE=${RUN_ONCE:-false}

if [ "$RUN_ONCE" = "true" ]; then
  echo "Starting container in ONE-TIME RUN mode"
else
  echo "Starting container with RUN_INTERVAL_MINUTES: ${RUN_INTERVAL_MINUTES} (${RUN_INTERVAL_SECONDS} seconds)"
fi

# Validate required environment variables
required_vars=("PL_USERNAME" "PL_PASSWORD" "PL_API_KEY")
missing_vars=()

for var in "${required_vars[@]}"; do
  if [ -z "${!var}" ]; then
    missing_vars+=("$var")
  fi
done

if [ ${#missing_vars[@]} -ne 0 ]; then
  echo "ERROR: The following required environment variables are missing:"
  for var in "${missing_vars[@]}"; do
    echo "  - $var"
  done
  echo "Please set these variables and restart the container."
  exit 1
fi

# Validate accounts.yaml exists
if [ ! -f "/app/accounts.yaml" ]; then
  echo "ERROR: accounts.yaml not found at /app/accounts.yaml"
  echo "Please mount your accounts.yaml file to this location."
  exit 1
fi

# Validate accounts.yaml format
echo "Validating accounts.yaml format..."
python3 -c "
import yaml
try:
    with open('/app/accounts.yaml', 'r') as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or 'accounts' not in data or not isinstance(data['accounts'], list):
        print('ERROR: Invalid accounts.yaml format. File should contain an \"accounts\" list.')
        exit(1)
    if len(data['accounts']) == 0:
        print('WARNING: No accounts found in accounts.yaml')
    else:
        print(f'Found {len(data[\"accounts\"])} accounts in configuration file')
except Exception as e:
    print(f'ERROR: Failed to parse accounts.yaml: {e}')
    exit(1)
"

# Test run the script with validation only to catch any issues
echo "Performing validation run of the script..."
python3 -c "
import os
os.environ['VALIDATE_ONLY'] = 'true'
try:
    exec(open('/app/projectionlab.py').read())
    print('Validation successful!')
except Exception as e:
    print(f'ERROR: Script validation failed: {e}')
    exit(1)
"

if [ $? -ne 0 ]; then
  echo "Validation failed. Please check the error messages above."
  exit 1
fi

# Create the log file with proper permissions
touch /app/script.log
chmod 666 /app/script.log

echo "All validation checks passed!"

# Run the script once immediately
echo "Running script..."
cd /app
python3 /app/projectionlab.py

# If RUN_ONCE is true, exit after the first run
if [ "$RUN_ONCE" = "true" ]; then
  echo "One-time run completed. Exiting container."
  exit 0
fi

# Enter the main loop
echo "Entering main loop, will run every ${RUN_INTERVAL_MINUTES} minutes (${RUN_INTERVAL_SECONDS} seconds)"
while true; do
  echo "Sleeping for ${RUN_INTERVAL_MINUTES} minutes until next run..."
  sleep $RUN_INTERVAL_SECONDS
  echo "Running script at $(date)..."
  cd /app
  python3 /app/projectionlab.py
done
