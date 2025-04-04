#!/bin/bash
set -e

# Runtime permission fix - run as root initially to fix permissions
if [ "$(id -u)" = "0" ]; then
    echo "Running as root, ensuring correct permissions..."
    
    # Create directories if they don't exist and set permissions
    mkdir -p /app/data /app/src
    
    # Fix ownership of ALL directories
    chown -R appuser:appgroup /app
    chmod -R 755 /app
    
    echo "Directory permissions for /app/data:"
    ls -la /app/data
    
    echo "Directory permissions for /app/src:"
    ls -la /app/src
    
    echo "Switching to appuser for security..."
    exec gosu appuser:appgroup "$0" "$@"
fi

# From this point on, we're running as appuser
echo "Running as $(id -un):$(id -gn) with UID=$(id -u), GID=$(id -g)"

# Verify write permissions before proceeding
if [ ! -w /app/src ] || [ ! -w /app/data ]; then
    echo "ERROR: Insufficient permissions for required directories!"
    echo "Permissions for /app/src:"
    ls -la /app/src
    echo "Permissions for /app/data:"
    ls -la /app/data
    exit 1
fi

# Create config.py from environment variables
echo "Generating configuration from environment variables..."
cat > /app/src/config.py <<EOF
NPM_API_URL = "${NPM_API_URL}"
NPM_API_USER = "${NPM_API_USER}" 
NPM_API_PASS = "${NPM_API_PASS}"

# Cloudflare configuration
CLOUDFLARE_CONFIG = {
EOF

# Parse the CLOUDFLARE_DOMAINS environment variable
# Format: domain1:token1:zoneid1,domain2:token2:zoneid2
if [ -n "$CLOUDFLARE_DOMAINS" ]; then
    IFS=',' read -ra DOMAINS <<< "$CLOUDFLARE_DOMAINS"
    for i in "${!DOMAINS[@]}"; do
        IFS=':' read -ra DOMAIN_INFO <<< "${DOMAINS[$i]}"
        if [ ${#DOMAIN_INFO[@]} -eq 3 ]; then
            if [ $i -gt 0 ]; then
                echo "," >> /app/src/config.py
            fi
            echo "    \"${DOMAIN_INFO[0]}\": {\"API_TOKEN\": \"${DOMAIN_INFO[1]}\", \"ZONE_ID\": \"${DOMAIN_INFO[2]}\"}" >> /app/src/config.py
        else
            echo "Warning: Invalid domain configuration format for ${DOMAINS[$i]}"
        fi
    done
fi

echo "}" >> /app/src/config.py

# Ensure data files exist
touch /app/data/domain_ips.json
touch /app/data/proxy_hosts.txt
touch /app/data/last_public_ip.txt

# Validate configuration
echo "Validating configuration..."
python3 -c "import sys; sys.path.append('/app'); from src import config; print('NPM URL:', config.NPM_API_URL); print('Domains configured:', len(config.CLOUDFLARE_CONFIG))"

# Set default update interval if not specified
UPDATE_INTERVAL=${UPDATE_INTERVAL:-300}
echo "Update interval set to ${UPDATE_INTERVAL} seconds"

# Add scalability for large deployments - adjust interval based on domain count
DOMAIN_COUNT=$(python3 -c "import sys; sys.path.append('/app'); from src import config; print(len(config.CLOUDFLARE_CONFIG))")
if [ "$DOMAIN_COUNT" -gt 20 ] && [ "$UPDATE_INTERVAL" -lt 300 ]; then
    echo "Large domain count detected ($DOMAIN_COUNT domains). Adjusting update interval to prevent rate limiting."
    UPDATE_INTERVAL=300
    echo "Adjusted update interval to ${UPDATE_INTERVAL} seconds"
fi

# Calculate when to do a forced refresh (every 24 hours)
COUNTER=0
FORCE_REFRESH_CYCLES=$((24*60*60 / ${UPDATE_INTERVAL}))

# First run should always check everything
export FORCE_UPDATE=true

# Start the service loop
echo "Starting DDNS update service..."
while true; do
    echo "$(date): Running DNS update"
    
    # Run the Python script with proper paths
    cd /app && python3 -c "
import sys
sys.path.append('/app')
from src import autodnsip
# Override the file paths to use the data directory
autodnsip.hosts_filename = '/app/data/proxy_hosts.txt'
autodnsip.domain_ips_file = '/app/data/domain_ips.json'
autodnsip.last_ip_file = '/app/data/last_public_ip.txt'
autodnsip.main()
"
    
    # Check exit status
    STATUS=$?
    if [ $STATUS -ne 0 ]; then
        echo "Error occurred during execution (exit code $STATUS). Will retry in ${UPDATE_INTERVAL} seconds."
        # Track error count for exponential backoff
        if [ -f "/app/data/.error_count" ]; then
            ERROR_COUNT=$(($(cat /app/data/.error_count) + 1))
            echo $ERROR_COUNT > /app/data/.error_count
        else
            echo "1" > /app/data/.error_count
        fi
    else
        # Reset error count on success
        echo "0" > /app/data/.error_count
    fi
    
    # After first run, disable forced updates unless periodic refresh
    if [ "${FORCE_UPDATE}" = "true" ]; then
        unset FORCE_UPDATE
    fi
    
    # Increment counter for periodic forced refresh
    COUNTER=$((COUNTER + 1))
    
    # Force update every FORCE_REFRESH_CYCLES
    if [ $COUNTER -ge $FORCE_REFRESH_CYCLES ]; then
        echo "Periodic forced refresh triggered"
        export FORCE_UPDATE=true
        COUNTER=0
    fi
    
    # Implement exponential backoff for error conditions
    if [ -f "/app/data/.error_count" ]; then
        ERROR_COUNT=$(cat /app/data/.error_count)
        if [ "$ERROR_COUNT" -gt 5 ]; then
            BACKOFF_INTERVAL=$((UPDATE_INTERVAL * 2))
            echo "Multiple errors detected, implementing backoff strategy"
            echo "Sleeping for extended period (${BACKOFF_INTERVAL} seconds) due to errors..."
            sleep ${BACKOFF_INTERVAL}
            echo "0" > /app/data/.error_count
            continue
        fi
    fi
    
    echo "Sleeping for ${UPDATE_INTERVAL} seconds..."
    sleep ${UPDATE_INTERVAL}
done