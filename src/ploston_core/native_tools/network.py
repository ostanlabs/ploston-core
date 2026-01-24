"""Core network and HTTP request functions."""

import asyncio
import time
from typing import Any


async def make_http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: Any | None = None,
    params: dict[str, str] | None = None,
    timeout: int = 30,
    max_retries: int = 3,
    retry_delay: int = 1,
) -> dict[str, Any]:
    """Make HTTP requests with retry logic and comprehensive response processing.

    Args:
        url: The URL to request
        method: HTTP method (GET, POST, PUT, DELETE, PATCH)
        headers: Optional request headers
        data: Optional request body data
        params: Optional query parameters
        timeout: Request timeout in seconds
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries in seconds

    Returns:
        Dictionary containing:
        - success: Whether request was successful
        - status_code: HTTP status code
        - data: Response data (parsed JSON or text)
        - headers: Response headers
        - elapsed_time: Request duration in seconds
        - content_length: Response size in bytes
        - error: Error message if request failed
    """
    try:
        # Import httpx for async HTTP requests
        try:
            import httpx
        except ImportError:
            return {
                "success": False,
                "error": "httpx library not available. Install with: pip install httpx",
            }

        if not url:
            return {"success": False, "error": "URL is required"}

        method = method.upper()
        headers = headers or {}
        params = params or {}

        # Retry logic
        last_error = None
        for attempt in range(max_retries):
            try:
                start_time = time.time()

                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        json=data if data and method in ["POST", "PUT", "PATCH"] else None,
                        params=params,
                    )

                elapsed_time = time.time() - start_time

                # Try to parse JSON, fallback to text
                try:
                    response_data = response.json()
                except Exception:
                    response_data = response.text

                return {
                    "success": True,
                    "status_code": response.status_code,
                    "data": response_data,
                    "headers": dict(response.headers),
                    "elapsed_time": elapsed_time,
                    "content_length": len(response.content),
                    "method": method,
                    "url": url,
                }

            except httpx.TimeoutException:
                last_error = f"Request timeout after {timeout}s"
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    continue
            except httpx.RequestError as e:
                last_error = f"Request error: {str(e)}"
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    continue
            except Exception as e:
                last_error = f"Unexpected error: {str(e)}"
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    continue

        return {
            "success": False,
            "error": f"Request failed after {max_retries} attempts: {last_error}",
        }

    except Exception as e:
        return {"success": False, "error": f"HTTP request failed: {str(e)}"}


async def ping_host(host: str, count: int = 4, timeout: int = 5) -> dict[str, Any]:
    """Ping a host to check connectivity and measure latency.

    Args:
        host: Hostname or IP address to ping
        count: Number of ping attempts
        timeout: Timeout for each ping in seconds

    Returns:
        Dictionary containing:
        - success: Whether ping was successful
        - host: The host that was pinged
        - packets_sent: Number of packets sent
        - packets_received: Number of packets received
        - packet_loss: Packet loss percentage
        - min_latency: Minimum latency in ms
        - max_latency: Maximum latency in ms
        - avg_latency: Average latency in ms
        - error: Error message if ping failed
    """
    try:
        if not host:
            return {"success": False, "error": "Host is required"}

        # Use asyncio subprocess to run ping command
        import platform

        system = platform.system().lower()

        if system == "windows":
            cmd = ["ping", "-n", str(count), "-w", str(timeout * 1000), host]
        else:  # Linux, macOS
            cmd = ["ping", "-c", str(count), "-W", str(timeout), host]

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()
        output = stdout.decode()

        if process.returncode != 0:
            return {"success": False, "error": f"Ping failed: {stderr.decode()}"}

        # Parse ping output (simplified)
        import re

        # Extract latency values
        latencies = []
        if system == "windows":
            pattern = r"time[=<](\d+)ms"
        else:
            pattern = r"time=(\d+\.?\d*)\s*ms"

        for match in re.finditer(pattern, output):
            latencies.append(float(match.group(1)))

        packets_received = len(latencies)
        packet_loss = ((count - packets_received) / count) * 100

        return {
            "success": True,
            "host": host,
            "packets_sent": count,
            "packets_received": packets_received,
            "packet_loss": packet_loss,
            "min_latency": min(latencies) if latencies else None,
            "max_latency": max(latencies) if latencies else None,
            "avg_latency": sum(latencies) / len(latencies) if latencies else None,
            "latencies": latencies,
        }

    except Exception as e:
        return {"success": False, "error": f"Ping failed: {str(e)}"}


async def dns_lookup(hostname: str, record_type: str = "A") -> dict[str, Any]:
    """Perform DNS lookup for a hostname.

    Args:
        hostname: The hostname to look up
        record_type: DNS record type (A, AAAA, MX, TXT, CNAME, NS)

    Returns:
        Dictionary containing:
        - success: Whether lookup was successful
        - hostname: The hostname that was looked up
        - record_type: The DNS record type
        - records: List of DNS records found
        - record_count: Number of records found
        - error: Error message if lookup failed
    """
    try:
        if not hostname:
            return {"success": False, "error": "Hostname is required"}

        import socket

        record_type = record_type.upper()

        if record_type == "A":
            # IPv4 address lookup
            try:
                records = [socket.gethostbyname(hostname)]
            except socket.gaierror as e:
                return {"success": False, "error": f"DNS lookup failed: {str(e)}"}
        elif record_type == "AAAA":
            # IPv6 address lookup
            try:
                results = socket.getaddrinfo(hostname, None, socket.AF_INET6)
                records = list(set([r[4][0] for r in results]))
            except socket.gaierror as e:
                return {"success": False, "error": f"DNS lookup failed: {str(e)}"}
        else:
            # For other record types, use dnspython if available
            try:
                import dns.resolver

                answers = dns.resolver.resolve(hostname, record_type)
                records = [str(rdata) for rdata in answers]
            except ImportError:
                return {
                    "success": False,
                    "error": f"dnspython library required for {record_type} records. Install with: pip install dnspython",
                }
            except Exception as e:
                return {"success": False, "error": f"DNS lookup failed: {str(e)}"}

        return {
            "success": True,
            "hostname": hostname,
            "record_type": record_type,
            "records": records,
            "record_count": len(records),
        }

    except Exception as e:
        return {"success": False, "error": f"DNS lookup failed: {str(e)}"}


async def check_port(host: str, port: int, timeout: int = 5) -> dict[str, Any]:
    """Check if a port is open on a host.

    Args:
        host: Hostname or IP address
        port: Port number to check
        timeout: Connection timeout in seconds

    Returns:
        Dictionary containing:
        - success: Whether check was successful
        - host: The host that was checked
        - port: The port that was checked
        - is_open: Whether the port is open
        - response_time: Time taken to connect in seconds
        - error: Error message if check failed
    """
    try:
        if not host:
            return {"success": False, "error": "Host is required"}

        if not port or port < 1 or port > 65535:
            return {"success": False, "error": "Valid port number (1-65535) is required"}

        start_time = time.time()

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            writer.close()
            await writer.wait_closed()

            response_time = time.time() - start_time

            return {
                "success": True,
                "host": host,
                "port": port,
                "is_open": True,
                "response_time": response_time,
            }

        except (TimeoutError, ConnectionRefusedError, OSError):
            response_time = time.time() - start_time

            return {
                "success": True,
                "host": host,
                "port": port,
                "is_open": False,
                "response_time": response_time,
            }

    except Exception as e:
        return {"success": False, "error": f"Port check failed: {str(e)}"}
