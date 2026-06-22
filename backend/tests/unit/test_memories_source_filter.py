"""Unit test for GET /v3/memories ?source= in-memory filter.

Stubs the heavy import chain (GCS/OpenAI/Firestore) so routers.memories imports without
creds. models.* stay real so MemoryDB validation behaves like production.
"""

import os
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault(
    "ENCRYPTION_SECRET",
    "omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv",
)
# LLM clients are constructed at import time in utils/llm/clients.py; give dummy keys so
# the import succeeds. They are never invoked by the in-memory filter under test.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
os.environ.setdefault("OPENROUTER_API_KEY", "sk-test-dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-dummy")


def _stub_module(name):
    mod = types.ModuleType(name)
    mod.__getattr__ = lambda attr: MagicMock()
    sys.modules[name] = mod
    return mod


# Firestore client used by database._client / various db modules.
_client = _stub_module("database._client")
_client.db = MagicMock()
_client.document_id_from_seed = lambda s: "id_" + str(abs(hash(s)) % 100000)

# GCS client is instantiated at import time in utils/other/storage.py — stub it so the
# import chain (routers.memories -> utils.apps -> database.conversations -> storage) is safe.
import google.cloud.storage as _gcs  # noqa: E402

_gcs.Client = MagicMock()

# Stub db submodules that routers.memories pulls in (auto-mock any attribute).
for name in [
    "database.memories",
    "database.vector_db",
    "database.redis_db",
]:
    _stub_module(name)
# get_memories is patched per-test; default it so import-time binding works.
sys.modules["database.memories"].get_memories = MagicMock()

from routers import memories as memories_router  # noqa: E402


def _doc(mid, source):
    return {
        "id": mid,
        "uid": "u",
        "content": f"c{mid}",
        "category": "interesting",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "source": source,
    }


def test_filter_by_source_whatsapp():
    docs = [_doc("1", "whatsapp"), _doc("2", "manual"), _doc("3", "whatsapp")]
    with patch.object(memories_router.memories_db, "get_memories", return_value=docs):
        result = memories_router.get_memories(limit=100, offset=1, source="whatsapp", uid="u")
    assert {m.id for m in result} == {"1", "3"}


def test_no_filter_returns_all():
    docs = [_doc("1", "whatsapp"), _doc("2", "manual")]
    with patch.object(memories_router.memories_db, "get_memories", return_value=docs):
        result = memories_router.get_memories(limit=100, offset=1, source=None, uid="u")
    assert {m.id for m in result} == {"1", "2"}
