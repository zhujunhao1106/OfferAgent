"""P1 knowledge module tests (tokenizer / parser / BM25 / retriever)."""
import hashlib
from pathlib import Path

from app.knowledge import (
    Entry,
    Focus,
    InterviewRetriever,
    KnowledgeQuery,
    bounded_material,
    entry_id,
    interview_search_text,
    load,
    new_index,
    parse_markdown,
    plain_text,
    tokenize,
    truncate_runes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _entry(id: str, question: str, body: str = "", title: str = "T") -> Entry:
    return Entry(id=id, source="t.md", title=title, question=question, excerpt="", body=body)


# --- tokenizer ---

def test_tokenize_han_unigram_bigram():
    assert tokenize("你好") == ["你", "你好", "好"]
    assert tokenize("你好世界") == ["你", "你好", "好", "好世", "世", "世界", "界"]


def test_tokenize_words():
    assert tokenize("abc") == ["abc"]
    assert tokenize("a") == []           # single latin letter dropped
    assert tokenize("1") == ["1"]        # single digit kept
    assert tokenize("ab1") == ["ab1"]
    assert tokenize("a-b") == ["a-b"]
    assert tokenize("RAG") == ["rag"]    # lowercased


def test_tokenize_mixed():
    assert tokenize("RAG 检索") == ["rag", "检", "检索", "索"]


# --- plain_text ---

def test_plain_text():
    md = "# 标题\n\n**粗体** 和 [链接](http://x) 文本\n\n```\n代码块\n```\n\n尾随"
    # fence markers are removed but their content is kept (Go plainText behavior)
    assert plain_text(md) == "标题 粗体 和 链接 文本 代码块 尾随"


def test_truncate_runes():
    assert truncate_runes("abcdef", 3) == "abc..."
    assert truncate_runes("ab", 5) == "ab"
    assert truncate_runes("abc", 0) == "abc"


# --- entry id / parser ---

def test_entry_id():
    expected = "kb_" + hashlib.sha256("a.md\x00q\x000".encode()).hexdigest()[:20]
    assert entry_id("a.md", "q", 0) == expected


SAMPLE = "# 面试题库\n\n## Q：什么是RAG？\n\nRAG是检索增强生成。\n\n## Q：什么是向量？\n\n向量是数值数组。\n"


def test_parse_markdown():
    entries = parse_markdown("t.md", SAMPLE)
    assert len(entries) == 2
    assert entries[0].question == "什么是RAG？"
    assert entries[0].title == "面试题库"
    assert entries[0].source == "t.md"
    assert entries[0].id.startswith("kb_")
    assert entries[1].question == "什么是向量？"


# --- BM25 search ---

def test_search_deterministic_and_sorted():
    idx = new_index([
        _entry("kb_a", "什么是RAG检索", "RAG 检索增强生成"),
        _entry("kb_b", "什么是向量数据库", "向量存储"),
        _entry("kb_c", "RAG评估方法", "评估"),
    ])
    first = idx.search("RAG", 10)
    second = idx.search("RAG", 10)
    assert [e.id for e in first] == [e.id for e in second]  # deterministic
    scores = [e.score for e in first]
    assert scores == sorted(scores, reverse=True)


def test_single_han_query_no_prefilter():
    idx = new_index([
        _entry("kb_a", "你好世界"),
        _entry("kb_b", "无关内容"),
    ])
    results = idx.search("你", 10)
    assert [e.id for e in results] == ["kb_a"]


def test_search_empty():
    idx = new_index([_entry("kb_a", "你好")])
    assert idx.search("", 10) == []
    assert idx.search("!!!", 10) == []


# --- retriever ---

def test_bounded_material():
    assert bounded_material("abcdef", 3) == "abc"
    assert bounded_material("ab", 3) == "ab"
    assert bounded_material("  x  ", 10) == "x"


def test_interview_search_text_focus_fallback():
    q = KnowledgeQuery(focus=Focus.PROJECTS)
    assert interview_search_text(q) == "简历 项目 深挖 个人职责 量化指标 技术取舍"
    q2 = KnowledgeQuery(focus=Focus.KNOWLEDGE, objective="RAG")
    assert interview_search_text(q2) == "RAG"  # objective present suppresses jd/resume + fallback


def test_retriever_content_format():
    idx = new_index([Entry(id="kb_1", source="s.md", title="主题", question="问题", excerpt="参考", body="参考")])
    docs = InterviewRetriever(idx, 8).retrieve(KnowledgeQuery(question="问题"))
    assert len(docs) == 1
    assert docs[0].content == "知识主题：主题\n问题：问题\n参考内容：参考\n来源：s.md"


# --- corpus load ---

def test_load_knowledge_count():
    index = load(str(REPO_ROOT / "knowledge"))
    assert index.len() == 486
