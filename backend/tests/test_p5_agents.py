"""P5b jobmatch + resumediagnosis agent tests."""
import pytest

from app.harness import Runtime
from app.jobmatch import (
    AGENT_ID as MATCHER_ID,
    Matcher,
    Request as MatchRequest,
    Result as MatchResult,
    validate_result as validate_match,
)
from app.resumediagnosis import (
    AGENT_ID as DIAG_ID,
    Diagnostician,
    Request as DiagRequest,
    Result as DiagResult,
    validate_result as validate_diag,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat_json(self, messages, out_model):
        self.calls.append(("text", out_model))
        return out_model.model_validate(self.responses.pop(0))

    async def chat_json_with_images(self, messages, images, out_model):
        self.calls.append(("images", out_model))
        return out_model.model_validate(self.responses.pop(0))


def valid_match():
    return {
        "score": 80,
        "matched": ["精通 Go 后端开发", "具备高并发系统设计经验", "有支付平台落地经验"],
        "missing": ["缺少分布式事务实战经验"],
        "suggestions": ["补充核心指标的量化结果", "增加开源贡献的具体说明"],
        "level": "高级工程师",
        "focus": ["高并发架构", "支付系统"],
        "summary": "整体匹配度较高，核心能力与岗位要求对齐。",
        "breakdown": {"mustHave": 40, "responsibilities": 20, "evidenceQuality": 15, "bonus": 5},
        "evidence": [
            {"requirement": "Go", "resumeEvidence": "精通 Go 后端开发", "verdict": "matched"},
            {"requirement": "高并发", "resumeEvidence": "高并发系统设计经验", "verdict": "matched"},
            {"requirement": "分布式事务", "resumeEvidence": "简历中未提及分布式事务经验", "verdict": "missing"},
        ],
    }


def valid_diag(mode="text_only"):
    return {
        "overallScore": 75,
        "summary": "简历整体结构清晰，但缺少量化结果与版式层次。",
        "strengths": ["项目经历完整", "技术栈描述清晰"],
        "risks": ["缺少量化指标"],
        "diagnosis": [
            {
                "section": "项目经历", "score": 7,
                "evidence": ["负责支付平台架构设计"],
                "issues": ["缺少量化结果"], "suggestions": ["补充性能指标"],
                "rewrite": "主导支付平台架构设计，支撑日均百万级交易。",
            },
            {
                "section": "专业技能", "score": 6,
                "evidence": ["熟悉 Go 与 Redis"],
                "issues": [], "suggestions": ["补充深度说明"],
                "rewrite": "熟练掌握 Go 并发模型与 Redis 缓存治理。",
            },
        ],
        "layout": {"score": 0 if mode == "text_only" else 7, "summary": "版式判断", "issues": [], "suggestions": []},
        "mode": mode,
    }


async def test_matcher_happy_path():
    client = FakeClient([valid_match()])
    matcher = Matcher(Runtime(client))
    result = await matcher.match(MatchRequest(jd="Go 后端工程师", resume="精通 Go"))
    assert result.agent == MATCHER_ID
    assert result.score == 80
    assert len(result.traceIds) == 1


async def test_matcher_repairs_once():
    broken = dict(valid_match())
    broken["score"] = 10  # breakdown sums to 80, mismatch
    client = FakeClient([broken, valid_match()])
    matcher = Matcher(Runtime(client))
    result = await matcher.match(MatchRequest(jd="Go", resume="Go"))
    assert result.score == 80
    assert len(result.traceIds) == 2


async def test_matcher_requires_jd_and_resume():
    matcher = Matcher(Runtime(FakeClient([])))
    with pytest.raises(ValueError, match="required"):
        await matcher.match(MatchRequest(jd="  ", resume="x"))


async def test_diagnostician_text_only():
    client = FakeClient([valid_diag()])
    diagnostician = Diagnostician(Runtime(client))
    result = await diagnostician.diagnose(DiagRequest(content="后端工程师简历内容"))
    assert result.agent == DIAG_ID
    assert result.mode == "text_only"
    assert result.layout.score == 0


async def test_diagnostician_multimodal_truncates_images():
    client = FakeClient([valid_diag("multimodal")])
    diagnostician = Diagnostician(Runtime(client))
    request = DiagRequest(content="简历", images=["img1", "img2", "img3", "img4"])
    result = await diagnostician.diagnose(request)
    assert result.mode == "multimodal"
    assert len(client.calls) == 1
    assert client.calls[0][0] == "images"


async def test_diagnostician_requires_content():
    diagnostician = Diagnostician(Runtime(FakeClient([])))
    with pytest.raises(ValueError, match="required"):
        await diagnostician.diagnose(DiagRequest(content=" "))


def test_validate_match_breakdown_sum():
    result = MatchResult(**valid_match())
    assert validate_match(result) is None

    wrong = MatchResult(**valid_match())
    wrong.score = 79
    assert "equal the four breakdown dimensions" in str(validate_match(wrong))


def test_validate_match_phrase_ranges():
    result = MatchResult(**valid_match())
    result.matched = ["短", "aaaa", "bbbb"]
    assert "keyword fragment" in str(validate_match(result))

    result = MatchResult(**valid_match())
    result.missing = []
    assert "must contain" in str(validate_match(result))


def test_validate_match_evidence_verdict():
    result = MatchResult(**valid_match())
    result.evidence[0].verdict = "bogus"
    assert "verdict" in str(validate_match(result))


def test_validate_diag_mode_invariant():
    result = DiagResult(**valid_diag("text_only"))
    assert validate_diag(result, DiagRequest(content="x")) is None

    bad = DiagResult(**valid_diag("text_only"))
    bad.layout.score = 5
    assert "text_only" in str(validate_diag(bad, DiagRequest(content="x")))

    with_images = DiagResult(**valid_diag("multimodal"))
    with_images.layout.score = 0
    assert "multimodal" in str(validate_diag(with_images, DiagRequest(content="x", images=["i"])))


def test_validate_diag_min_sections_depends_on_length():
    result = DiagResult(**valid_diag())
    long_content = "长" * 1500
    assert "diagnosis must contain 4-9" in str(validate_diag(result, DiagRequest(content=long_content)))
