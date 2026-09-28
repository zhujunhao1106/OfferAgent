"""P4d knowledge privacy sanitization tests (mirrors Go sources_test.go)."""
from app.interview.sources import (
    knowledge_question_label,
    knowledge_reference_text,
    merge_knowledge_documents,
    public_evidence_quote,
    public_generated_text,
    public_knowledge_question,
)
from app.interview.types import EvidenceRef, KnowledgeDocument, SourceIndex, SourceKind


def knowledge_ref(quote, anchor="a"):
    return EvidenceRef(sourceId="s", kind=SourceKind.KNOWLEDGE, anchorId=anchor, locator="l", quote=quote)


def test_public_generated_text_rejects_verbatim_reference_copy():
    ref = knowledge_ref("问题：ColBERT 如何工作？\n参考答案：ColBERT 会对每个 query token 与文档 token 做 MaxSim，再聚合局部匹配分数。")
    assert public_generated_text("需要补充 MaxSim 的适用边界。", [ref]) != ""
    assert public_generated_text("每个 query token 与文档 token 做 MaxSim，再聚合局部匹配分数。", [ref]) == ""


def test_public_generated_text_rejects_reference_marker():
    ref = knowledge_ref("问题：Q\n参考内容：secret")
    assert public_generated_text("包含参考内容：泄露", [ref]) == ""


def test_public_knowledge_question_extracts_question_only():
    content = "知识主题：RAG 重排\n问题：ColBERT 为什么能保留 token 级交互？\n参考内容：分别编码 query 和 document token。\n来源：rag.md"
    assert public_knowledge_question(content) == "问题：ColBERT 为什么能保留 token 级交互？"


def test_public_knowledge_question_falls_back():
    assert public_knowledge_question("参考内容：只有答案") == "知识题"


def test_knowledge_reference_text_extracts_answer_between_markers():
    content = "问题：Q\n参考答案：分别编码 query 和 document token。\n来源：rag.md"
    assert knowledge_reference_text(content) == "分别编码 query 和 document token。"


def test_knowledge_question_label_strips_question_prefix():
    assert knowledge_question_label("问题：ColBERT 为什么？\n参考内容：x") == "ColBERT 为什么？"


def test_public_evidence_quote_projects_knowledge():
    knowledge = knowledge_ref("问题：Q\n参考内容：secret")
    assert public_evidence_quote(knowledge) == "问题：Q"
    resume = EvidenceRef(sourceId="s", kind=SourceKind.RESUME, anchorId="a", locator="l", quote="负责服务开发")
    assert public_evidence_quote(resume) == "负责服务开发"


def test_merge_knowledge_documents_keeps_block_atomic():
    index = SourceIndex()
    document = KnowledgeDocument(
        id="kb_colbert", title="ColBERT late interaction",
        content="知识主题：RAG 重排\n问题：ColBERT 为什么能保留 token 级交互？\n参考内容：分别编码 query 和 document token，再用 MaxSim 聚合。\n来源：rag.md",
    )
    refs = merge_knowledge_documents(index, [document], 0)
    assert len(refs) == 1
    assert refs[0].sourceId == "knowledge:kb_colbert"
    assert refs[0].anchorId == "knowledge:kb_colbert:block"
    anchor = index.anchors["knowledge:kb_colbert:block"]
    assert "问题：ColBERT" in anchor.text
    assert "参考内容：分别编码" in anchor.text


def test_merge_knowledge_documents_dedupes_identical_content():
    index = SourceIndex()
    document = KnowledgeDocument(id="kb", title="T", content="问题：Q\n参考内容：A")
    merge_knowledge_documents(index, [document], 0)
    refs = merge_knowledge_documents(index, [document], 0)
    assert len(refs) == 1
    assert refs[0].anchorId == "knowledge:kb:block"
    assert "knowledge:kb:2" not in index.anchors


def test_merge_knowledge_documents_reuses_id_with_suffix_for_new_content():
    index = SourceIndex()
    merge_knowledge_documents(index, [KnowledgeDocument(id="kb", title="T", content="问题：Q\n参考内容：A")], 0)
    refs = merge_knowledge_documents(index, [KnowledgeDocument(id="kb", title="T", content="问题：Q\n参考内容：B")], 0)
    assert refs[0].sourceId == "knowledge:kb:2"
    assert refs[0].anchorId == "knowledge:kb:2:block"


def test_merge_knowledge_documents_respects_limit():
    index = SourceIndex()
    documents = [KnowledgeDocument(id=f"kb{i}", title="T", content=f"问题：Q{i}\n参考内容：A") for i in range(5)]
    refs = merge_knowledge_documents(index, documents, 3)
    assert len(refs) == 3
