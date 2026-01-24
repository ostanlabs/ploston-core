"""Core Firecrawl implementation functions for MCP tools."""

import re
from typing import Any
from urllib.parse import urlparse

import httpx


async def search_web_firecrawl(
    query: str,
    base_url: str,
    api_key: str | None = None,
    limit: int = 10,
    sources: list[str] = ["web"],
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    search_type: str = "web",
    timeout: int = 300,
) -> dict[str, Any]:
    """Core implementation of Firecrawl search.

    Args:
        query: Search query string
        base_url: Firecrawl API base URL
        api_key: Optional API key for authentication
        limit: Maximum number of results
        sources: List of search sources (ignored for self-hosted v1 API)
        include_domains: Domains to include (ignored for self-hosted v1 API)
        exclude_domains: Domains to exclude (ignored for self-hosted v1 API)
        search_type: Type of search (ignored for self-hosted v1 API)
        timeout: Request timeout in seconds

    Returns:
        Dictionary with search results

    Note:
        Self-hosted Firecrawl v1 API only supports 'query' and 'limit' parameters.
        Other parameters (sources, includeDomains, excludeDomains, type) are only
        supported by the Firecrawl cloud API and are ignored here.
    """
    # Prepare payload for self-hosted v1 search API
    # Only 'query' and 'limit' are supported
    payload = {"query": query, "limit": limit}

    # Note: sources, includeDomains, excludeDomains, type are NOT supported
    # by self-hosted Firecrawl v1 API - they cause "Unrecognized key" errors

    # Prepare headers
    request_headers = {}
    if api_key:
        request_headers["Authorization"] = f"Bearer {api_key}"

    endpoint = f"{base_url}/v1/search"

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, headers=request_headers, json=payload)
        response.raise_for_status()
        data = response.json()

    # Process results
    if data.get("success") and "data" in data:
        result_data = data["data"]

        # For self-hosted v1, data is typically a list of results
        result_count = len(result_data) if isinstance(result_data, list) else 1

        summary = f"Found {result_count} results for '{query}'"

        return {
            "success": True,
            "data": result_data,
            "query": query,
            "result_count": result_count,
            "summary": summary,
        }
    else:
        raise Exception(f"Search failed: {data.get('error', 'Unknown error')}")


def _filter_excluded_urls(links: list[str], kwargs: dict[str, Any]) -> list[str]:
    """Filter URLs based on exclusion criteria."""
    filtered_links = links.copy()

    # Filter by excluded paths
    exclude_paths = kwargs.get("exclude_paths", [])
    if exclude_paths:
        filtered_links = [
            link for link in filtered_links if not any(path in link for path in exclude_paths)
        ]

    # Filter by excluded domains
    exclude_domains = kwargs.get("exclude_domains", [])
    if exclude_domains:
        filtered_links = [
            link
            for link in filtered_links
            if not any(domain in urlparse(link).netloc for domain in exclude_domains)
        ]

    # Filter by excluded keywords
    exclude_keywords = kwargs.get("exclude_keywords", [])
    if exclude_keywords:
        filtered_links = [
            link
            for link in filtered_links
            if not any(keyword.lower() in link.lower() for keyword in exclude_keywords)
        ]

    # Filter by excluded patterns (regex)
    exclude_patterns = kwargs.get("exclude_patterns", [])
    if exclude_patterns:
        for pattern in exclude_patterns:
            try:
                regex = re.compile(pattern)
                filtered_links = [link for link in filtered_links if not regex.search(link)]
            except re.error:
                pass  # Skip invalid regex patterns

    return filtered_links


async def map_website_firecrawl(
    url: str,
    base_url: str,
    api_key: str | None = None,
    limit: int = 1000,
    exclude_tags: list[str] | None = None,
    custom_headers: dict[str, str] | None = None,
    timeout: int = 300,
    **filter_kwargs,
) -> dict[str, Any]:
    """Core implementation of Firecrawl website mapping.

    Args:
        url: Target website URL to map
        base_url: Firecrawl API base URL
        api_key: Optional API key for authentication
        limit: Maximum number of links to discover
        exclude_tags: HTML tags to exclude
        custom_headers: Custom HTTP headers
        timeout: Request timeout in seconds
        **filter_kwargs: Additional filtering options (exclude_paths, exclude_domains, etc.)

    Returns:
        Dictionary with mapping results
    """
    # Prepare payload for v2 map API
    payload = {"url": url, "limit": limit}

    if exclude_tags:
        payload["excludeTags"] = exclude_tags
    if custom_headers:
        payload["headers"] = custom_headers

    # Prepare headers
    request_headers = {}
    if api_key:
        request_headers["Authorization"] = f"Bearer {api_key}"

    endpoint = f"{base_url}/v1/map"

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, headers=request_headers, json=payload)
        response.raise_for_status()
        data = response.json()

    # Process and filter results
    if data.get("success") and "links" in data:
        original_links = data["links"]
        filtered_links = _filter_excluded_urls(original_links, filter_kwargs)

        summary = f"Found {len(filtered_links)} links from {url}"
        excluded_count = len(original_links) - len(filtered_links)
        if excluded_count > 0:
            summary += f" (excluded {excluded_count} URLs)"

        return {
            "success": True,
            "urls": filtered_links,
            "total_urls": len(filtered_links),
            "original_link_count": len(original_links),
            "filtered_link_count": len(filtered_links),
            "excluded_count": excluded_count,
            "summary": summary,
        }
    else:
        raise Exception(f"Mapping failed: {data.get('error', 'Unknown error')}")


async def extract_data_firecrawl(
    urls: list[str],
    base_url: str,
    api_key: str | None = None,
    schema: dict[str, Any] | None = None,
    prompt: str | None = None,
    custom_headers: dict[str, str] | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    """Core implementation of Firecrawl data extraction.

    Args:
        urls: List of URLs to extract data from
        base_url: Firecrawl API base URL
        api_key: Optional API key for authentication
        schema: JSON schema for extraction
        prompt: Natural language prompt for extraction
        custom_headers: Custom HTTP headers
        timeout: Request timeout in seconds

    Returns:
        Dictionary with extraction results
    """
    # Prepare payload for v1 extract API
    payload = {"urls": urls}

    if schema:
        payload["schema"] = schema
    if prompt:
        payload["prompt"] = prompt
    if custom_headers:
        payload["headers"] = custom_headers

    # Prepare headers
    request_headers = {}
    if api_key:
        request_headers["Authorization"] = f"Bearer {api_key}"

    endpoint = f"{base_url}/v1/extract"

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, headers=request_headers, json=payload)
        response.raise_for_status()
        data = response.json()

    # Process results - aligned with Firecrawl API response schema
    # API returns: {"success": true, "data": <extracted content>}
    # where "data" is the extracted content (dict, string, etc.) - not per-URL results
    if data.get("success"):
        extracted_data = data.get("data")

        return {
            "success": True,
            "data": extracted_data,
            "total_urls": len(urls),
            "summary": f"Extracted data from {len(urls)} URL(s)",
        }
    else:
        raise Exception(f"Extraction failed: {data.get('error', 'Unknown error')}")


async def check_health_firecrawl(base_url: str, timeout: int = 10) -> dict[str, Any]:
    """Core implementation of Firecrawl health check.

    Args:
        base_url: Firecrawl API base URL
        timeout: Request timeout in seconds

    Returns:
        Dictionary with health status
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{base_url}/")

        if response.status_code < 500:
            return {
                "success": True,
                "status": "healthy",
                "base_url": base_url,
                "response_code": response.status_code,
                "version": "v2",
            }
        else:
            return {
                "success": False,
                "status": "unhealthy",
                "base_url": base_url,
                "response_code": response.status_code,
            }
