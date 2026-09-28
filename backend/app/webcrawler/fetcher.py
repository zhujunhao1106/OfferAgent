"""SSRF-safe content fetcher + explorer mirroring Go webcrawler/{fetcher,explorer,alibaba,bytedance}.go."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

import httpx

from .embedded import extract_embedded_job_posting
from .htmltree import (
    Element,
    attribute,
    extract_document,
    parse_html,
)
from .types import (
    BlockedURLError,
    CrawlTimeoutError,
    InvalidURLError,
    NoContentError,
    PageObservation,
    Request,
    ResourceObservation,
    ResourceRequest,
    ResponseLargeError,
    Result,
    ScriptScanObservation,
    UpstreamFailedError,
)
from .util import (
    append_unique,
    clean_list,
    compact_runes,
    limit_strings,
    localized_name,
    merge_urls,
    prioritized_scripts,
)

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; OfferPilot-WebCrawler/1.0)"

MAX_OBSERVATION_TEXT = 5000
MAX_OBSERVATION_SOURCE = 7000
MAX_RESOURCE_CONTENT = 12000
MAX_OBSERVATION_URLS = 32
MAX_OBSERVATION_SCRIPT_URLS = 32
MAX_SCRIPTS_PER_SCAN = 12

RESERVED_PREFIXES = [
    "0.0.0.0/8", "100.64.0.0/10", "169.254.0.0/16", "192.0.0.0/24",
    "192.0.2.0/24", "192.88.99.0/24", "198.51.100.0/24", "203.0.113.0/24",
    "240.0.0.0/4", "64:ff9b::/96", "64:ff9b:1::/48", "100::/64",
    "2001::/23", "2001:db8::/32", "2002::/16", "fec0::/10",
]
BENCHMARK_PREFIX = "198.18.0.0/15"

_RESERVED = [ipaddress.ip_network(prefix) for prefix in RESERVED_PREFIXES]
_BENCHMARK = ipaddress.ip_network(BENCHMARK_PREFIX)

ALIBABA_POSITION_PATH = re.compile(r"^/campus/position/([0-9]+)/?$")
ALIBABA_TOKEN_PATTERN = re.compile(r"""__token__\s*:\s*["']([^"']+)["']""")
ALIBABA_CHANNEL_PATTERN = re.compile(r"""(?s)channelCodeMap\s*:\s*\{.{0,1000}?\bcampus\s*:\s*["']([^"']+)["']""")
BYTEDANCE_POSITION_PATH = re.compile(r"^/campus/position/([0-9]+)/detail/?$")

QUOTED_URL_PATTERN = re.compile(r"""["']((?:https?://|/)[^"'\s<>\\]{2,300})["']""")
API_BASE_PATH_PATTERN = re.compile(r"(?i)^/api(?:/v[0-9]+)?/?$")
DETAIL_ENDPOINT_PATTERN = re.compile(
    r"""(?is)(?:getPositionDetail|getJobDetail|getPostingDetail).{0,800}?["'](/(?:job|position)/[^"']{1,160})["']"""
)


def public_address(addr: str, allow_benchmark: bool = False) -> bool:
    address = ipaddress.ip_address(addr)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address in _BENCHMARK:
        return allow_benchmark
    if (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
    ):
        return False
    for prefix in _RESERVED:
        if address in prefix:
            return False
    return True


def normalize_url(raw: str):
    raw = raw.strip()
    if not raw:
        raise InvalidURLError()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.hostname:
        raise InvalidURLError()
    if parsed.scheme not in ("http", "https"):
        raise InvalidURLError("only http and https are supported")
    if parsed.username is not None or parsed.password is not None:
        raise InvalidURLError("embedded credentials are not supported")
    return parsed._replace(fragment="")


def _try_normalize(raw: str):
    try:
        return normalize_url(raw)
    except InvalidURLError:
        return None


def read_response_body(resp: httpx.Response, max_bytes: int) -> bytes:
    if resp.status_code < 200 or resp.status_code >= 300:
        raise UpstreamFailedError(f"HTTP {resp.status_code}")
    content_length = resp.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise ResponseLargeError(f"limit is {max_bytes} bytes")
    data = resp.content
    if len(data) > max_bytes:
        raise ResponseLargeError(f"limit is {max_bytes} bytes")
    return data


def classify_request_error(err: Exception) -> Exception:
    if isinstance(err, asyncio.CancelledError):
        return err
    if isinstance(err, CrawlTimeoutError):
        return err
    if isinstance(err, httpx.TimeoutException):
        return CrawlTimeoutError(str(err))
    if isinstance(err, (BlockedURLError, InvalidURLError, UpstreamFailedError, ResponseLargeError, NoContentError)):
        return err
    return UpstreamFailedError(str(err))


def safe_tool_error(err: Exception) -> str:
    if isinstance(err, InvalidURLError):
        return "invalid URL"
    if isinstance(err, BlockedURLError):
        return "blocked URL"
    if isinstance(err, ResponseLargeError):
        return "response too large"
    if isinstance(err, CrawlTimeoutError):
        return "request timed out"
    return "resource fetch failed"


class Fetcher:
    def __init__(
        self,
        *,
        request_timeout: float = 10.0,
        max_response_bytes: int = 2 << 20,
        max_redirects: int = 3,
        allow_benchmark_tunnel: bool = False,
        user_agent: str = DEFAULT_USER_AGENT,
        resolver=None,
    ):
        self._request_timeout = request_timeout if request_timeout > 0 else 10.0
        self._max_response_bytes = max_response_bytes if max_response_bytes > 0 else 2 << 20
        self._max_redirects = max_redirects if max_redirects > 0 else 3
        self._allow_benchmark = allow_benchmark_tunnel
        self._user_agent = user_agent.strip() or DEFAULT_USER_AGENT
        self._resolver = resolver

    # --- public API ---

    async def fetch(self, request: Request) -> Result:
        target = normalize_url(request.url)
        async with httpx.AsyncClient(follow_redirects=False, timeout=self._request_timeout) as client:
            page, content_type, final_url = await self._get(client, target)
            if (position_id := alibaba_campus_position_id(final_url)):
                return await self._fetch_alibaba_campus_position(client, final_url, position_id, page)
            if (position_id := bytedance_campus_position_id(final_url)):
                return await self._fetch_bytedance_campus_position(client, final_url, position_id)
            if (embedded := extract_embedded_job_posting(page, urlunparse(final_url))) is not None:
                return embedded
            title, text = extract_document(content_type, page)
            return Result(text=text, title=title, source=urlunparse(final_url), provider="generic-html")

    async def inspect_page(self, request: Request) -> PageObservation:
        target = normalize_url(request.url)
        async with httpx.AsyncClient(follow_redirects=False, timeout=self._request_timeout) as client:
            page, content_type, final_url = await self._get(client, target)
        try:
            title, text = extract_document(content_type, page)
        except NoContentError:
            title, text = "", ""
        scripts, links = discover_document_urls(page, final_url)
        candidates = merge_urls(
            links,
            discover_text_urls(page.decode("utf-8", "replace"), final_url, final_url),
            MAX_OBSERVATION_URLS,
        )
        return PageObservation(
            url=urlunparse(final_url),
            title=compact_runes(title, 500),
            text=compact_runes(text, MAX_OBSERVATION_TEXT),
            contentType=content_type,
            scriptUrls=limit_strings(scripts, MAX_OBSERVATION_SCRIPT_URLS),
            candidateUrls=candidates,
            sourceExcerpt=page_source_excerpt(page),
        )

    async def scan_scripts(self, request: Request) -> ScriptScanObservation:
        page = await self.inspect_page(request)
        scripts = prioritized_scripts(page.scriptUrls, MAX_SCRIPTS_PER_SCAN)
        if not scripts:
            raise NoContentError()
        semaphore = asyncio.Semaphore(4)

        async def scan_one(index: int, url: str):
            try:
                async with semaphore:
                    observation = await self.fetch_resource(ResourceRequest(url=url, referer=page.url))
                return index, url, observation
            except Exception:
                return None

        results = [r for r in await asyncio.gather(*(scan_one(i, u) for i, u in enumerate(scripts))) if r is not None]
        if not results:
            raise NoContentError()
        results.sort(key=lambda item: item[0])
        content_parts: list[str] = []
        completed: list[str] = []
        candidates: list[str] = []
        templates: list[str] = []
        for _index, url, observation in results:
            completed.append(url)
            candidates = merge_urls(candidates, observation.candidateUrls, MAX_OBSERVATION_URLS)
            templates = merge_urls(templates, observation.apiTemplates, 16)
            if observation.content.strip():
                content_parts.append("SCRIPT " + url + "\n" + observation.content + "\n===\n")
        return ScriptScanObservation(
            pageUrl=page.url, scriptsScanned=completed,
            content=compact_runes("".join(content_parts), MAX_RESOURCE_CONTENT),
            candidateUrls=candidates, apiTemplates=templates,
        )

    async def fetch_resource(self, input: ResourceRequest) -> ResourceObservation:
        target = normalize_url(input.url)
        headers = self._resource_headers()
        if (referer := _try_normalize(input.referer)) is not None:
            headers["Referer"] = urlunparse(referer)
        async with httpx.AsyncClient(follow_redirects=False, timeout=self._request_timeout) as client:
            await self._check_public(target)
            try:
                resp = await client.get(urlunparse(target), headers=headers)
            except Exception as err:
                raise classify_request_error(err) from err
        content_type = resp.headers.get("content-type", "")
        data = read_response_body(resp, self._max_response_bytes)
        final_url = _try_normalize(str(resp.url)) or target
        root_url = final_url
        if (referer := _try_normalize(input.referer)) is not None:
            root_url = referer
        content = data.decode("utf-8", "replace")
        return ResourceObservation(
            url=urlunparse(final_url),
            contentType=content_type,
            content=resource_excerpt(data, content_type),
            candidateUrls=discover_text_urls(content, final_url, root_url),
            apiTemplates=discover_api_templates(content, root_url),
        )

    # --- HTTP plumbing ---

    async def _get(self, client: httpx.AsyncClient, target):
        current = target
        redirects = 0
        while True:
            await self._check_public(current)
            try:
                resp = await client.get(urlunparse(current), headers=self._page_headers())
            except Exception as err:
                raise classify_request_error(err) from err
            location = resp.headers.get("location", "")
            if resp.status_code in (301, 302, 303, 307, 308) and location:
                if redirects >= self._max_redirects:
                    raise UpstreamFailedError(f"more than {self._max_redirects} redirects")
                current = normalize_url(urljoin(urlunparse(current), location))
                redirects += 1
                continue
            data = read_response_body(resp, self._max_response_bytes)
            return data, resp.headers.get("content-type", ""), current

    async def _post_json(self, client: httpx.AsyncClient, target, referer, body: bytes) -> bytes:
        await self._check_public(target)
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": urlunparse(referer),
        }
        try:
            resp = await client.post(urlunparse(target), content=body, headers=headers)
        except Exception as err:
            raise classify_request_error(err) from err
        return read_response_body(resp, self._max_response_bytes)

    async def _check_public(self, target) -> None:
        addresses = await self._resolve(target.hostname)
        for address in addresses:
            if not public_address(address, self._allow_benchmark):
                raise BlockedURLError("private, loopback, link-local, or reserved address")

    async def _resolve(self, host: str) -> list[str]:
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            pass
        if self._resolver is not None:
            return list(await self._resolver(host))
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses: list[str] = []
        for info in infos:
            address = info[4][0]
            if address not in addresses:
                addresses.append(address)
        return addresses

    def _page_headers(self) -> dict:
        return {
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        }

    def _resource_headers(self) -> dict:
        return {
            "User-Agent": self._user_agent,
            "Accept": "application/json,text/plain,text/html,application/javascript,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        }

    # --- provider fast paths ---

    async def _fetch_alibaba_campus_position(self, client, page_url, position_id: str, page: bytes) -> Result:
        page_text = page.decode("utf-8", "replace")
        token_match = ALIBABA_TOKEN_PATTERN.search(page_text)
        if token_match is None:
            raise UpstreamFailedError("Alibaba page token is missing")
        channel = "new_campus_group_official_site"
        channel_match = ALIBABA_CHANNEL_PATTERN.search(page_text)
        if channel_match is not None and channel_match.group(1).strip():
            channel = channel_match.group(1).strip()
        payload = json.dumps({"id": position_id, "channel": channel, "language": "zh"}, ensure_ascii=False).encode("utf-8")
        detail_url = normalize_url(
            urlunparse((page_url.scheme, page_url.netloc, "/position/detail", "", urlencode({"_csrf": token_match.group(1)}), ""))
        )
        body = await self._post_json(client, detail_url, page_url, payload)
        try:
            envelope = json.loads(body)
        except (ValueError, TypeError):
            raise UpstreamFailedError("invalid Alibaba position response")
        if not envelope.get("success"):
            message = str(envelope.get("errorMsg", "")).strip()
            if not message:
                message = "position detail request was rejected"
            raise UpstreamFailedError(message)
        content = envelope.get("content") or {}
        text = format_alibaba_position(content)
        if len(text) < 80:
            raise NoContentError()
        return Result(
            text=text, title=str(content.get("name", "")).strip(),
            source=urlunparse(page_url), provider="alibaba-campus",
        )

    async def _fetch_bytedance_campus_position(self, client, page_url, position_id: str) -> Result:
        detail_url = normalize_url(
            urlunparse((page_url.scheme, page_url.netloc, f"/api/v1/job/posts/{position_id}", "", "", ""))
        )
        headers = self._resource_headers()
        headers["Referer"] = urlunparse(page_url)
        async with httpx.AsyncClient(follow_redirects=False, timeout=self._request_timeout) as detail_client:
            await self._check_public(detail_url)
            try:
                resp = await detail_client.get(urlunparse(detail_url), headers=headers)
            except Exception as err:
                raise classify_request_error(err) from err
        body = read_response_body(resp, self._max_response_bytes)
        try:
            envelope = json.loads(body)
        except (ValueError, TypeError):
            raise UpstreamFailedError("invalid ByteDance position response")
        detail = (envelope.get("data") or {}).get("job_post_detail") or {}
        if envelope.get("code") != 0 or not str(detail.get("id", "")).strip():
            message = str(envelope.get("message", "")).strip()
            if not message:
                message = "position detail request was rejected"
            raise UpstreamFailedError(message)
        text = format_bytedance_position(detail)
        if len(text) < 80:
            raise NoContentError()
        return Result(
            text=text, title=str(detail.get("title", "")).strip(),
            source=urlunparse(page_url), provider="bytedance-campus",
        )


def alibaba_campus_position_id(target) -> str | None:
    if target.hostname != "campus-talent.alibaba.com":
        return None
    match = ALIBABA_POSITION_PATH.match(target.path)
    if match is None:
        return None
    return match.group(1)


def bytedance_campus_position_id(target) -> str | None:
    if target.hostname != "jobs.bytedance.com":
        return None
    match = BYTEDANCE_POSITION_PATH.match(target.path)
    if match is None:
        return None
    return match.group(1)


def format_alibaba_position(position: dict) -> str:
    sections: list[str] = []
    if (name := str(position.get("name", "")).strip()):
        sections.append("职位：" + name)
    metadata = clean_list([str(position.get("batchName", "")).strip(), str(position.get("categoryName", "")).strip()])
    if metadata:
        sections.append("招聘类型：" + " · ".join(metadata))
    if (locations := clean_list(position.get("workLocations") or [])):
        sections.append("工作地点：" + "、".join(locations))
    if (circles := clean_list(position.get("circleNames") or [])):
        sections.append("招聘组织：" + "、".join(circles))
    if (description := normalize_extracted_text(str(position.get("description", "")))):
        sections.append("岗位职责\n" + description)
    if (requirement := normalize_extracted_text(str(position.get("requirement", "")))):
        sections.append("岗位要求与加分项\n" + requirement)
    return "\n\n".join(sections)


def format_bytedance_position(position: dict) -> str:
    sections: list[str] = []
    if (title := str(position.get("title", "")).strip()):
        sections.append("职位：" + title)
    recruit_type = position.get("recruit_type") or {}
    parent = recruit_type.get("parent") or {}
    job_category = position.get("job_category") or {}
    metadata = clean_list([
        localized_name(parent.get("i18n_name", ""), parent.get("name", "")),
        localized_name(recruit_type.get("i18n_name", ""), recruit_type.get("name", "")),
        localized_name(job_category.get("i18n_name", ""), job_category.get("name", "")),
    ])
    if metadata:
        sections.append("招聘类型：" + " · ".join(metadata))
    city_info = position.get("city_info") or {}
    locations = [localized_name(city_info.get("i18n_name", ""), city_info.get("name", ""))]
    for city in position.get("city_list") or []:
        locations.append(localized_name(city.get("i18n_name", ""), city.get("name", "")))
    if (locations := clean_list(locations)):
        sections.append("工作地点：" + "、".join(locations))
    if (code := str(position.get("code", "")).strip()):
        sections.append("职位编号：" + code)
    if (description := normalize_extracted_text(str(position.get("description", "")))):
        sections.append("岗位职责\n" + description)
    if (requirement := normalize_extracted_text(str(position.get("requirement", "")))):
        sections.append("岗位要求与加分项\n" + requirement)
    return "\n\n".join(sections)


def normalize_extracted_text(value: str) -> str:
    from .htmltree import normalize_extracted_text as _norm
    return _norm(value)


# --- discovery helpers ---

def discover_document_urls(page: bytes, base) -> tuple[list[str], list[str]]:
    try:
        document = parse_html(page.decode("utf-8", "replace"))
    except Exception:
        return [], []
    scripts: list[str] = []
    links: list[str] = []

    def walk(node) -> None:
        nonlocal scripts, links
        if isinstance(node, Element):
            tag = node.tag
            if tag == "script":
                if (resolved := resolve_candidate_url(attribute(node, "src"), base)):
                    scripts = append_unique(scripts, resolved, MAX_OBSERVATION_SCRIPT_URLS)
            elif tag in ("a", "link"):
                if (resolved := resolve_candidate_url(attribute(node, "href"), base)) and interesting_url(resolved):
                    links = append_unique(links, resolved, MAX_OBSERVATION_URLS)
            for child in node.children:
                walk(child)

    walk(document)
    return scripts, links


def discover_text_urls(content: str, base, root_base) -> list[str]:
    result: list[str] = []
    fragments: list[str] = []
    for match in QUOTED_URL_PATTERN.findall(content)[:200]:
        fragments.append(match)
        resolution_base = base
        if match.startswith("/") and root_base is not None:
            resolution_base = root_base
        resolved = resolve_candidate_url(match, resolution_base)
        if resolved and interesting_url(resolved):
            result = append_unique(result, resolved, MAX_OBSERVATION_URLS)
    return merge_urls(result, composed_api_candidates(fragments, root_base), MAX_OBSERVATION_URLS)


def composed_api_candidates(fragments: list[str], root) -> list[str]:
    if root is None:
        return []
    prefixes: list[str] = []
    endpoints: list[str] = []
    for fragment in fragments:
        path = fragment.replace("\\/", "/").strip()
        lower = path.lower()
        if API_BASE_PATH_PATTERN.match(lower):
            prefixes = append_unique(prefixes, path.rstrip("/"), 8)
            continue
        if path.startswith("/") and (lower.startswith("/job/") or lower.startswith("/position/") or lower.startswith("/search/")):
            endpoints = append_unique(endpoints, path, 24)
    result: list[str] = []
    for prefix in prefixes:
        for endpoint in endpoints:
            resolved = resolve_candidate_url(prefix + endpoint, root)
            if resolved:
                result = append_unique(result, resolved, MAX_OBSERVATION_URLS)
    return result


def discover_api_templates(content: str, root) -> list[str]:
    if root is None:
        return []
    fragments = [match for match in QUOTED_URL_PATTERN.findall(content)[:400]]
    prefixes: list[str] = []
    for fragment in fragments:
        path = fragment.replace("\\/", "/").strip()
        if API_BASE_PATH_PATTERN.match(path):
            prefixes = append_unique(prefixes, path.rstrip("/"), 8)
    if not prefixes:
        return []
    result: list[str] = []
    for match in DETAIL_ENDPOINT_PATTERN.findall(content)[:24]:
        endpoint = match.replace("\\/", "/").strip()
        if not endpoint.endswith("/"):
            endpoint += "/"
        for prefix in prefixes:
            resolved = resolve_candidate_url(prefix + endpoint, root)
            if resolved:
                result = append_unique(result, resolved + "{id}", 16)
    return result


def resolve_candidate_url(raw: str, base) -> str:
    raw = raw.strip().replace("\\/", "/")
    if not raw or raw.startswith("//"):
        if raw.startswith("//"):
            raw = base.scheme + ":" + raw
        else:
            return ""
    resolved = urlparse(urljoin(urlunparse(base), raw))
    if resolved.scheme not in ("http", "https"):
        return ""
    resolved = resolved._replace(fragment="")
    return urlunparse(resolved)


def interesting_url(value: str) -> bool:
    lower = value.lower()
    return any(marker in lower for marker in ("/api/", "job", "position", "career", "detail", "search"))


def page_source_excerpt(page: bytes) -> str:
    content = page.decode("utf-8", "replace")
    windows = relevant_windows(
        content, ["__next_data__", "application/ld+json", "application/json", "/api/", "job", "position"],
        MAX_OBSERVATION_SOURCE,
    )
    if windows.strip():
        return windows
    return compact_runes(content, MAX_OBSERVATION_SOURCE)


def resource_excerpt(data: bytes, content_type: str) -> str:
    content = data.decode("utf-8", "replace").strip()
    lower_type = content_type.lower()
    if "json" in lower_type or content.startswith("{") or content.startswith("["):
        return compact_runes(content, MAX_RESOURCE_CONTENT)
    return relevant_windows(
        content,
        ["/api/v1", "/api/", "job/posts", "position/detail", "description", "requirement", "qualification"],
        MAX_RESOURCE_CONTENT,
    )


def relevant_windows(content: str, markers: list[str], limit: int) -> str:
    lower = content.lower()
    parts: list[str] = []
    for marker in markers:
        search_from = 0
        while sum(len(part) for part in parts) < limit:
            index = lower.find(marker.lower(), search_from)
            if index < 0:
                break
            start = max(0, index - 500)
            end = min(len(content), index + 1500)
            parts.append(content[start:end])
            parts.append("\n---\n")
            search_from = end
        if sum(len(part) for part in parts) >= limit:
            break
    if not parts:
        return compact_runes(content, limit)
    return compact_runes("".join(parts), limit)
