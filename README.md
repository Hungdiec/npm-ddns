# NPM-Cloudflare DDNS Automation Project Documentation

## Project Overview

This project implements an automated system that synchronizes Nginx Proxy Manager (NPM) hosts with Cloudflare DNS records using Docker for easy deployment. The system monitors NPM for host changes and automatically updates Cloudflare DNS records, while also tracking public IP changes.

## Project Structure

```javascript
npm-cloudflare-ddns/
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── requirements.txt
├── README.md
└── src/
    └── autodnsip.py
```

## Implementation Files

### Dockerfile

```dockerfile
FROM python:3.9-slim

WORKDIR /app

# Install required packages
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY src/ /app/src/
COPY entrypoint.sh .

# Make the entrypoint script executable
RUN chmod +x entrypoint.sh

# Set environment variables (can be overridden)
ENV NPM_API_URL="http://nginx-proxy-manager:81"
ENV NPM_API_USER="admin@example.com"
ENV NPM_API_PASS="changeme"
ENV UPDATE_INTERVAL=300
ENV PYTHONPATH=/app

# Create data directory and set proper permissions
RUN mkdir -p /app/data
RUN touch /app/data/domain_ips.json /app/data/proxy_hosts.txt /app/data/last_public_ip.txt

# Create volume for persistent data
VOLUME ["/app/data"]

# Run as non-root user for better security
RUN useradd -m appuser
RUN chown -R appuser:appuser /app
USER appuser

# Run the entrypoint script
ENTRYPOINT ["/app/entrypoint.sh"]
```

### requirements.txt

```javascript
requests==2.28.1
```

### entrypoint.sh

```bash
#!/bin/bash
set -e

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

# Ensure data directory exists with proper permissions
mkdir -p /app/data
touch /app/data/domain_ips.json /app/data/proxy_hosts.txt /app/data/last_public_ip.txt

# Validate configuration
echo "Validating configuration..."
python3 -c "import sys; sys.path.append('/app'); from src import config; print('NPM URL:', config.NPM_API_URL); print('Domains configured:', len(config.CLOUDFLARE_CONFIG))"

# Set default update interval if not specified
UPDATE_INTERVAL=${UPDATE_INTERVAL:-300}
echo "Update interval set to ${UPDATE_INTERVAL} seconds"

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
    if [ $? -ne 0 ]; then
        echo "Error occurred during execution. Will retry in ${UPDATE_INTERVAL} seconds."
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
    
    echo "Sleeping for ${UPDATE_INTERVAL} seconds..."
    sleep ${UPDATE_INTERVAL}
done
```

### src/autodnsip.py

```python
#!/usr/bin/env python3
import requests
import json
import os
import sys

# Import configuration values from config.py
try:
    # Try direct import first (for development)
    import config
except ModuleNotFoundError:
    # Fall back to package import (for containerized environment)
    from src import config

NPM_API_URL = config.NPM_API_URL
NPM_API_USER = config.NPM_API_USER
NPM_API_PASS = config.NPM_API_PASS
# CLOUDFLARE_CONFIG is a dict mapping a root domain (e.g. "hung99.com") to its Cloudflare credentials.
CLOUDFLARE_CONFIG = config.CLOUDFLARE_CONFIG

# Default file paths (can be overridden)
hosts_filename = "proxy_hosts.txt"
domain_ips_file = "domain_ips.json"
last_ip_file = "last_public_ip.txt"

def get_npm_token():
    """Gets an API token from NPM."""
    url = f"{NPM_API_URL}/api/tokens"
    data = {"identity": NPM_API_USER, "secret": NPM_API_PASS}
    response = requests.post(url, json=data)
    response.raise_for_status()
    return response.json()['token']

def get_proxy_hosts(token):
    """Gets the list of proxy hosts from NPM."""
    url = f"{NPM_API_URL}/api/nginx/proxy-hosts"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

def update_host_file(current_hosts, filename):
    """Writes the current hosts into the file."""
    with open(filename, "w") as f:
        for host in current_hosts:
            f.write(host + "\n")

def get_public_ip():
    """Fetches the public IP of this server."""
    try:
        response = requests.get("https://api.ipify.org?format=json")
        response.raise_for_status()
        return response.json()["ip"]
    except Exception as e:
        print("Error retrieving public IP:", e)
        return None

def get_cf_config_for_domain(domain):
    """
    Determines the root domain for the given proxy host and returns the corresponding
    Cloudflare configuration. If the domain isn't configured, it returns None.
    """
    for root in CLOUDFLARE_CONFIG:
        if domain.endswith(root):
            return CLOUDFLARE_CONFIG[root]
    print(f"Skipping domain {domain}: No Cloudflare configuration found.")
    return None

def check_cloudflare_record_exists(domain, cf_config):
    """Checks if an A record already exists for the given domain in Cloudflare."""
    url = f"https://api.cloudflare.com/client/v4/zones/{cf_config['ZONE_ID']}/dns_records"
    params = {"type": "A", "name": domain}
    headers = {"Authorization": f"Bearer {cf_config['API_TOKEN']}"}
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    results = response.json().get('result', [])
    return len(results) > 0

def create_cloudflare_a_record(domain, ip, cf_config):
    """Creates an A record in Cloudflare for the given domain with the provided IP."""
    if check_cloudflare_record_exists(domain, cf_config):
        print(f"A record already exists for {domain}, checking for update.")
        update_cloudflare_a_record(domain, ip, cf_config)
        return

    url = f"https://api.cloudflare.com/client/v4/zones/{cf_config['ZONE_ID']}/dns_records"
    headers = {
        "Authorization": f"Bearer {cf_config['API_TOKEN']}",
        "Content-Type": "application/json"
    }
    data = {
        "type": "A",
        "name": domain,
        "content": ip,
        "ttl": 3600,
        "proxied": True
    }
    response = requests.post(url, headers=headers, json=data)
    try:
        response.raise_for_status()
        print(f"A record created for {domain} with IP {ip}")
    except requests.exceptions.HTTPError:
        error_detail = response.json()
        print(f"Error creating Cloudflare A record for {domain}: {response.status_code} {response.reason}")
        print("Details:", error_detail)

def update_cloudflare_a_record(domain, new_ip, cf_config):
    """Updates the A record in Cloudflare for the given domain with the new IP if it differs."""
    url = f"https://api.cloudflare.com/client/v4/zones/{cf_config['ZONE_ID']}/dns_records"
    params = {"type": "A", "name": domain}
    headers = {"Authorization": f"Bearer {cf_config['API_TOKEN']}", "Content-Type": "application/json"}
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    results = response.json().get('result', [])
    if not results:
        print(f"No A record found for {domain} to update, creating one.")
        create_cloudflare_a_record(domain, new_ip, cf_config)
        return
    for record in results:
        if record.get('content') != new_ip:
            record_id = record.get('id')
            update_url = f"https://api.cloudflare.com/client/v4/zones/{cf_config['ZONE_ID']}/dns_records/{record_id}"
            data = {
                "type": "A",
                "name": domain,
                "content": new_ip,
                "ttl": 3600,
                "proxied": True
            }
            update_response = requests.put(update_url, headers=headers, json=data)
            try:
                update_response.raise_for_status()
                print(f"A record updated for {domain} to IP {new_ip}")
            except requests.exceptions.HTTPError:
                error_detail = update_response.json()
                print(f"Error updating Cloudflare A record for {domain}: {update_response.status_code} {update_response.reason}")
                print("Details:", error_detail)
        else:
            print(f"A record for {domain} already has the correct IP {new_ip}")

def delete_cloudflare_a_record(domain, cf_config):
    """Deletes an A record in Cloudflare for the given domain."""
    url = f"https://api.cloudflare.com/client/v4/zones/{cf_config['ZONE_ID']}/dns_records"
    params = {"type": "A", "name": domain}
    headers = {"Authorization": f"Bearer {cf_config['API_TOKEN']}"}
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    results = response.json().get('result', [])
    if not results:
        print(f"No A record found for {domain}")
        return
    for record in results:
        record_id = record.get('id')
        del_url = f"https://api.cloudflare.com/client/v4/zones/{cf_config['ZONE_ID']}/dns_records/{record_id}"
        del_response = requests.delete(del_url, headers=headers)
        try:
            del_response.raise_for_status()
            print(f"A record deleted for {domain}")
        except requests.exceptions.HTTPError:
            error_detail = del_response.json()
            print(f"Error deleting Cloudflare A record for {domain}: {del_response.status_code} {del_response.reason}")
            print("Details:", error_detail)

def main():
    """Main function that handles both IP updates and NPM host synchronization."""
    # Check if we have a stored last IP
    last_known_ip = None
    if os.path.exists(last_ip_file):
        with open(last_ip_file, "r") as f:
            last_known_ip = f.read().strip()
    
    # Always get NPM proxy hosts to check for additions/deletions
    token = get_npm_token()
    proxy_hosts = get_proxy_hosts(token)
    current_domains = {domain for host in proxy_hosts for domain in host['domain_names']}
    
    # File to track previous domains (for detecting created/deleted hosts)
    if os.path.exists(hosts_filename):
        with open(hosts_filename, "r") as f:
            previous_domains = {line.strip() for line in f if line.strip()}
    else:
        previous_domains = set()
    
    # Detect domain changes
    created_domains = current_domains - previous_domains
    deleted_domains = previous_domains - current_domains
    
    # If domains have changed or we're in forced mode, we need to process
    domains_changed = len(created_domains) > 0 or len(deleted_domains) > 0
    force_update = os.environ.get('FORCE_UPDATE', '').lower() in ('true', '1', 'yes')
    
    # Get current public IP
    current_public_ip = get_public_ip()
    if not current_public_ip:
        print("Could not retrieve public IP, aborting update.")
        return 1
    
    # Check if IP has changed
    ip_changed = last_known_ip != current_public_ip
    
    # Log status
    if domains_changed:
        print(f"Domain changes detected: {len(created_domains)} new, {len(deleted_domains)} removed")
        if created_domains:
            print(f"New domains: {', '.join(created_domains)}")
        if deleted_domains:
            print(f"Removed domains: {', '.join(deleted_domains)}")
    
    if ip_changed:
        if last_known_ip:
            print(f"IP change detected: {last_known_ip} -> {current_public_ip}")
        else:
            print(f"Initial IP detection: {current_public_ip}")
    
    # If nothing changed and we're not forcing, exit early
    if not domains_changed and not ip_changed and not force_update:
        print("No changes detected. Skipping update cycle.")
        return 0
    
    # Load stored domain IP mappings from file
    try:
        with open(domain_ips_file, "r") as f:
            stored_ips = json.load(f)
    except Exception:
        stored_ips = {}
    
    # Process each current domain individually
    for domain in current_domains:
        cf_config = get_cf_config_for_domain(domain)
        if cf_config is None:
            continue
            
        # For new domains or IP changes
        if domain in created_domains or domain not in stored_ips:
            print(f"New domain detected: {domain}. Creating A record with IP {current_public_ip}")
            try:
                create_cloudflare_a_record(domain, current_public_ip, cf_config)
                stored_ips[domain] = current_public_ip
            except Exception as e:
                print(f"Error processing Cloudflare creation for {domain}: {e}")
        elif ip_changed or force_update:
            # Update existing domains if IP changed or forced
            if ip_changed:
                print(f"Updating {domain} with new IP {current_public_ip}")
            else:
                print(f"Forced update for {domain} with IP {current_public_ip}")
            try:
                update_cloudflare_a_record(domain, current_public_ip, cf_config)
                stored_ips[domain] = current_public_ip
            except Exception as e:
                print(f"Error updating Cloudflare A record for {domain}: {e}")
        else:
            print(f"No changes needed for {domain}")
    
    # Process deleted domains
    for domain in deleted_domains:
        cf_config = get_cf_config_for_domain(domain)
        if cf_config is None:
            continue
        try:
            print(f"Deleting DNS record for removed domain: {domain}")
            delete_cloudflare_a_record(domain, cf_config)
        except Exception as e:
            print(f"Error processing Cloudflare deletion for {domain}: {e}")
        if domain in stored_ips:
            del stored_ips[domain]
    
    # Save updated domain IPs
    with open(domain_ips_file, "w") as f:
        json.dump(stored_ips, f)
    
    # Save current hosts list
    update_host_file(current_domains, hosts_filename)
    
    # After successful update, store the current IP
    with open(last_ip_file, "w") as f:
        f.write(current_public_ip)
    
    return 0

if __name__ == "__main__":
    # Run the main function
    exit(main())
```

### docker-compose.yml

```yaml
version: '3.8'

services:
  npm-cloudflare-ddns:
    image: hungdiec/npm-cloudflare-ddns:latest
    container_name: npm-cloudflare-ddns
    restart: unless-stopped
    environment:
      - NPM_API_URL=http://nginx-proxy-manager:81
      - NPM_API_USER=admin@example.com
      - NPM_API_PASS=changeme
      - CLOUDFLARE_DOMAINS=example.com:your_cloudflare_token:your_zone_id,example2.com:token2:zone2
      - UPDATE_INTERVAL=300
    volumes:
      - npm-cloudflare-data:/app/data
    healthcheck:
      test: ["CMD", "test", "-e", "/app/data/domain_ips.json"]
      interval: 1m
      timeout: 10s
      retries: 3
      start_period: 30s
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    networks:
      - npm-network

networks:
  npm-network:
    external: true

volumes:
  npm-cloudflare-data:
```

### README.md

```markdown
# NPM-Cloudflare DDNS Automation

This Docker container automatically synchronizes Nginx Proxy Manager hosts with Cloudflare DNS records, handling both IP updates and host synchronization.

## Features

- **Automatic IP Updates**: Updates DNS records when your public IP changes
- **Host Synchronization**: Creates, updates, or deletes Cloudflare DNS records when proxy hosts are added/removed in NPM
- **Efficient API Usage**: Only makes API calls when necessary to avoid rate limiting
- **Persistent Storage**: Maintains state between container restarts
- **Security**: Runs as non-root user with minimal permissions

## Quick Deployment

### Using Docker Compose

1. Create a `docker-compose.yml` file:
   ```yaml
   version: '3.8'
   
   services:
     npm-cloudflare-ddns:
       image: hungdiec/npm-cloudflare-ddns:latest
       container_name: npm-cloudflare-ddns
       restart: unless-stopped
       environment:
         - NPM_API_URL=http://nginx-proxy-manager:81
         - NPM_API_USER=your_npm_username
         - NPM_API_PASS=your_npm_password
         - CLOUDFLARE_DOMAINS=example.com:your_cf_token:your_zone_id,example2.com:token2:zone2
         - UPDATE_INTERVAL=300
       volumes:
         - npm-cloudflare-data:/app/data
       networks:
         - npm-network
   
   networks:
     npm-network:
       external: true
   
   volumes:
     npm-cloudflare-data:
```

2. Deploy the container:

```bash
   docker-compose up -d
```

3. Check logs:

```bash
   docker-compose logs -f
```

### Using Docker Run

```bash
docker run -d --name npm-cloudflare-ddns \
  --restart unless-stopped \
  -e NPM_API_URL=http://nginx-proxy-manager:81 \
  -e NPM_API_USER=your_npm_username \
  -e NPM_API_PASS=your_npm_password \
  -e CLOUDFLARE_DOMAINS=example.com:your_cf_token:your_zone_id \
  -e UPDATE_INTERVAL=300 \
  -v npm-cloudflare-data:/app/data \
  --network npm-network \
  hungdiec/npm-cloudflare-ddns:latest
```

## Environment Variables

 Variable  Description  Default  NPM_API_URL  URL to your NPM instance  http://nginx-proxy-manager:81  NPM_API_USER  NPM admin username  admin@example.com  NPM_API_PASS  NPM admin password  changeme  CLOUDFLARE_DOMAINS  Domain configurations in format: domain:token:zoneid  None  UPDATE_INTERVAL  Seconds between DNS updates  300  FORCE_UPDATE  Force update regardless of changes  false 

## Cloudflare API Token Requirements

Your Cloudflare API token needs the following permissions:

- Zone:DNS:Edit
- Zone:Zone:Read

## Troubleshooting

### Common Issues

1. **Connection to NPM fails**:

- Verify NPM URL is correct
- Check NPM credentials
- Ensure container can reach NPM (network configuration)

2. **Cloudflare API errors**:

- Verify API token has correct permissions
- Check Zone ID is correct
- Ensure domain format is correct in CLOUDFLARE_DOMAINS

3. **No DNS updates happening**:

- Check logs for errors
- Verify domains are configured in NPM
- Force an update with FORCE_UPDATE=true

### Viewing Logs

```bash
docker logs -f npm-cloudflare-ddns
```

## Security Considerations

- Store sensitive information using Docker secrets in production
- Use a dedicated Cloudflare API token with minimal permissions
- Consider network isolation for the container

## License

MIT

```javascript
## Project Development Phases

### Phase 1: Environment Preparation

1. **Create project directory structure**:
   ```bash
   mkdir -p npm-cloudflare-ddns/src
   cd npm-cloudflare-ddns
```

2. **Set up Python virtual environment** (for development):

```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install requests
   pip freeze > requirements.txt
```

3. **Initialize version control**:

```bash
   git init
   echo "*.pyc\n__pycache__/\n.env\nvenv/\n*.log" > .gitignore
```

### Phase 2: Core Application Development

1. **Implement the main Python script** (`src/autodnsip.py`):

- Develop NPM API integration
- Implement Cloudflare DNS record management
- Create state tracking for domains and IP addresses
- Add conditional update logic to optimize API calls

2. **Create configuration handling**:

- Design flexible configuration system
- Implement environment variable parsing
- Add support for multiple domains

3. **Implement error handling and logging**:

- Add comprehensive error handling
- Implement informative logging
- Create recovery mechanisms

### Phase 3: Containerization

1. **Create Docker infrastructure**:

- Write Dockerfile
- Create entrypoint script
- Configure volume for persistent data
- Implement security best practices (non-root user)

2. **Develop Docker Compose configuration**:

- Define service configuration
- Set up networking
- Configure persistent volumes
- Add health checks and logging

3. **Test container locally**:

```bash
   docker build -t npm-cloudflare-ddns:test .
   docker run -d --name ddns-test \
     -e NPM_API_URL=http://your-npm-server:81 \
     -e NPM_API_USER=test_user \
     -e NPM_API_PASS=test_pass \
     -e CLOUDFLARE_DOMAINS=example.com:CF_TOKEN:ZONE_ID \
     -e UPDATE_INTERVAL=60 \
     npm-cloudflare-ddns:test
   docker logs -f ddns-test
```

### Phase 4: Testing and Refinement

1. **Perform integration testing**:

- Test with actual NPM instance
- Verify Cloudflare API integration
- Validate domain addition/removal detection
- Test IP change detection and updates

2. **Optimize performance**:

- Implement conditional updates
- Add periodic forced refresh
- Optimize API call frequency
- Improve error handling and recovery

3. **Address issues**:

- Fix permission issues with data files
- Resolve Python module import problems
- Enhance error reporting
- Improve logging clarity

### Phase 5: Production Deployment

1. **Publish Docker image**:

```bash
   docker login
   docker tag npm-cloudflare-ddns:latest hungdiec/npm-cloudflare-ddns:latest
   docker push hungdiec/npm-cloudflare-ddns:latest
```

2. **Create documentation**:

- Write comprehensive README
- Document environment variables
- Create troubleshooting guide
- Add deployment examples

3. **Implement monitoring and maintenance**:

- Add container health checks
- Configure log rotation
- Create update procedures
- Document backup and recovery

## Deployment Instructions

### Quick Start

1. **Pull and run the container**:

```bash
   docker run -d --name npm-cloudflare-ddns \
     --restart unless-stopped \
     -e NPM_API_URL=http://nginx-proxy-manager:81 \
     -e NPM_API_USER=your_npm_username \
     -e NPM_API_PASS=your_npm_password \
     -e CLOUDFLARE_DOMAINS=example.com:your_cf_token:your_zone_id \
     -e UPDATE_INTERVAL=300 \
     -v npm-cloudflare-data:/app/data \
     --network npm-network \
     hungdiec/npm-cloudflare-ddns:latest
```

2. **Using Docker Compose**:

```bash
   # Create docker-compose.yml with the configuration
   docker-compose up -d
```

3. **Verify operation**:

```bash
   docker logs -f npm-cloudflare-ddns
```

### Cloudflare Setup

1. **Create API Token**:

- Go to Cloudflare Dashboard → Profile → API Tokens
- Create Custom Token with:
    - Zone:DNS:Edit
    - Zone:Zone:Read permissions
- Note the token for configuration

2. **Get Zone ID**:

- Go to Cloudflare Dashboard → Select your domain
- Find Zone ID on the right sidebar
- Note the Zone ID for configuration

### NPM Integration

1. **Get NPM API credentials**:

- Use your NPM admin username and password
- Ensure the container can reach your NPM instance

2. **Configure network access**:

- Ensure the container is on the same network as NPM
- Or configure proper network routing

## Maintenance and Updates

1. **Update the container**:

```bash
   docker-compose pull
   docker-compose up -d
```

2. **View logs**:

```bash
   docker logs -f npm-cloudflare-ddns
```

3. **Backup data**:

```bash
   docker cp npm-cloudflare-ddns:/app/data /backup/npm-cloudflare-ddns-data
```

## Security Considerations

1. **API Token Security**:

- Use tokens with minimal required permissions
- Rotate tokens periodically
- Use separate tokens for different environments

2. **Credential Management**:

- Consider using Docker secrets for sensitive data
- Avoid hardcoding credentials in compose files
- Use .env files for local development

3. **Network Security**:

- Isolate the container on appropriate networks
- Implement proper firewall rules
- Use HTTPS for all API communications

## Scalability Considerations

1. **High Availability**:

- Deploy multiple instances for critical environments
- Implement proper monitoring and alerting
- Use container orchestration for automatic recovery

2. **Performance Optimization**:

- Adjust update intervals based on operational needs
- Implement conditional updates to reduce API calls
- Consider caching mechanisms for frequent operations

3. **Multi-Environment Deployment**:

- Use different configurations for dev/test/prod
- Implement CI/CD pipelines for automated deployment
- Consider multi-registry strategy for geographic distribution
