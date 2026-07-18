"""Testes dos shims CONFIG_KARLA de config sobre a MEMÓRIA UNIFICADA — arquivo
COMPARTILHADO entre as tasks do F4.5 que ainda faltam shim próprio (ex: Tasks
4-5). Cada módulo shimmed ganha sua própria seção com cabeçalho `# ── nome ──`.
Idioma de stub (sys.modules antes do import) copiado de test_users_karla.py —
mesmo padrão em todo o arquivo, novas seções devem reusar os fixtures daqui em
vez de duplicar o boilerplate de stub.
"""

import os
import sys
import types
from unittest.mock import MagicMock, patch

import requests as _real_requests


def _requests_mock():
    """A `requests` mock whose exception classes stay REAL, so the shim's
    `except requests.HTTPError` clauses don't blow up with 'catching classes
    that do not inherit from BaseException'."""
    m = MagicMock()
    m.HTTPError = _real_requests.HTTPError
    m.RequestException = _real_requests.RequestException
    return m


os.environ.setdefault("MEMORIA_UNIFICADA_URL", "http://karla-fake:8765")
os.environ.setdefault("MEMORIA_UNIFICADA_TOKEN", "tok-teste")
os.environ.setdefault("CONFIG_KARLA", "true")

# Stub Firestore client so importing the modules under test doesn't trigger ADC lookups.
_client = types.ModuleType("database._client")
_client.db = MagicMock()
_client.document_id_from_seed = lambda s: "id_" + str(abs(hash(s)) % 100000)
sys.modules["database._client"] = _client

# Stub opuslib + utils.other.storage — omi_docs_karla imports database.conversations_karla,
# which reaches utils.other.storage at import time.
sys.modules.setdefault("opuslib", MagicMock())
_storage = types.ModuleType("utils.other.storage")
_storage.list_audio_chunks = lambda uid, cid: []
sys.modules["utils.other.storage"] = _storage

# Import order matters: database.users participates in a pre-existing circular
# import with utils.subscription (users → utils.subscription → users). Importing
# database.users FIRST lets utils.subscription finish initializing before
# users_karla pulls get_default_basic_subscription from it — same order as the
# real app boot.
from database import users as users_mod  # noqa: E402
from database import notifications as notif_mod  # noqa: E402
from database import notifications_karla as nk  # noqa: E402
from database import omi_docs_karla as odk  # noqa: E402
from database.cache_manager import InMemoryCacheManager  # noqa: E402


def _fake_cache():
    """A real (non-Redis-backed) InMemoryCacheManager, bypassing the global
    `get_memory_cache()` singleton — its lazy init wires a Redis pub/sub
    subscription that isn't available in unit-test env (fails hard, not
    fail-open; a pre-existing house constraint, not something this shim
    controls). Same cache semantics (TTL, get_or_fetch, delete), no Redis."""
    return InMemoryCacheManager(max_memory_mb=10)


def _resp(json_data, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status.return_value = None
    return r


# ═══════════════════════════════════════════════════════════════════════════
# ── notifications_karla ──────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════


# ── save_token: upsert correto colecao/doc_id/dados ───────────────────────────


def test_save_token_upsert_colecao_doc_id_dados():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp(None, status=404),  # obter users (sem legado) — passo 1
            _resp(None, status=404),  # obter fcm_tokens/unknown_default — passo 2
            _resp(None, status=404),  # obter fcm_tokens/device-1 (novo) — passo 3
            _resp({"ok": True}, status=201),  # upsert fcm_tokens/device-1 — passo 3
            _resp(None, status=404),  # obter users dentro de _merge_user (time_zone)
            _resp({"ok": True}, status=201),  # upsert users (time_zone)
        ]
        nk.save_token("uid-1", {"device_key": "device-1", "fcm_token": "tok-abc", "time_zone": "America/Sao_Paulo"})

        upsert_call = rq.request.call_args_list[3]
        assert upsert_call[0][0] == "POST"
        assert upsert_call[0][1].endswith("/u/tok-teste/omi-docs/fcm_tokens")
        body = upsert_call[1]["json"]
        assert body["doc_id"] == "device-1"
        assert body["dados"]["device_key"] == "device-1"
        assert body["dados"]["token"] == "tok-abc"
        assert body["dados"]["time_zone"] == "America/Sao_Paulo"


def test_save_token_migra_legado_e_remove_campo():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"dados": {"fcm_token": "legacy-tok", "time_zone": "UTC"}}),  # obter users (tem legado) — passo 1
            _resp({"docs": []}),  # listar fcm_tokens (vazio → legado não existe ainda) — passo 1
            _resp({"ok": True}, status=201),  # upsert fcm_tokens/unknown_default (migração) — passo 1
            _resp({"ok": True}, status=201),  # upsert users (remove fcm_token) — passo 1
            # passo 2 pulado: device_key default já é unknown_default
            _resp(None, status=404),  # obter fcm_tokens/unknown_default (save) — passo 3
            _resp({"ok": True}, status=201),  # upsert fcm_tokens/unknown_default (save) — passo 3
            _resp({"dados": {}}),  # obter users dentro de _merge_user (time_zone)
            _resp({"ok": True}, status=201),  # patch/upsert users (time_zone)
        ]
        nk.save_token("uid-1", {"fcm_token": "new-tok", "time_zone": "UTC"})

        migrate_call = rq.request.call_args_list[2]
        assert migrate_call[0][0] == "POST"
        assert migrate_call[0][1].endswith("/u/tok-teste/omi-docs/fcm_tokens")
        assert migrate_call[1]["json"]["doc_id"] == "unknown_default"
        assert migrate_call[1]["json"]["dados"]["token"] == "legacy-tok"

        remove_call = rq.request.call_args_list[3]
        assert remove_call[0][0] == "POST"
        assert remove_call[0][1].endswith("/u/tok-teste/omi-docs/users")
        assert "fcm_token" not in remove_call[1]["json"]["dados"]


# ── get_all_tokens: subcoleção + legado ────────────────────────────────────────


def test_get_all_tokens_combina_subcolecao_e_legado():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"docs": [{"dados": {"device_key": "d1", "token": "t1"}}]}),  # listar fcm_tokens
            _resp({"dados": {"fcm_token": "legacy-t"}}),  # obter users
        ]
        tokens = nk.get_all_tokens("uid-1")
        assert tokens == ["t1", "legacy-t"]


def test_get_all_tokens_nao_duplica_legado_ja_presente():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"docs": [{"dados": {"device_key": "d1", "token": "dup"}}]}),
            _resp({"dados": {"fcm_token": "dup"}}),
        ]
        tokens = nk.get_all_tokens("uid-1")
        assert tokens == ["dup"]


# ── remove_invalid_token / remove_bulk_tokens: deletar por doc_id ─────────────


def test_remove_invalid_token_deleta_por_doc_id():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"docs": [{"dados": {"device_key": "d1", "token": "bad-tok"}}]}),  # listar filtrado
            _resp(None, status=204),  # deletar
        ]
        nk.remove_invalid_token("bad-tok")
        list_call = rq.request.call_args_list[0]
        assert list_call[0][0] == "GET"
        delete_call = rq.request.call_args_list[1]
        assert delete_call[0][0] == "DELETE"
        assert delete_call[0][1].endswith("/u/tok-teste/omi-docs/fcm_tokens/d1")


def test_remove_bulk_tokens_deleta_todos_os_matches():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp(
                {
                    "docs": [
                        {"dados": {"device_key": "d1", "token": "a"}},
                        {"dados": {"device_key": "d2", "token": "b"}},
                        {"dados": {"device_key": "d3", "token": "keep"}},
                    ]
                }
            ),  # listar (uma vez só)
            _resp(None, status=204),  # deletar d1
            _resp(None, status=204),  # deletar d2
        ]
        nk.remove_bulk_tokens(["a", "b"])
        delete_calls = [c for c in rq.request.call_args_list if c[0][0] == "DELETE"]
        assert len(delete_calls) == 2
        deleted_paths = {c[0][1] for c in delete_calls}
        assert deleted_paths == {
            "http://karla-fake:8765/u/tok-teste/omi-docs/fcm_tokens/d1",
            "http://karla-fake:8765/u/tok-teste/omi-docs/fcm_tokens/d2",
        }


def test_remove_bulk_tokens_lista_vazia_nao_faz_io():
    with patch.object(odk, "requests") as rq:
        nk.remove_bulk_tokens([])
        assert rq.request.call_count == 0


# ── prefs no doc users: GET+merge sem dot-keys ─────────────────────────────────


def test_daily_summary_hour_local_set_merge_sem_dot_keys():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"dados": {"language": "pt"}}),  # obter dentro de _merge_user
            _resp({"dados": {}}),  # patch
        ]
        assert nk.set_daily_summary_hour_local("uid-1", 9) is True
        patch_call = rq.request.call_args_list[-1]
        assert patch_call[0][0] == "PATCH"
        assert patch_call[0][1].endswith("/u/tok-teste/omi-docs/users/uid-1")
        body = patch_call[1]["json"]["dados_merge"]
        assert not any("." in key for key in body)
        assert body["daily_summary_hour_local"] == 9
        assert body["uid"] == "uid-1"


def test_daily_summary_hour_local_invalido_raise():
    import pytest

    with pytest.raises(ValueError):
        nk.set_daily_summary_hour_local("uid-1", 99)


def test_daily_summary_enabled_default_true_quando_ausente():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp(None, status=404)
        assert nk.get_daily_summary_enabled("uid-1") is True


def test_daily_summary_enabled_false_quando_setado():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"dados": {"daily_summary_enabled": False}})
        assert nk.get_daily_summary_enabled("uid-1") is False


# ── mentor_notification_frequency: cache 30s honrado ───────────────────────────


def test_mentor_frequency_cache_honrado():
    cache = _fake_cache()
    with patch.object(nk, "get_memory_cache", return_value=cache):
        with patch.object(odk, "requests") as rq:
            rq.request.return_value = _resp({"dados": {"mentor_notification_frequency": 4}})
            first = nk.get_mentor_notification_frequency("uid-cache")
            second = nk.get_mentor_notification_frequency("uid-cache")
            assert first == 4
            assert second == 4
            # segunda chamada veio do cache — só 1 request de rede
            assert rq.request.call_count == 1


def test_mentor_frequency_set_invalida_cache():
    cache = _fake_cache()
    with patch.object(nk, "get_memory_cache", return_value=cache):
        with patch.object(odk, "requests") as rq:
            rq.request.side_effect = [
                _resp({"dados": {"mentor_notification_frequency": 1}}),  # obter (fetch inicial p/ cache)
            ]
            assert nk.get_mentor_notification_frequency("uid-set") == 1

            rq.request.side_effect = [
                _resp({"dados": {}}),  # obter dentro de _merge_user
                _resp({"dados": {}}),  # patch
            ]
            assert nk.set_mentor_notification_frequency("uid-set", 5) is True

            rq.request.side_effect = [
                _resp({"dados": {"mentor_notification_frequency": 5}}),  # obter (cache foi invalidado)
            ]
            assert nk.get_mentor_notification_frequency("uid-set") == 5


def test_mentor_frequency_invalida_raise():
    import pytest

    with pytest.raises(ValueError):
        nk.set_mentor_notification_frequency("uid-1", 9)


# ── get_users_for_daily_summary: janela horária sobre docs users da Karla ─────


def test_get_users_for_daily_summary_aplica_janela_horaria():
    import asyncio

    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp(
                {
                    "docs": [
                        {
                            "dados": {
                                "uid": "uid-match",
                                "time_zone": "America/Sao_Paulo",
                                "daily_summary_hour_local": 22,
                            }
                        },
                        {
                            "dados": {
                                "uid": "uid-wrong-hour",
                                "time_zone": "America/Sao_Paulo",
                                "daily_summary_hour_local": 8,
                            }
                        },
                        {
                            "dados": {
                                "uid": "uid-disabled",
                                "time_zone": "America/Sao_Paulo",
                                "daily_summary_enabled": False,
                            }
                        },
                    ]
                }
            ),  # listar users
            _resp({"docs": [{"dados": {"device_key": "d1", "token": "tok-match"}}]}),  # tokens uid-match
        ]
        result = asyncio.run(nk.get_users_for_daily_summary(["America/Sao_Paulo"], 22))
        assert result == [("uid-match", ["tok-match"], "America/Sao_Paulo")]


def test_get_users_for_daily_summary_sem_token_e_pulado():
    import asyncio

    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp(
                {
                    "docs": [
                        {"dados": {"uid": "uid-no-token", "time_zone": "UTC", "daily_summary_hour_local": 22}},
                    ]
                }
            ),
            _resp({"docs": []}),  # sem tokens
        ]
        result = asyncio.run(nk.get_users_for_daily_summary(["UTC"], 22))
        assert result == []


def test_get_users_for_daily_summary_default_hour_quando_ausente():
    import asyncio

    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"docs": [{"dados": {"uid": "uid-default", "time_zone": "UTC"}}]}),  # sem hour_local → default 22
            _resp({"docs": [{"dados": {"device_key": "d1", "token": "tok"}}]}),
        ]
        result = asyncio.run(nk.get_users_for_daily_summary(["UTC"], nk.DEFAULT_DAILY_SUMMARY_HOUR_LOCAL))
        assert result == [("uid-default", ["tok"], "UTC")]


def test_get_users_for_daily_summary_timezones_vazio_retorna_vazio():
    import asyncio

    with patch.object(odk, "requests") as rq:
        result = asyncio.run(nk.get_users_for_daily_summary([], 22))
        assert result == []
        assert rq.request.call_count == 0


def test_get_users_id_in_timezones_shape():
    import asyncio

    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"docs": [{"dados": {"uid": "uid-1", "time_zone": "UTC"}}]}),
            _resp({"docs": [{"dados": {"device_key": "d1", "token": "tok"}}]}),
        ]
        result = asyncio.run(nk.get_users_id_in_timezones(["UTC"]))
        assert result == [("uid-1", ["tok"], "UTC")]


def test_get_users_token_in_timezones_flat_list():
    import asyncio

    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"docs": [{"dados": {"uid": "uid-1", "time_zone": "UTC"}}]}),
            _resp({"docs": [{"dados": {"device_key": "d1", "token": "tok"}}]}),
        ]
        result = asyncio.run(nk.get_users_token_in_timezones(["UTC"]))
        assert result == ["tok"]


# ── footer rebind ───────────────────────────────────────────────────────────


def test_notifications_footer_rebind():
    assert notif_mod.save_token is nk.save_token
    assert notif_mod.get_user_time_zone is nk.get_user_time_zone
    assert notif_mod.get_daily_summary_hour_local is nk.get_daily_summary_hour_local
    assert notif_mod.set_daily_summary_hour_local is nk.set_daily_summary_hour_local
    assert notif_mod.get_daily_summary_enabled is nk.get_daily_summary_enabled
    assert notif_mod.set_daily_summary_enabled is nk.set_daily_summary_enabled
    assert notif_mod.get_mentor_notification_frequency is nk.get_mentor_notification_frequency
    assert notif_mod.set_mentor_notification_frequency is nk.set_mentor_notification_frequency
    assert notif_mod.get_all_tokens is nk.get_all_tokens
    assert notif_mod.remove_invalid_token is nk.remove_invalid_token
    assert notif_mod.remove_bulk_tokens is nk.remove_bulk_tokens
    assert notif_mod.get_users_token_in_timezones is nk.get_users_token_in_timezones
    assert notif_mod.get_users_id_in_timezones is nk.get_users_id_in_timezones
    assert notif_mod.get_users_for_daily_summary is nk.get_users_for_daily_summary
    assert notif_mod._get_users_in_timezones is nk._get_users_in_timezones


# ── erro de rede → default do original (fail-open) ─────────────────────────────


def test_erro_de_rede_get_daily_summary_enabled_default_true():
    rq = _requests_mock()
    rq.request.side_effect = RuntimeError("boom")
    with patch.object(odk, "requests", new=rq):
        assert nk.get_daily_summary_enabled("uid") is True


def test_erro_de_rede_get_all_tokens_lista_vazia():
    rq = _requests_mock()
    rq.request.side_effect = RuntimeError("boom")
    with patch.object(odk, "requests", new=rq):
        assert nk.get_all_tokens("uid") == []


def test_erro_de_rede_mentor_frequency_default():
    cache = _fake_cache()
    rq = _requests_mock()
    rq.request.side_effect = RuntimeError("boom")
    with patch.object(nk, "get_memory_cache", return_value=cache):
        with patch.object(odk, "requests", new=rq):
            assert nk.get_mentor_notification_frequency("uid-err") == nk.DEFAULT_MENTOR_NOTIFICATION_FREQUENCY


def test_erro_de_rede_get_users_for_daily_summary_lista_vazia():
    import asyncio

    rq = _requests_mock()
    rq.request.side_effect = RuntimeError("boom")
    with patch.object(odk, "requests", new=rq):
        result = asyncio.run(nk.get_users_for_daily_summary(["UTC"], 22))
        assert result == []
