import os
import sys
import types
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

os.environ.setdefault("MEMORIA_UNIFICADA_URL", "http://karla-fake:8765")
os.environ.setdefault("MEMORIA_UNIFICADA_TOKEN", "tok-teste")
os.environ.setdefault("CONVERSAS_KARLA", "true")

# Stub Firestore client so importing the module under test doesn't trigger ADC lookups.
_client = types.ModuleType("database._client")
_client.db = MagicMock()
_client.document_id_from_seed = lambda s: "id_" + str(abs(hash(s)) % 100000)
sys.modules["database._client"] = _client

# Stub opuslib + utils.other.storage — the shim imports list_audio_chunks at module top
# (for create_audio_files_from_chunks), and storage constructs a GCS client at import
# time (needs ADC). Stub the whole module so the import chain stays credential-free.
sys.modules.setdefault("opuslib", MagicMock())
_storage = types.ModuleType("utils.other.storage")
_storage.list_audio_chunks = lambda uid, cid: []
sys.modules["utils.other.storage"] = _storage

from database import conversations_karla as ck  # noqa: E402


def _resp(json_data, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status.return_value = None
    return r


CONV = {
    "id": "conv-1",
    "uid": "uid-x",
    "status": "completed",
    "discarded": False,
    "created_at": "2026-05-02T13:31:02+00:00",
    "started_at": "2026-05-02T13:30:00+00:00",
    "finished_at": "2026-05-02T13:35:00+00:00",
    "transcript_segments": [{"id": "s1", "text": "olá", "start": 0.0, "end": 1.0}],
    "structured": {
        "title": "Reunião",
        "action_items": [
            {"description": "ligar pro fornecedor", "completed": False},
            {"description": "feito", "completed": True},
        ],
    },
    "photos": [],
}


def test_upsert_serializa_datetime_e_remove_photos_audio():
    with patch.object(ck, "requests") as rq:
        rq.request.return_value = _resp({"id": "conv-1"}, status=201)
        data = {
            "id": "conv-1",
            "created_at": datetime(2026, 5, 2, 13, 31, 2, tzinfo=timezone.utc),
            "photos": [{"id": "p1"}],
            "audio_base64_url": "http://x/audio",
            "structured": {"title": "t"},
        }
        ck.upsert_conversation("uid", data)
        args, kwargs = rq.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/u/tok-teste/conversas-omi")
        corpo = kwargs["json"]
        assert "photos" not in corpo
        assert "audio_base64_url" not in corpo
        # datetime deve virar string ISO com timezone
        assert corpo["created_at"] == "2026-05-02T13:31:02+00:00"


def test_get_conversations_monta_params():
    with patch.object(ck, "requests") as rq:
        rq.request.return_value = _resp({"conversas": [CONV]})
        out = ck.get_conversations(
            "uid",
            limit=50,
            offset=10,
            include_discarded=False,
            statuses=["completed", "processing"],
            start_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 5, 3, tzinfo=timezone.utc),
            categories=["business"],
            folder_id="f1",
            starred=True,
        )
        args, kwargs = rq.request.call_args
        assert args[0] == "GET"
        params = kwargs["params"]
        assert params["status_in"] == "completed,processing"
        assert params["limite"] == 50 and params["offset"] == 10
        assert params["incluir_descartadas"] == "false"
        assert params["categorias"] == "business"
        assert params["folder_id"] == "f1"
        assert params["starred"] == "true"
        assert params["criado_de"] == "2026-05-01T00:00:00+00:00"
        assert params["criado_ate"] == "2026-05-03T00:00:00+00:00"
    assert out[0]["id"] == "conv-1"


def test_get_conversation_404_retorna_none():
    import requests as real_requests

    with patch.object(ck, "requests") as rq:
        rq.HTTPError = real_requests.HTTPError
        err = real_requests.HTTPError()
        err.response = MagicMock(status_code=404)
        r = MagicMock()
        r.raise_for_status.side_effect = err
        rq.request.return_value = r
        assert ck.get_conversation("uid", "nope") is None


def test_update_conversation_segments_manda_compressed_false_e_engole_404():
    import requests as real_requests

    with patch.object(ck, "requests") as rq:
        rq.HTTPError = real_requests.HTTPError
        rq.request.return_value = _resp(CONV)
        ck.update_conversation_segments("uid", "conv-1", [{"id": "s1", "text": "novo"}])
        args, kwargs = rq.request.call_args
        assert args[0] == "PATCH"
        merge = kwargs["json"]["dados_merge"]
        assert merge["transcript_segments_compressed"] is False
        assert merge["transcript_segments"] == [{"id": "s1", "text": "novo"}]

    # 404 → silencioso
    with patch.object(ck, "requests") as rq:
        rq.HTTPError = real_requests.HTTPError
        err = real_requests.HTTPError()
        err.response = MagicMock(status_code=404)
        r = MagicMock()
        r.raise_for_status.side_effect = err
        rq.request.return_value = r
        # não pode levantar
        ck.update_conversation_segments("uid", "conv-1", [{"id": "s1"}])


def test_get_action_items_flatten():
    with patch.object(ck, "requests") as rq:
        rq.request.return_value = _resp({"conversas": [CONV]})
        items = ck.get_action_items("uid", include_completed=False)
    # só o não-completo deve sobrar (include_completed=False)
    descs = [i["description"] for i in items]
    assert "ligar pro fornecedor" in descs
    assert "feito" not in descs
    assert items[0]["conversation_id"] == "conv-1"


def test_erro_de_rede_retorno_neutro_sem_raise():
    with patch.object(ck, "requests") as rq:
        rq.request.side_effect = Exception("boom")
        assert ck.get_conversations("uid") == []
        assert ck.get_conversation("uid", "x") is None
        assert ck.get_conversations_count("uid") == 0


def test_footer_rebind():
    """Com CONVERSAS_KARLA=true, conversations.py rebinda pros símbolos do shim."""
    import subprocess

    code = (
        "import os, sys, types\n"
        "from unittest.mock import MagicMock\n"
        "os.environ['CONVERSAS_KARLA'] = 'true'\n"
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
        "from database import conversations, conversations_karla\n"
        "assert conversations.get_conversation is conversations_karla.get_conversation\n"
        "assert conversations.upsert_conversation is conversations_karla.upsert_conversation\n"
        "assert conversations.update_conversation_segments is conversations_karla.update_conversation_segments\n"
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
