"""Webcrawler domain types mirroring Go internal/webcrawler/types.go."""
from __future__ import annotations

from pydantic import Field

from ..llm.schema import StrictModel


class CrawlError(Exception):
    pass


class InvalidURLError(CrawlError):
    pass


class BlockedURLError(CrawlError):
    pass


class ResponseLargeError(CrawlError):
    pass


class NoContentError(CrawlError):
    pass


class UpstreamFailedError(CrawlError):
    pass


class CrawlTimeoutError(CrawlError):
    pass


class Request(StrictModel):
    url: str = ""


class Result(StrictModel):
    text: str = ""
    title: str = ""
    source: str = ""
    provider: str = ""
    strategy: str = ""
    agent: str = ""
    tool: str = ""
    traceId: str = ""
    traceIds: list[str] = Field(default_factory=list)


class PageObservation(StrictModel):
    url: str = ""
    title: str = ""
    text: str = ""
    contentType: str = ""
    scriptUrls: list[str] = Field(default_factory=list)
    candidateUrls: list[str] = Field(default_factory=list)
    sourceExcerpt: str = ""


class ResourceRequest(StrictModel):
    url: str = ""
    referer: str = ""


class ResourceObservation(StrictModel):
    url: str = ""
    contentType: str = ""
    content: str = ""
    candidateUrls: list[str] = Field(default_factory=list)
    apiTemplates: list[str] = Field(default_factory=list)


class ScriptScanObservation(StrictModel):
    pageUrl: str = ""
    scriptsScanned: list[str] = Field(default_factory=list)
    content: str = ""
    candidateUrls: list[str] = Field(default_factory=list)
    apiTemplates: list[str] = Field(default_factory=list)


class JobDraft(StrictModel):
    title: str = Field(default="", description="job title")
    responsibilities: str = Field(default="", description="job responsibilities")
    requirements: str = Field(default="", description="job requirements")
    locations: list[str] = Field(default_factory=list, description="work locations")
    employmentType: str = Field(default="", description="employment type")
    organization: str = Field(default="", description="hiring organization")
    identifier: str = Field(default="", description="position identifier")


class CrawlDecision(StrictModel):
    action: str = Field(default="", description="call_tool, finish, or fail")
    tool: str = Field(default="", description="inspect_web_page, scan_web_scripts, or fetch_web_resource when action is call_tool; otherwise empty")
    url: str = Field(default="", description="public URL for the selected tool; otherwise empty")
    job: JobDraft | None = Field(default=None, description="validated job fields when action is finish; otherwise null")
    reason: str = Field(default="", description="short evidence-based reason for the selected action")


class URLInput(StrictModel):
    url: str


class ResourceInput(StrictModel):
    url: str
    referer: str = ""
