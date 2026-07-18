import os
import sys
import types
from unittest.mock import MagicMock, patch

os.environ.setdefault("ENCRYPTION_SECRET", "0" * 32)

# search.py faz `import typesense` no topo — stub pra não exigir o pacote real
# instalado / nem bater rede. client vira None quando TYPESENSE_API_KEY não
# está setado (mesmo guard de database/vector_db.py pro Pinecone).
os.environ.pop("TYPESENSE_API_KEY", None)
sys.modules.setdefault("typesense", MagicMock())

# Stub database.conversations e database.conversations_karla — search.py importa
# os dois módulos diretamente por nome (`import database.conversations as
# conversations_db`, `from database import conversations_karla`). Stubar evita
# puxar a cadeia pesada (google.cloud.firestore, opuslib, GCS, encryption).
_conversations_db = types.ModuleType("database.conversations")
_conversations_db.get_conversations_by_id = MagicMock(return_value=[])
sys.modules["database.conversations"] = _conversations_db

_conversations_karla = types.ModuleType("database.conversations_karla")
_conversations_karla.is_enabled = MagicMock(return_value=False)
_conversations_karla.buscar_conversas_ids = MagicMock(return_value=[])
sys.modules["database.conversations_karla"] = _conversations_karla

_database_pkg = types.ModuleType("database")
_database_pkg.conversations = _conversations_db
_database_pkg.conversations_karla = _conversations_karla
sys.modules.setdefault("database", _database_pkg)

from utils.conversations import search  # noqa: E402


def _conv(cid, is_locked=False):
    return {
        "id": cid,
        "uid": "uid-1",
        "is_locked": is_locked,
        "created_at": "2026-05-02T13:31:02+00:00",
        "started_at": "2026-05-02T13:30:00+00:00",
        "finished_at": "2026-05-02T13:35:00+00:00",
        "structured": {"title": f"Conversa {cid}", "overview": "overview", "emoji": "🗣️", "category": "other"},
    }


def test_fallback_returns_items_in_id_ranking_order_locked_excluded():
    with patch.object(search, "client", None), patch.object(
        search.conversations_karla, "is_enabled", return_value=True
    ), patch.object(
        search.conversations_karla, "buscar_conversas_ids", return_value=["c3", "c1", "c2"]
    ) as mock_ids, patch.object(
        search.conversations_db,
        "get_conversations_by_id",
        return_value=[_conv("c1"), _conv("c2", is_locked=True), _conv("c3")],
    ) as mock_docs:
        result = search.search_conversations(uid="uid-1", query="reunião", page=1, per_page=10)

    mock_ids.assert_called_once()
    mock_docs.assert_called_once()
    assert [item["id"] for item in result["items"]] == ["c3", "c1"]
    assert result["current_page"] == 1
    assert result["per_page"] == 10
    assert result["total_pages"] == 1
    # shape check — fields the web conversation cards render
    for item in result["items"]:
        assert "structured" in item and "title" in item["structured"]
        assert "created_at" in item


def test_fallback_pagination_page_2_slices_correctly():
    all_ids = [f"c{i}" for i in range(1, 21)]  # c1..c20

    def fake_get_by_id(uid, ids):
        return [_conv(cid) for cid in ids]

    with patch.object(search, "client", None), patch.object(
        search.conversations_karla, "is_enabled", return_value=True
    ), patch.object(search.conversations_karla, "buscar_conversas_ids", return_value=all_ids) as mock_ids, patch.object(
        search.conversations_db, "get_conversations_by_id", side_effect=fake_get_by_id
    ):
        result = search.search_conversations(uid="uid-1", query="q", page=2, per_page=5)

    # page=2, per_page=5 → window = min(2*5, 100) = 10
    assert mock_ids.call_args.kwargs["k"] == 10
    assert [item["id"] for item in result["items"]] == ["c6", "c7", "c8", "c9", "c10"]
    assert result["current_page"] == 2


def test_karla_disabled_and_no_typesense_returns_empty_no_raise():
    with patch.object(search, "client", None), patch.object(
        search.conversations_karla, "is_enabled", return_value=False
    ):
        result = search.search_conversations(uid="uid-1", query="q", page=1, per_page=10)

    assert result == {"items": [], "total_pages": 1, "current_page": 1, "per_page": 10}


def test_typesense_present_fallback_not_called():
    fake_client = MagicMock()
    fake_client.collections.__getitem__.return_value.documents.search.return_value = {
        "hits": [
            {
                "document": {
                    "id": "t1",
                    "is_locked": False,
                    "created_at": 1750000000,
                    "started_at": 1750000000,
                    "finished_at": 1750000100,
                    "structured": {"title": "Typesense hit"},
                }
            }
        ]
    }

    with patch.object(search, "client", fake_client), patch.object(
        search.conversations_karla, "buscar_conversas_ids"
    ) as mock_ids:
        result = search.search_conversations(uid="uid-1", query="q", page=1, per_page=10)

    mock_ids.assert_not_called()
    assert [item["id"] for item in result["items"]] == ["t1"]
