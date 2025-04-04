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