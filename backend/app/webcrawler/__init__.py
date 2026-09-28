"""Web crawler (mirrors Go internal/webcrawler)."""
from .agent import (
    AGENT_ID,
    CrawlerAgent,
    matches_any_api_template,
    validate_job_draft,
)
from .fetcher import Fetcher, normalize_url, public_address
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

__all__ = [
    "AGENT_ID", "CrawlerAgent", "matches_any_api_template", "validate_job_draft",
    "Fetcher", "normalize_url", "public_address",
    "BlockedURLError", "CrawlTimeoutError", "InvalidURLError", "NoContentError",
    "PageObservation", "Request", "ResourceObservation", "ResourceRequest",
    "ResponseLargeError", "Result", "ScriptScanObservation", "UpstreamFailedError",
]
