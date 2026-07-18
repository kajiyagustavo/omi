import os
import sys
import types
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

os.environ.setdefault("MEMORIA_UNIFICADA_URL", "http://karla-fake:8765")
os.environ.setdefault("MEMORIA_UNIFICADA_TOKEN", "tok-teste")
os.environ.setdefault("OMI_DOCS_KARLA", "true")

# Stub Firestore client so importing the module under test doesn't trigger ADC lookups.
_client = types.ModuleType("database._client")
_client.db = MagicMock()
_client.document_id_from_seed = lambda s: "id_" + str(abs(hash(s)) % 100000)
sys.modules["database._client"] = _client

# Stub opuslib + utils.other.storage — chat_karla imports database.conversations,
# which (via conversations_karla) reaches utils.other.storage at import time.
sys.modules.setdefault("opuslib", MagicMock())
_storage = types.ModuleType("utils.other.storage")
_storage.list_audio_chunks = lambda uid, cid: []
sys.modules["utils.other.storage"] = _storage

from database import chat_karla as ck  # noqa: E402
from database import omi_docs_karla as odk  # noqa: E402


def _resp(json_data, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status.return_value = None
    return r


def test_add_message_upsert_com_created_at_iso():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"ok": True}, status=201)
        data = {
            "id": "msg-1",
            "text": "olá",
            "created_at": datetime(2026, 5, 2, 13, 31, 2, tzinfo=timezone.utc),
            "memories": [{"id": "should-be-stripped"}],
        }
        ck.add_message("uid", data)
        args, kwargs = rq.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/u/tok-teste/omi-docs/messages")
        corpo = kwargs["json"]
        assert corpo["doc_id"] == "msg-1"
        assert corpo["criado_em"] == "2026-05-02T13:31:02+00:00"
        # created_at dentro de dados também serializado
        assert corpo["dados"]["created_at"] == "2026-05-02T13:31:02+00:00"
        # campo front-facing `memories` removido antes de gravar
        assert "memories" not in corpo["dados"]


def test_get_messages_filtro_app_id_none():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": []})
        ck.get_messages("uid", limit=20, offset=0, app_id=None)
        args, kwargs = rq.request.call_args
        assert args[0] == "GET"
        params = kwargs["params"]
        import json as _json

        filtro = _json.loads(params["filtro"])
        # app-scoped, app_id None → containment {"plugin_id": null}
        assert filtro == {"plugin_id": None}
        assert params["ordem"] == "desc"
        assert params["limite"] == 20 and params["offset"] == 0


def test_get_messages_filtro_app_id_set():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": []})
        ck.get_messages("uid", app_id="app-xyz")
        args, kwargs = rq.request.call_args
        import json as _json

        filtro = _json.loads(kwargs["params"]["filtro"])
        assert filtro == {"plugin_id": "app-xyz"}


def test_get_messages_filtro_chat_session_id_ignora_app_id():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": []})
        ck.get_messages("uid", app_id="app-xyz", chat_session_id="sess-1")
        args, kwargs = rq.request.call_args
        import json as _json

        filtro = _json.loads(kwargs["params"]["filtro"])
        # session-scoped: filtra SÓ por chat_session_id (sem plugin_id)
        assert filtro == {"chat_session_id": "sess-1"}


def test_get_messages_pula_reportadas():
    docs = {
        "docs": [
            {"dados": {"id": "m1", "text": "a", "reported": False}},
            {"dados": {"id": "m2", "text": "b", "reported": True}},
            {"dados": {"id": "m3", "text": "c"}},
        ]
    }
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp(docs)
        out = ck.get_messages("uid")
    ids = [m["id"] for m in out]
    assert ids == ["m1", "m3"]


def test_add_message_to_chat_session_get_append_patch_array_inteira():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"dados": {"id": "sess-1", "message_ids": ["a", "b"]}}),  # GET (obter)
            _resp({"dados": {"id": "sess-1", "message_ids": ["a", "b", "c"]}}),  # PATCH
        ]
        ck.add_message_to_chat_session("uid", "sess-1", "c")

        assert rq.request.call_count == 2
        get_args, _ = rq.request.call_args_list[0]
        assert get_args[0] == "GET"

        patch_args, patch_kwargs = rq.request.call_args_list[1]
        assert patch_args[0] == "PATCH"
        merge = patch_kwargs["json"]["dados_merge"]
        # array inteira reenviada (merge raso) — nunca patch parcial
        assert merge == {"message_ids": ["a", "b", "c"]}


def test_add_message_to_chat_session_idempotente():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"dados": {"id": "sess-1", "message_ids": ["a", "b"]}}),
            _resp({"dados": {}}),
        ]
        ck.add_message_to_chat_session("uid", "sess-1", "b")  # já presente
        patch_args, patch_kwargs = rq.request.call_args_list[1]
        merge = patch_kwargs["json"]["dados_merge"]
        assert merge == {"message_ids": ["a", "b"]}


def test_delete_chat_session_cascade_deleta_mensagens_por_sessao():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"dados": {"id": "sess-1"}}),  # obter sessão (existe)
            _resp({"deletados": 3}),  # deletar_em_lote messages
            _resp(None, status=204),  # deletar sessão
        ]
        ck.delete_chat_session("uid", "sess-1", cascade_messages=True)

        # a chamada de deleção em lote das mensagens deve filtrar por chat_session_id
        del_msgs_args, del_msgs_kwargs = rq.request.call_args_list[1]
        assert del_msgs_args[0] == "DELETE"
        assert del_msgs_args[1].endswith("/u/tok-teste/omi-docs/messages")
        import json as _json

        filtro = _json.loads(del_msgs_kwargs["params"]["filtro"])
        assert filtro == {"chat_session_id": "sess-1"}


def test_delete_chat_session_cascade_sessao_ausente_retorna_false():
    with patch.object(odk, "requests") as rq:
        import requests as real_requests

        rq.HTTPError = real_requests.HTTPError
        err = real_requests.HTTPError()
        err.response = MagicMock(status_code=404)
        r = MagicMock()
        r.raise_for_status.side_effect = err
        rq.request.return_value = r
        assert ck.delete_chat_session("uid", "nope", cascade_messages=True) is False


def test_get_message_devolve_tuple_message_id():
    dados = {"id": "msg-9", "text": "oi", "created_at": "2026-05-02T13:31:02+00:00", "sender": "ai", "type": "text"}
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"dados": dados})
        result = ck.get_message("uid", "msg-9")
    assert result is not None
    message, doc_id = result
    assert doc_id == "msg-9"
    assert message.id == "msg-9"
    assert message.text == "oi"


def test_update_message_rating_patch_e_neutro_em_404():
    # sucesso
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"dados": {"id": "m1", "rating": 1}})
        assert ck.update_message_rating("uid", "m1", 1) is True
        args, kwargs = rq.request.call_args
        assert args[0] == "PATCH"
        assert kwargs["json"]["dados_merge"] == {"rating": 1}

    # 404 → False
    with patch.object(odk, "requests") as rq:
        import requests as real_requests

        rq.HTTPError = real_requests.HTTPError
        err = real_requests.HTTPError()
        err.response = MagicMock(status_code=404)
        r = MagicMock()
        r.raise_for_status.side_effect = err
        rq.request.return_value = r
        assert ck.update_message_rating("uid", "nope", 1) is False


def test_get_chat_files_com_ids_usa_param_ids():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": [{"dados": {"id": "f1"}}]})
        out = ck.get_chat_files("uid", ["f1", "f2"])
        args, kwargs = rq.request.call_args
        assert kwargs["params"]["ids"] == "f1,f2"
    assert out[0]["id"] == "f1"


def test_save_message_auto_acquire_e_atualiza_sessao():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"docs": [{"dados": {"id": "sess-1", "plugin_id": None}}]}),  # acquire: listar sessões
            _resp({"ok": True}, status=201),  # upsert message
            _resp({"dados": {"id": "sess-1", "message_count": 2}}),  # obter sessão
            _resp({"dados": {"id": "sess-1"}}),  # patch sessão
        ]
        out = ck.save_message("uid", "hello", "human", app_id=None)
        assert "id" in out and "created_at" in out
        # patch da sessão incrementa message_count (2 -> 3) e seta preview
        patch_args, patch_kwargs = rq.request.call_args_list[3]
        assert patch_args[0] == "PATCH"
        merge = patch_kwargs["json"]["dados_merge"]
        assert merge["message_count"] == 3
        assert merge["preview"] == "hello"


def test_erro_de_rede_retorno_neutro_sem_raise():
    import requests as real_requests

    with patch.object(odk, "requests") as rq:
        rq.HTTPError = real_requests.HTTPError
        rq.request.side_effect = Exception("boom")
        assert ck.get_messages("uid") == []
        assert ck.get_message("uid", "x") is None
        assert ck.get_message_count("uid") == 0
        assert ck.get_chat_files("uid") == []
        assert ck.delete_messages("uid") == 0


def test_footer_rebind():
    """Com OMI_DOCS_KARLA=true, chat.py rebinda pros símbolos do shim."""
    import subprocess

    code = (
        "import os, sys, types\n"
        "from unittest.mock import MagicMock\n"
        "os.environ['OMI_DOCS_KARLA'] = 'true'\n"
        "os.environ.setdefault('MEMORIA_UNIFICADA_URL', 'http://k:1')\n"
        "os.environ.setdefault('MEMORIA_UNIFICADA_TOKEN', 't')\n"
        "_c = types.ModuleType('database._client')\n"
        "_c.db = MagicMock()\n"
        "_c.document_id_from_seed = lambda s: 'x'\n"
        "sys.modules['database._client'] = _c\n"
        "sys.modules['opuslib'] = MagicMock()\n"
        "_s = types.ModuleType('utils.other.storage')\n"
        "_s.list_audio_chunks = lambda uid, cid: []\n"
        "sys.modules['utils.other.storage'] = _s\n"
        "from database import chat, chat_karla\n"
        "assert chat.get_messages is chat_karla.get_messages\n"
        "assert chat.add_message is chat_karla.add_message\n"
        "assert chat.add_message_to_chat_session is chat_karla.add_message_to_chat_session\n"
        "assert chat.delete_chat_session is chat_karla.delete_chat_session\n"
        "assert chat.save_message is chat_karla.save_message\n"
        "print('OK')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
