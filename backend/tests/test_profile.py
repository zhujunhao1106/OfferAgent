"""P4b deterministic profile extraction + grounding validation tests."""
import pytest

from app.profile import (
    DeterministicExtractor,
    build_agent_request,
    validate,
)
from app.profile.extractor import NoMaterialsError, _technologies_in
from app.profile.types import (
    DocumentInput,
    EvidenceRef,
    Fact,
    Input,
    SourceKind,
)
from app.profile.validate import UngroundedFactError


def has_fact_value(facts, value):
    return any(fact.value.lower() == value.lower() for fact in facts)


def test_extracts_grounded_chinese_profile():
    input = Input(
        jd=DocumentInput(
            source_id="jd-v1",
            text=(
                "职位：高级 Go 后端工程师\n"
                "任职要求\n"
                "- 5 年以上 Go 服务端经验，必须熟悉 MySQL 与 Redis\n"
                "- 掌握 Kubernetes，具备高并发和低延迟系统设计经验\n"
                "加分项\n"
                "- 有 LLM Agent 或 RAG 落地经验优先\n"
                "岗位职责\n"
                "- 负责交易平台架构设计、核心服务交付与稳定性治理"
            ),
        ),
        resume=DocumentInput(
            source_id="resume-v3",
            text=(
                "张三｜后端工程师\n"
                "项目经历\n"
                "### OfferPilot | 2025.01-2026.06\n"
                "- 主导 Go Agent Harness 与 SQLite 状态层设计，负责服务上线\n"
                "- 将 P95 延迟从 800ms 降低至 220ms，支持 10k QPS\n"
                "- 使用 Go、Redis、Docker 和 Kubernetes\n"
                "技能\n"
                "Go、TypeScript、PostgreSQL、OpenTelemetry"
            ),
        ),
    )

    extractor = DeterministicExtractor()
    first = extractor.extract(input)
    second = extractor.extract(input)
    assert first == second

    assert first.job.title is not None
    assert first.job.title.value == "高级 Go 后端工程师"
    assert len(first.job.must_have) == 2
    assert len(first.job.nice_to_have) == 1
    assert len(first.job.responsibilities) == 1
    assert len(first.job.seniority_signals) > 0
    assert len(first.job.business_constraints) > 0
    assert has_fact_value(first.job.technical_topics, "Go")
    assert has_fact_value(first.job.technical_topics, "Kubernetes")

    assert len(first.candidate.projects) == 1
    project = first.candidate.projects[0]
    assert project.name.value == "OfferPilot"
    assert len(project.responsibilities) == 1
    assert len(project.metrics) == 1
    assert has_fact_value(first.candidate.skills, "OpenTelemetry")
    assert has_fact_value(project.technologies, "Kubernetes")

    request = build_agent_request(input)
    validate(request, first)


def test_extracts_english_sections():
    result = DeterministicExtractor().extract(Input(
        jd=DocumentInput(text=(
            "Position: Staff Platform Engineer\n"
            "Requirements\n"
            "- 7 years of experience with Go and Kubernetes\n"
            "Nice to have\n"
            "- RAG production experience preferred\n"
            "Responsibilities\n"
            "- Build low latency platform services and maintain observability"
        )),
        resume=DocumentInput(text=(
            "Platform engineer\n"
            "Projects\n"
            "Control Plane | 2024-2026\n"
            "- Led the Go service design and delivered the Kubernetes migration\n"
            "- Reduced P99 latency to 95ms and handled 20k RPS\n"
            "Skills\n"
            "Go, Kubernetes, Prometheus"
        )),
    ))
    assert result.job.title is not None
    assert result.job.title.value == "Staff Platform Engineer"
    assert len(result.job.must_have) == 1
    assert len(result.job.nice_to_have) == 1
    assert len(result.job.responsibilities) == 1
    assert len(result.candidate.projects) == 1
    assert len(result.candidate.projects[0].metrics) == 1
    assert has_fact_value(result.candidate.skills, "Prometheus")


def test_extracts_inline_sections_without_substring_skills():
    result = DeterministicExtractor().extract(Input(
        jd=DocumentInput(text=(
            "Position: Backend Engineer\n"
            "Requirements: Experience with MongoDB and Go\n"
            "Responsibilities: Build reliable APIs"
        )),
        resume=DocumentInput(text=(
            "Platform engineer\n"
            "Projects: Atlas\n"
            "- Built MongoDB migration tooling\n"
            "Skills: Go, MongoDB, Prometheus"
        )),
    ))
    assert len(result.job.must_have) == 1
    assert result.job.must_have[0].value == "Experience with MongoDB and Go"
    assert len(result.job.responsibilities) == 1
    assert len(result.candidate.projects) == 1
    assert has_fact_value(result.candidate.skills, "Go")
    assert has_fact_value(result.candidate.skills, "MongoDB")
    assert has_fact_value(result.candidate.skills, "Prometheus")
    assert not has_fact_value(_technologies_in("MongoDB migration"), "Go")
    assert "go" in [t.lower() for t in _technologies_in("熟悉Go语言并用于服务开发")]


def test_splits_inline_project_name_and_responsibility():
    result = DeterministicExtractor().extract(Input(
        resume=DocumentInput(text="项目 OfferPilot：我负责 Go Agent Harness 的架构设计与实现。"),
    ))
    assert len(result.candidate.projects) == 1
    assert result.candidate.projects[0].name.value == "OfferPilot"
    project = result.candidate.projects[0]
    assert len(project.responsibilities) == 1
    assert project.responsibilities[0].value == "我负责 Go Agent Harness 的架构设计与实现。"
    validate(build_agent_request(Input(resume=DocumentInput(text="项目 OfferPilot：我负责 Go Agent Harness 的架构设计与实现。"))), result)


def test_empty_input_raises_no_materials():
    with pytest.raises(NoMaterialsError):
        DeterministicExtractor().extract(Input())


def test_build_agent_request_rejects_duplicate_source_id():
    input = Input(
        jd=DocumentInput(source_id="dup", text="职位：Go 工程师"),
        resume=DocumentInput(source_id="dup", text="Go 工程师"),
    )
    with pytest.raises(ValueError, match="duplicate source ID"):
        build_agent_request(input)


def _anchor(request, index):
    return request.anchors[index]


def _evidence(anchor):
    return EvidenceRef(
        source_id=anchor.source_id, kind=anchor.kind, anchor_id=anchor.id,
        locator=anchor.locator, quote=anchor.text,
    )


def test_validate_rejects_ungrounded_fact_value():
    input = Input(jd=DocumentInput(text="职位：Go 工程师\n任职要求\n- 必须熟悉 Go"))
    request = build_agent_request(input)
    anchor = request.anchors[2]
    assert anchor.text == "- 必须熟悉 Go"
    fact = Fact(id="must-1", value="必须精通 Rust", evidence_refs=[_evidence(anchor)])
    from app.profile.types import JobProfile, Profile
    result = Profile(job=JobProfile(must_have=[fact]))
    with pytest.raises(UngroundedFactError, match="not extractive"):
        validate(request, result)


def test_validate_rejects_job_fact_backed_by_resume():
    input = Input(
        jd=DocumentInput(text="职位：Go 工程师"),
        resume=DocumentInput(text="Go 工程师\n- 负责 Go 服务开发"),
    )
    request = build_agent_request(input)
    anchor = request.anchors[-1]
    fact = Fact(id="must-1", value="Go 服务开发", evidence_refs=[_evidence(anchor)])
    from app.profile.types import JobProfile, Profile
    result = Profile(job=JobProfile(must_have=[fact]))
    with pytest.raises(UngroundedFactError, match="want"):
        validate(request, result)


def test_validate_rejects_unknown_anchor():
    input = Input(jd=DocumentInput(text="职位：Go 工程师\n- 必须熟悉 Go"))
    request = build_agent_request(input)
    fact = Fact(id="must-1", value="必须熟悉 Go", evidence_refs=[
        EvidenceRef(source_id="jd", kind=SourceKind.JD, anchor_id="jd:999", locator="line:2", quote="必须熟悉 Go"),
    ])
    from app.profile.types import JobProfile, Profile
    result = Profile(job=JobProfile(must_have=[fact]))
    with pytest.raises(UngroundedFactError, match="unknown evidence anchor"):
        validate(request, result)
