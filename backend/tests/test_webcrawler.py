"""P5c webcrawler tests: SSRF, extraction, providers, fallback loop."""
import pytest

from app.harness import Runtime
from app.webcrawler import (
    BlockedURLError,
    CrawlerAgent,
    Fetcher,
    InvalidURLError,
    NoContentError,
    PageObservation,
    Request,
    ResourceObservation,
    ScriptScanObservation,
    matches_any_api_template,
    normalize_url,
    public_address,
)
from app.webcrawler.agent import validate_job_draft
from app.webcrawler.embedded import extract_embedded_job_posting
from app.webcrawler.fetcher import (
    alibaba_campus_position_id,
    bytedance_campus_position_id,
    format_alibaba_position,
    format_bytedance_position,
)
from app.webcrawler.htmltree import extract_document


def test_public_address():
    assert public_address("8.8.8.8") is True
    assert public_address("127.0.0.1") is False
    assert public_address("10.0.0.1") is False
    assert public_address("192.168.1.1") is False
    assert public_address("169.254.1.1") is False
    assert public_address("fe80::1") is False
    assert public_address("::1") is False
    assert public_address("192.0.2.1") is False
    assert public_address("198.18.0.1") is False
    assert public_address("198.18.0.1", allow_benchmark=True) is True
    assert public_address("240.0.0.1") is False


def test_normalize_url():
    assert normalize_url("example.com").scheme == "https"
    assert normalize_url("example.com").hostname == "example.com"
    with pytest.raises(InvalidURLError):
        normalize_url("")
    with pytest.raises(InvalidURLError):
        normalize_url("ftp://example.com")
    with pytest.raises(InvalidURLError):
        normalize_url("http://user:pass@example.com")


def test_provider_position_ids():
    assert alibaba_campus_position_id(normalize_url("https://campus-talent.alibaba.com/campus/position/123")) == "123"
    assert alibaba_campus_position_id(normalize_url("https://example.com/campus/position/123")) is None
    assert bytedance_campus_position_id(normalize_url("https://jobs.bytedance.com/campus/position/456/detail")) == "456"
    assert bytedance_campus_position_id(normalize_url("https://example.com/campus/position/456/detail")) is None


def test_format_alibaba_position():
    text = format_alibaba_position({
        "name": "高级 Go 工程师", "batchName": "2025 校招", "categoryName": "后端",
        "workLocations": ["杭州", "北京"], "circleNames": ["支付技术"],
        "description": "负责支付平台架构设计", "requirement": "要求五年以上经验",
    })
    assert text.startswith("职位：高级 Go 工程师")
    assert "工作地点：杭州、北京" in text
    assert "岗位职责" in text


def test_format_bytedance_position():
    text = format_bytedance_position({
        "title": "后端工程师", "code": "A123",
        "recruit_type": {"parent": {"i18n_name": "Engineering"}, "i18n_name": "Backend"},
        "job_category": {"i18n_name": "Backend"},
        "city_info": {"i18n_name": "Beijing", "name": "北京"},
        "city_list": [{"i18n_name": "Hangzhou", "name": "杭州"}],
        "description": "负责服务开发", "requirement": "要求熟悉 Go",
    })
    assert text.startswith("职位：后端工程师")
    assert "职位编号：A123" in text
    assert "工作地点：Beijing、Hangzhou" in text


def test_extract_document_html():
    html = (
        "<html><head><title>Job Title</title></head><body>"
        "<div>负责支付平台整体架构设计与核心交易链路建设，主导高并发系统治理与稳定性保障。</div>"
        "<p>要求五年以上 Go 服务端经验，熟悉分布式系统与高并发架构。</p>"
        "</body></html>"
    ).encode()
    title, text = extract_document("text/html", html)
    assert title == "Job Title"
    assert "支付平台整体架构设计" in text


def test_extract_document_plain_and_short():
    title, text = extract_document("text/plain", ("a" * 50).encode())
    assert title == ""
    assert text == "a" * 50
    with pytest.raises(NoContentError):
        extract_document("text/plain", b"short")


def test_extract_embedded_job_posting():
    html = (
        '<html><body><script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"JobPosting","title":"高级 Go 工程师",'
        '"description":"负责支付平台整体架构设计与核心交易链路建设，主导高并发系统治理与稳定性保障，推动服务化改造并交付多个重点项目。",'
        '"qualifications":"要求五年以上 Go 服务端经验，熟悉分布式系统与高并发架构，掌握 Redis、Kafka 与 MySQL，具备良好的系统设计能力。",'
        '"employmentType":"全职","hiringOrganization":{"name":"示例公司"},'
        '"jobLocation":[{"address":{"addressLocality":"北京","addressCountry":"中国"}}],"identifier":"12345"}'
        "</script></body></html>"
    ).encode()
    result = extract_embedded_job_posting(html, "https://example.com/jobs/12345")
    assert result is not None
    assert result.provider == "embedded-json"
    assert result.title == "高级 Go 工程师"
    assert result.text.startswith("职位：高级 Go 工程师")
    assert extract_embedded_job_posting(b"<html><body>no scripts</body></html>", "https://example.com") is None


async def test_ssrf_blocks_private_resolution():
    async def private_resolver(host):
        return ["127.0.0.1"]

    fetcher = Fetcher(resolver=private_resolver)
    with pytest.raises(BlockedURLError):
        await fetcher.fetch(Request(url="https://example.com/job/1"))


def test_validate_job_draft():
    from app.webcrawler.types import JobDraft

    assert validate_job_draft(JobDraft(title="工程师", responsibilities="a" * 40, requirements="b" * 40)) is None
    assert "title" in str(validate_job_draft(JobDraft(title=" ", responsibilities="a" * 40, requirements="b" * 40)))
    assert "incomplete" in str(validate_job_draft(JobDraft(title="工程师", responsibilities="短", requirements="短")))


def test_matches_any_api_template():
    target = normalize_url("https://jobs.example.com/api/v1/job/posts/42")
    templates = ["https://jobs.example.com/api/v1/job/posts/{id}"]
    assert matches_any_api_template(target, templates) is True
    other = normalize_url("https://other.example.com/api/v1/job/posts/42")
    assert matches_any_api_template(other, templates) is False


class FakeFetcher:
    async def fetch(self, request):
        raise NoContentError()

    async def inspect_page(self, request):
        return PageObservation(
            url="https://jobs.example.com/post/1", title="职位页", text="x" * 100,
            scriptUrls=[], candidateUrls=["https://jobs.example.com/api/v1/job/posts/1"],
        )

    async def scan_scripts(self, request):
        return ScriptScanObservation(pageUrl="https://jobs.example.com/post/1", scriptsScanned=[])

    async def fetch_resource(self, request):
        return ResourceObservation(url=request.url, contentType="application/json", content="{}")


class FakeClient:
    def __init__(self, decision):
        self._decision = decision
        self.calls = 0

    async def chat_json(self, messages, out_model):
        self.calls += 1
        return out_model.model_validate(self._decision)


def _job_decision():
    return {
        "action": "finish", "tool": "", "url": "", "reason": "已获取完整职位信息",
        "job": {
            "title": "高级 Go 工程师",
            "responsibilities": "负责支付平台架构设计，" * 4,
            "requirements": "要求五年以上 Go 经验，" * 4,
            "locations": ["北京"], "employmentType": "全职",
            "organization": "示例公司", "identifier": "123",
        },
    }


async def test_agent_fallback_finish():
    runtime = Runtime(FakeClient(_job_decision()))
    agent = CrawlerAgent(runtime, FakeFetcher())
    result = await agent.crawl(Request(url="https://jobs.example.com/post/1"))
    assert result.provider == "agent-fallback"
    assert result.strategy == "agent_fallback"
    assert result.title == "高级 Go 工程师"
    assert "岗位职责" in result.text


async def test_agent_fallback_fail_raises_no_content():
    runtime = Runtime(FakeClient({"action": "fail", "tool": "", "url": "", "job": None, "reason": "证据不足"}))
    agent = CrawlerAgent(runtime, FakeFetcher())
    with pytest.raises(NoContentError):
        await agent.crawl(Request(url="https://jobs.example.com/post/1"))


async def test_agent_fallback_exhausts_iterations():
    decision = {"action": "call_tool", "tool": "inspect_web_page", "url": "https://jobs.example.com/post/1", "job": None, "reason": "再观察一次"}
    runtime = Runtime(FakeClient(decision))
    agent = CrawlerAgent(runtime, FakeFetcher())
    with pytest.raises(NoContentError, match="exhausted"):
        await agent.crawl(Request(url="https://jobs.example.com/post/1"))
    assert runtime.agent("web_crawler").tools == [
        "fetch_web_content", "inspect_web_page", "scan_web_scripts", "fetch_web_resource"
    ]
