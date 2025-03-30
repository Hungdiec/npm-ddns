# NPM-Cloudflare DDNS Docker Deployment Documentation

## Overview

This document provides comprehensive information about deploying the NPM-Cloudflare DDNS synchronization service using Docker Compose. The service automatically maintains DNS records in Cloudflare based on hosts configured in Nginx Proxy Manager (NPM).

## Docker Compose Configuration

```yaml
version: '3'

services:
  npm-cloudflare-ddns:
    image: hungdiec/npm-cloudflare-ddns:latest
    container_name: npm-cloudflare-ddns
    restart: unless-stopped
    environment:
      - NPM_API_URL=http://nginx-proxy-manager:81
      - NPM_API_USER=admin@example.com
      - NPM_API_PASS=changeme
      - CLOUDFLARE_DOMAINS=example.com:your_cloudflare_token:your_zone_id
      - UPDATE_INTERVAL=300
    volumes:
      - npm-cloudflare-data:/app/data
    networks:
      - npm-network

networks:
  npm-network:
    # Choose one of these options based on your environment:
    # Option 1: Create a new network
    driver: bridge
    
    # Option 2: Use an existing network (uncomment and specify the name)
    # external: true
    # name: your_existing_network_name

volumes:
  npm-cloudflare-data:
```

## Configuration Variables

### Environment Variables

 Variable  Description  Default  Required  Example  `NPM_API_URL`  URL to your Nginx Proxy Manager API  `http://nginx-proxy-manager:81`  Yes  `http://192.168.1.10:81`  `NPM_API_USER`  NPM admin username/email  `admin@example.com`  Yes  `admin@yourdomain.com`  `NPM_API_PASS`  NPM admin password  `changeme`  Yes  `your-secure-password`  `CLOUDFLARE_DOMAINS`  Domain configurations in format: domain:token:zoneid  None  Yes  `example.com:CF_TOKEN_123:ZONE_ID_456`  `UPDATE_INTERVAL`  Seconds between DNS update checks  `300`  No  `600`  `FORCE_UPDATE`  Force update regardless of changes  `false`  No  `true`  `TZ`  Container timezone  `UTC`  No  `America/New_York` 

### CLOUDFLARE_DOMAINS Format

The `CLOUDFLARE_DOMAINS` variable accepts multiple domain configurations separated by commas:

```javascript
domain1:token1:zoneid1,domain2:token2:zoneid2
```

Each domain configuration consists of three parts separated by colons:

1. **Root domain**: The domain managed in Cloudflare (e.g., `example.com`)
2. **API token**: A Cloudflare API token with DNS edit permissions
3. **Zone ID**: The Cloudflare Zone ID for the domain

### Network Configuration

The service needs network connectivity to both:

1. Your Nginx Proxy Manager instance
2. The internet (to access Cloudflare API and check public IP)

Choose the appropriate network configuration:

#### Option 1: Create a new bridge network

```yaml
networks:
  npm-network:
    driver: bridge
```

#### Option 2: Use an existing network

```yaml
networks:
  npm-network:
    external: true
    name: your_existing_network_name  # Replace with your actual network name
```

To find existing networks:

```bash
docker network ls
```

### Volume Configuration

The service uses a named volume to persist data between container restarts:

```yaml
volumes:
  npm-cloudflare-data:
```

This stores:

- Current domain to IP mappings
- List of previously detected domains
- Last known public IP address

## Deployment Instructions

1. **Create the docker-compose.yml file**:

```bash
   mkdir -p ~/npm-cloudflare-ddns
   cd ~/npm-cloudflare-ddns
   nano docker-compose.yml  # Paste the configuration
```

2. **Configure environment variables**:
   Edit the docker-compose.yml file to include your specific configuration values.

3. **Deploy the container**:

```bash
   docker-compose up -d
```

4. **Verify deployment**:

```bash
   docker-compose logs -f
```

## Network Connectivity Requirements

 Source  Destination  Port  Protocol  Purpose  npm-cloudflare-ddns  Nginx Proxy Manager  81  HTTP  API access  npm-cloudflare-ddns  api.ipify.org  443  HTTPS  Public IP detection  npm-cloudflare-ddns  api.cloudflare.com  443  HTTPS  DNS management 

## Performance Considerations

### Resource Usage

The container has minimal resource requirements:

- **CPU**: Negligible except during update operations
- **Memory**: ~50-100MB during normal operation
- **Network**: Small API calls, typically less than 1MB per hour
- **Disk**: Less than 1MB for persistent data

### Optimization Recommendations

1. **Update Interval**: 

- Default: 300 seconds (5 minutes)
- For stable IPs: Consider increasing to 900-1800 seconds (15-30 minutes)
- For frequently changing IPs: Keep at 300 seconds

2. **Network Placement**:

- Place the container on the same network as NPM for optimal performance
- Ensure reliable internet connectivity for Cloudflare API access

## Scaling Considerations

While this service typically runs as a singleton container, for high-availability environments:

1. **Multiple Domains**: The service efficiently handles multiple domains in a single instance
2. **Multiple Environments**: For separate dev/test/prod environments, deploy separate instances
3. **Geographic Distribution**: For global deployments, consider regional instances with different update intervals

## Security Recommendations

1. **API Token Scope**:

- Create dedicated Cloudflare API tokens with minimal permissions:
    - Zone:DNS:Edit
    - Zone:Zone:Read
- Restrict tokens to specific zones when possible

2. **Credential Management**:

- Store sensitive environment variables in a .env file:

```bash
     echo "NPM_API_PASS=your-secure-password" >> .env
     echo "CLOUDFLARE_DOMAINS=example.com:your-token:your-zone-id" >> .env
```

- Reference in docker-compose:

```yaml
     services:
       npm-cloudflare-ddns:
         env_file: .env
```

3. **Network Isolation**:

- Consider placing the container on a management network with restricted access
- Implement proper firewall rules to limit connectivity to required services

## Monitoring and Maintenance

### Log Monitoring

```bash
# View real-time logs
docker-compose logs -f

# View recent logs
docker-compose logs --tail=100
```

### Health Checking

```bash
# Check container status
docker ps -f name=npm-cloudflare-ddns

# Check data persistence
docker exec npm-cloudflare-ddns ls -la /app/data
```

### Update Procedure

```bash
# Pull latest image
docker-compose pull

# Restart service with new image
docker-compose up -d
```

## Troubleshooting

### Common Issues and Solutions

1. **Connection to NPM fails**:

- Verify NPM URL is correct and accessible
- Check NPM credentials
- Ensure proper network connectivity

2. **Cloudflare API errors**:

- Verify API token has correct permissions
- Check Zone ID is correct
- Ensure domain format is correct in CLOUDFLARE_DOMAINS

3. **No DNS updates happening**:

- Force an update: `docker-compose exec npm-cloudflare-ddns sh -c "export FORCE_UPDATE=true && cd /app && python3 -m src.autodnsip"`
- Check logs for errors
- Verify domains are configured in NPM

4. **Container restarts frequently**:

- Check logs for error messages
- Verify network connectivity to both NPM and Cloudflare
- Ensure volume permissions are correct

## Backup and Recovery

### Backup Procedure

```bash
# Create backup directory
mkdir -p ~/backups/npm-cloudflare-ddns

# Backup data volume
docker run --rm -v npm-cloudflare-data:/data -v ~/backups/npm-cloudflare-ddns:/backup alpine tar -czf /backup/data-$(date +%Y%m%d).tar.gz -C /data .
```

### Restore Procedure

```bash
# Restore from backup
docker run --rm -v npm-cloudflare-data:/data -v ~/backups/npm-cloudflare-ddns:/backup alpine sh -c "rm -rf /data/* && tar -xzf /backup/data-YYYYMMDD.tar.gz -C /data"
```

## Conclusion

This Docker Compose configuration provides a reliable, efficient way to synchronize Nginx Proxy Manager hosts with Cloudflare DNS records. By following the configuration guidelines and security recommendations, you can ensure optimal performance and security for your DNS automation infrastructure.
