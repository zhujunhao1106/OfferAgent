"""P4b interview domain type tests."""
from app.interview.types import (
    Assessment,
    ClaimVerdict,
    InterviewConfig,
    MaterialInput,
    MaterialsInput,
    SourceDocument,
    SourceIndex,
    SourceKind,
    StartRequest,
)
from app.llm.schema import schema_for


def test_material_input_accepts_plain_string():
    value = MaterialInput.model_validate("some jd text")
    assert value.text == "some jd text"
    assert value.name == ""


def test_material_input_accepts_object():
    value = MaterialInput.model_validate({"name": "jd.md", "text": "body"})
    assert value.name == "jd.md"
    assert value.text == "body"


def test_material_input_rejects_unknown_fields():
    try:
        MaterialInput.model_validate({"name": "x", "text": "y", "bogus": 1})
    except Exception as exc:
        assert "bogus" in str(exc)
    else:
        raise AssertionError("unknown field was accepted")


def test_materials_input_double_form():
    materials = MaterialsInput.model_validate({"jd": "jd text", "resume": {"name": "r", "text": "resume text"}})
    assert materials.jd.text == "jd text"
    assert materials.resume.name == "r"
    assert materials.resume.text == "resume text"
    assert MaterialsInput.model_validate({}).jd is None


def test_source_document_content_excluded_from_dump():
    document = SourceDocument(id="jd", kind=SourceKind.JD, content="private body")
    assert "content" not in document.model_dump()
    assert document.content == "private body"


def test_source_index_dump_excludes_document_content():
    index = SourceIndex(documents={"jd": SourceDocument(id="jd", kind=SourceKind.JD, content="secret")})
    dumped = index.model_dump()
    assert "content" not in dumped["documents"]["jd"]


def test_start_request_enums_and_defaults():
    request = StartRequest.model_validate({
        "action": "start",
        "clientSessionId": "c1",
        "config": {"focus": "projects", "questionCount": 4},
        "materials": {"jd": "senior go engineer"},
    })
    assert request.config.focus.value == "projects"
    assert request.config.questionCount == 4
    assert request.config.difficulty.value == "medium"
    assert request.materials.jd.text == "senior go engineer"


def test_assessment_schema_maps_str_enums_to_string():
    schema = schema_for(Assessment)
    claim_checks = schema["properties"]["claimChecks"]
    verdict = claim_checks["items"]["properties"]["verdict"]
    assert verdict == {"type": "string"}
    evidence_kind = schema["properties"]["evidenceRefs"]["items"]["properties"]["kind"]
    assert evidence_kind == {"type": "string"}


def test_claim_verdict_round_trips():
    assessment = Assessment(
        correctness=4, depth=4, specificity=4, ownership=4, metrics=4, tradeoffs=4,
        claimChecks=[{"claim": "x", "verdict": ClaimVerdict.CONTRADICTED.value}],
    )
    assert assessment.claimChecks[0].verdict == ClaimVerdict.CONTRADICTED
    assert assessment.model_dump()["claimChecks"][0]["verdict"] == "contradicted"
