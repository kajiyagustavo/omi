import os
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("MEMORIA_UNIFICADA_URL", "http://karla-fake:8765")
os.environ.setdefault("MEMORIA_UNIFICADA_TOKEN", "tok-teste")
os.environ.setdefault("OMI_DOCS_KARLA", "true")

# Stub Firestore client so importing the module under test doesn't trigger ADC lookups.
_client = types.ModuleType("database._client")
_client.db = MagicMock()
_client.document_id_from_seed = lambda s: "id_" + str(abs(hash(s)) % 100000)
sys.modules["database._client"] = _client

# Stub opuslib + utils.other.storage — folders_karla imports database.conversations,
# which (via conversations_karla) reaches utils.other.storage at import time.
sys.modules.setdefault("opuslib", MagicMock())
_storage = types.ModuleType("utils.other.storage")
_storage.list_audio_chunks = lambda uid, cid: []
sys.modules["utils.other.storage"] = _storage

from database import folders_karla as fk  # noqa: E402
from database import omi_docs_karla as odk  # noqa: E402
from database import conversations as conversations_db  # noqa: E402
from database import folders as folders_mod  # noqa: E402


def _resp(json_data, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status.return_value = None
    return r


# ── get_folders ────────────────────────────────────────────────────────────


def test_get_folders_ordena_por_order_client_side():
    with patch.object(odk, "requests") as rq:
        docs = {
            "docs": [
                {"dados": {"id": "f-b", "name": "B", "order": 2}},
                {"dados": {"id": "f-a", "name": "A", "order": 0}},
                {"dados": {"id": "f-c", "name": "C", "order": 1}},
            ]
        }
        rq.request.return_value = _resp(docs)
        result = fk.get_folders("uid")
        assert [f["id"] for f in result] == ["f-a", "f-c", "f-b"]


# ── create_folder ─────────────────────────────────────────────────────────


def test_create_folder_escreve_colecao_doc_id_dados_corretos():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"docs": []}),  # get_folders (max_order)
            _resp({"ok": True}, status=201),  # upsert
        ]
        result = fk.create_folder("uid", "Trabalho", description="desc", color="#111", icon="🧩")

        assert result["name"] == "Trabalho"
        assert result["order"] == 1
        assert result["is_default"] is False
        assert result["is_system"] is False

        args, kwargs = rq.request.call_args_list[-1]
        assert args[0] == "POST"
        assert args[1].endswith("/u/tok-teste/omi-docs/folders")
        corpo = kwargs["json"]
        assert corpo["doc_id"] == result["id"]
        assert corpo["dados"]["name"] == "Trabalho"
        assert corpo["dados"]["color"] == "#111"


def test_create_folder_usa_maior_order_existente_mais_um():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"docs": [{"dados": {"id": "f-1", "order": 5}}, {"dados": {"id": "f-2", "order": 2}}]}),
            _resp({"ok": True}, status=201),
        ]
        result = fk.create_folder("uid", "Nova")
        assert result["order"] == 6


# ── delete_folder ──────────────────────────────────────────────────────────


def test_delete_folder_com_move_to_folder_id_move_conversas_depois_deleta():
    with patch.object(conversations_db, "get_conversations") as get_convs, patch.object(
        conversations_db, "update_conversation"
    ) as update_conv, patch.object(fk, "update_folder_conversation_count") as update_count, patch.object(
        odk, "deletar"
    ) as deletar:
        get_convs.return_value = [{"id": "c1"}, {"id": "c2"}]
        deletar.return_value = True

        # rastreia a ordem relativa das chamadas entre os 4 mocks
        manager = MagicMock()
        manager.attach_mock(get_convs, "get_conversations")
        manager.attach_mock(update_conv, "update_conversation")
        manager.attach_mock(update_count, "update_folder_conversation_count")
        manager.attach_mock(deletar, "deletar")

        result = fk.delete_folder("uid", "folder-1", move_to_folder_id="folder-2")

        assert result is True
        # conversas foram buscadas filtrando pela pasta a deletar
        _, get_kwargs = get_convs.call_args
        assert get_kwargs["folder_id"] == "folder-1"

        # cada conversa foi movida pra folder-2
        update_calls = update_conv.call_args_list
        assert len(update_calls) == 2
        for call in update_calls:
            args, _ = call
            assert args[0] == "uid"
            assert args[1] in ("c1", "c2")
            assert args[2] == {"folder_id": "folder-2"}

        # contagem da pasta destino foi recalculada
        update_count.assert_called_once_with("uid", "folder-2")

        # a pasta foi deletada por último (depois de mover as conversas)
        deletar.assert_called_once_with("folders", "folder-1")

        # ordem: get_conversations -> update_conversation x2 -> update_folder_conversation_count -> deletar
        call_names = [c[0] for c in manager.mock_calls]
        assert call_names == [
            "get_conversations",
            "update_conversation",
            "update_conversation",
            "update_folder_conversation_count",
            "deletar",
        ]


def test_delete_folder_sem_move_to_folder_id_usa_pasta_default():
    with patch.object(fk, "get_folders") as get_folders, patch.object(
        conversations_db, "get_conversations"
    ) as get_convs, patch.object(conversations_db, "update_conversation") as update_conv, patch.object(
        fk, "update_folder_conversation_count"
    ) as update_count, patch.object(
        odk, "deletar"
    ) as deletar:
        get_folders.return_value = [
            {"id": "folder-other", "is_default": True},
            {"id": "folder-1", "is_default": False},
        ]
        get_convs.return_value = [{"id": "c1"}]
        deletar.return_value = True

        result = fk.delete_folder("uid", "folder-1")

        assert result is True
        _, get_kwargs = get_convs.call_args
        assert get_kwargs["folder_id"] == "folder-1"
        update_conv.assert_called_once_with("uid", "c1", {"folder_id": "folder-other"})
        update_count.assert_called_once_with("uid", "folder-other")
        deletar.assert_called_once_with("folders", "folder-1")


def test_delete_folder_sem_pasta_default_apenas_deleta():
    with patch.object(fk, "get_folders") as get_folders, patch.object(
        conversations_db, "get_conversations"
    ) as get_convs, patch.object(conversations_db, "update_conversation") as update_conv, patch.object(
        fk, "update_folder_conversation_count"
    ) as update_count, patch.object(
        odk, "deletar"
    ) as deletar:
        get_folders.return_value = [{"id": "folder-1", "is_default": False}]
        deletar.return_value = True

        result = fk.delete_folder("uid", "folder-1")

        assert result is True
        get_convs.assert_not_called()
        update_conv.assert_not_called()
        update_count.assert_not_called()
        deletar.assert_called_once_with("folders", "folder-1")


# ── move_conversation_to_folder ──────────────────────────────────────────


def test_move_conversation_to_folder_patcha_conversa_nao_a_pasta():
    with patch.object(conversations_db, "get_conversations_by_id") as get_by_id, patch.object(
        conversations_db, "update_conversation"
    ) as update_conv, patch.object(fk, "update_folder_conversation_count") as update_count:
        get_by_id.return_value = [{"id": "c1", "folder_id": "folder-old"}]

        result = fk.move_conversation_to_folder("uid", "c1", "folder-new")

        assert result is True
        update_conv.assert_called_once_with("uid", "c1", {"folder_id": "folder-new"})
        assert update_count.call_args_list == [(("uid", "folder-old"),), (("uid", "folder-new"),)]


def test_move_conversation_to_folder_conversa_inexistente_devolve_false():
    with patch.object(conversations_db, "get_conversations_by_id") as get_by_id, patch.object(
        conversations_db, "update_conversation"
    ) as update_conv:
        get_by_id.return_value = []
        result = fk.move_conversation_to_folder("uid", "c-missing", "folder-new")
        assert result is False
        update_conv.assert_not_called()


# ── update_folder_conversation_count ─────────────────────────────────────


def test_update_folder_conversation_count_patch_colecao_folders():
    with patch.object(conversations_db, "get_conversations") as get_convs, patch.object(odk, "patch") as patch_fn:
        get_convs.return_value = [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]
        patch_fn.return_value = {"conversation_count": 3}

        count = fk.update_folder_conversation_count("uid", "folder-1")

        assert count == 3
        patch_fn.assert_called_once_with("folders", "folder-1", {"conversation_count": 3})


# ── footer rebind ─────────────────────────────────────────────────────────


def test_folders_footer_rebind():
    assert folders_mod.get_folders is fk.get_folders
    assert folders_mod.get_folder is fk.get_folder
    assert folders_mod.create_folder is fk.create_folder
    assert folders_mod.update_folder is fk.update_folder
    assert folders_mod.delete_folder is fk.delete_folder
    assert folders_mod.reorder_folders is fk.reorder_folders
    assert folders_mod.initialize_system_folders is fk.initialize_system_folders
    assert folders_mod.get_conversations_in_folder is fk.get_conversations_in_folder
    assert folders_mod.move_conversation_to_folder is fk.move_conversation_to_folder
    assert folders_mod.bulk_move_conversations_to_folder is fk.bulk_move_conversations_to_folder
    assert folders_mod.update_folder_conversation_count is fk.update_folder_conversation_count
    assert folders_mod.get_folder_by_category_mapping is fk.get_folder_by_category_mapping
