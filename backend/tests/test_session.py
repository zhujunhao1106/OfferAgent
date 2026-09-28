"""P3 session store + memory tests."""
import pytest

from app.session import Memory, Message, Store


def test_store_create_and_get():
    store = Store()
    session = store.create()
    assert len(session.id) == 32
    assert store.get(session.id) is not None


def test_store_duplicate_id_rejected():
    store = Store()
    store.create("fixed")
    with pytest.raises(ValueError):
        store.create("fixed")


def test_store_values_and_ids_and_delete():
    store = Store()
    store.create("a")
    store.create("b")
    store.set_value("a", "k", "v")
    assert store.get("a").values["k"] == "v"
    assert store.ids() == ["a", "b"]
    assert store.delete("a") is True
    assert store.delete("missing") is False
    assert store.get("a") is None


def test_memory_trims_to_most_recent():
    memory = Memory(2)
    memory.add("s", "user", "1")
    memory.add("s", "user", "2")
    memory.add("s", "user", "3")
    assert [m.content for m in memory.messages("s")] == ["2", "3"]


def test_memory_messages_is_defensive_copy():
    memory = Memory(10)
    memory.add("s", "user", "x")
    messages = memory.messages("s")
    messages[0].content = "mutated"
    assert memory.messages("s")[0].content == "x"


def test_memory_replace_and_clear():
    memory = Memory(10)
    memory.replace("s", [Message("user", "a"), Message("assistant", "b")])
    assert len(memory.messages("s")) == 2
    assert memory.session_ids() == ["s"]
    memory.clear("s")
    assert memory.messages("s") == []
    assert memory.session_ids() == []
