import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("MEMORIA_UNIFICADA_URL", "http://karla-fake:8765")
os.environ.setdefault("MEMORIA_UNIFICADA_TOKEN", "tok-teste")

# Stub Firestore client so importing the script under test doesn't trigger ADC
# lookups (mirrors tests/unit/test_export_f41.py idiom).
_client = types.ModuleType("database._client")
_client.db = MagicMock()
_client.document_id_from_seed = lambda s: "id_" + str(abs(hash(s)) % 100000)
sys.modules["database._client"] = _client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "memoria"))

from export_config_f45 import normalizar_doc  # noqa: E402


def test_normalizar_doc_users_injeta_uid():
    dados = {"email": "a@b.com", "language": "pt"}
    out = normalizar_doc("users", "uid-123", dados, uid="uid-123")
    assert out["uid"] == "uid-123"
    assert out["email"] == "a@b.com"


def test_normalizar_doc_users_nao_sobrescreve_uid_existente():
    dados = {"uid": "uid-original", "email": "a@b.com"}
    out = normalizar_doc("users", "uid-123", dados, uid="uid-123")
    assert out["uid"] == "uid-original"


def test_normalizar_doc_people_injeta_id():
    dados = {"name": "Fulano"}
    out = normalizar_doc("people", "person-1", dados)
    assert out["id"] == "person-1"


def test_normalizar_doc_task_integrations_injeta_app_key():
    dados = {"connected": True}
    out = normalizar_doc("task_integrations", "todoist", dados)
    assert out["app_key"] == "todoist"


def test_normalizar_doc_fcm_tokens_injeta_device_key():
    dados = {"token": "fcm-abc"}
    out = normalizar_doc("fcm_tokens", "device-9", dados)
    assert out["device_key"] == "device-9"


def test_normalizar_doc_hourly_usage_meetings_devkeys_mcpkeys_injetam_id():
    for colecao, doc_id in (
        ("hourly_usage", "2026-07-14-10"),
        ("meetings", "meeting-1"),
        ("dev_api_keys", "key-1"),
        ("mcp_api_keys", "key-2"),
    ):
        out = normalizar_doc(colecao, doc_id, {"foo": "bar"})
        assert out["id"] == doc_id, colecao


def test_normalizar_doc_integrations_passthrough_sem_injecao():
    dados = {"connected": True, "updated_at": "2026-07-14T10:00:00+00:00"}
    out = normalizar_doc("integrations", "readwise", dados)
    assert out == dados
    assert "app_key" not in out


def test_normalizar_doc_llm_usage_passthrough_verbatim():
    dados = {"date": "2026-07-14", "chat.gpt-4.call_count": 3}
    out = normalizar_doc("llm_usage", "2026-07-14", dados)
    assert out == dados


def test_normalizar_doc_apps_passthrough_verbatim():
    dados = {"id": "app-1", "name": "My App", "approved": True}
    out = normalizar_doc("apps", "app-1", dados)
    assert out == dados
