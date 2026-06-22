import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault(
    "ENCRYPTION_SECRET",
    "omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv",
)

# Stub Firestore client so importing models.memories doesn't trigger ADC lookups.
_client = types.ModuleType("database._client")
_client.db = MagicMock()
_client.document_id_from_seed = lambda s: "id_" + str(abs(hash(s)) % 100000)
sys.modules["database._client"] = _client

from models.memories import Memory, MemoryDB, MemorySource, MemoryCategory  # noqa: E402
from models.conversation_enums import CategoryEnum  # noqa: E402


def _mem():
    return Memory(content="x", category=MemoryCategory.interesting)


def test_from_memory_defaults_source_and_topic_none():
    db = MemoryDB.from_memory(_mem(), "uid", "conv1", False)
    assert db.source is None
    assert db.topic is None


def test_from_memory_sets_source_and_topic():
    db = MemoryDB.from_memory(
        _mem(),
        "uid",
        "conv1",
        False,
        source=MemorySource.whatsapp,
        topic=CategoryEnum.work,
    )
    assert db.source == MemorySource.whatsapp
    assert db.topic == CategoryEnum.work


def test_memorysource_values():
    assert {s.value for s in MemorySource} == {
        "whatsapp",
        "recording",
        "plaud",
        "email",
        "manual",
        "other",
    }


def test_memorydb_serializes_source_topic():
    db = MemoryDB.from_memory(
        _mem(),
        "uid",
        "conv1",
        False,
        source=MemorySource.plaud,
        topic=CategoryEnum.spiritual,
    )
    d = db.dict()
    assert d["source"] == MemorySource.plaud
    assert d["topic"] == CategoryEnum.spiritual


def test_manual_memory_source_is_manual():
    db = MemoryDB.from_memory(_mem(), "uid", None, True, source=MemorySource.manual)
    assert db.source == MemorySource.manual
