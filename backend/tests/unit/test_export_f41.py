import os
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("MEMORIA_UNIFICADA_URL", "http://karla-fake:8765")
os.environ.setdefault("MEMORIA_UNIFICADA_TOKEN", "tok-teste")

# Stub Firestore client + friends so importing the script under test doesn't
# trigger ADC lookups (mirrors tests/unit/test_conversas_karla.py idiom).
_client = types.ModuleType("database._client")
_client.db = MagicMock()
_client.document_id_from_seed = lambda s: "id_" + str(abs(hash(s)) % 100000)
sys.modules["database._client"] = _client

sys.modules.setdefault("opuslib", MagicMock())
_storage = types.ModuleType("utils.other.storage")
_storage.list_audio_chunks = lambda uid, cid: []
sys.modules["utils.other.storage"] = _storage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "memoria"))

from export_conversas_f41 import normalizar_conversa  # noqa: E402


def test_normalizar_conversa_datetime_para_iso_deep():
    conv = {
        "id": "conv-1",
        "created_at": datetime(2026, 5, 2, 13, 31, 2, tzinfo=timezone.utc),
        "structured": {
            "title": "Reunião",
            "sub": {"started_at": datetime(2026, 5, 2, 13, 30, 0, tzinfo=timezone.utc)},
        },
        "transcript_segments": [{"id": "s1", "text": "olá"}],
    }
    out = normalizar_conversa(conv)
    assert out["created_at"] == "2026-05-02T13:31:02+00:00"
    assert out["structured"]["sub"]["started_at"] == "2026-05-02T13:30:00+00:00"


def test_normalizar_conversa_segments_str_levanta_com_id():
    conv = {"id": "conv-quebrada", "transcript_segments": "ainda-criptografado"}
    with pytest.raises(ValueError) as exc_info:
        normalizar_conversa(conv)
    assert "conv-quebrada" in str(exc_info.value)


def test_normalizar_conversa_forca_compressed_false():
    conv = {
        "id": "conv-2",
        "transcript_segments": [{"id": "s1"}],
        "transcript_segments_compressed": True,
    }
    out = normalizar_conversa(conv)
    assert out["transcript_segments_compressed"] is False


def test_normalizar_conversa_dict_passthrough_sem_datetime():
    conv = {"id": "conv-3", "status": "completed", "discarded": False, "photos": [{"id": "p1", "url": "x"}]}
    out = normalizar_conversa(conv)
    assert out["status"] == "completed"
    assert out["discarded"] is False
    assert out["photos"] == [{"id": "p1", "url": "x"}]
    assert out["id"] == "conv-3"
