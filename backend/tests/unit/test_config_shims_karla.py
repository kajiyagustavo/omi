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
from database import apps as apps_mod  # noqa: E402
from database import apps_karla as ak  # noqa: E402
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


# ═══════════════════════════════════════════════════════════════════════════
# ── apps_karla (shim PARCIAL) ────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════


def _app_doc(app_id, **overrides):
    doc = {
        'id': app_id,
        'uid': 'uid-owner',
        'private': False,
        'approved': True,
        'category': 'productivity',
        'capabilities': ['chat'],
    }
    doc.update(overrides)
    return doc


# ── get_app_by_id_db: cache Redis primeiro, depois Karla, depois popula cache ──


def test_get_app_by_id_usa_cache_primeiro():
    with patch.object(ak, "get_app_cache_by_id", return_value=_app_doc("app-1")) as cache_get:
        with patch.object(ak, "set_app_cache_by_id") as cache_set:
            with patch.object(odk, "requests") as rq:
                result = ak.get_app_by_id_db("app-1")
                assert result == _app_doc("app-1")
                cache_get.assert_called_once_with("app-1")
                # cache hit — não vai na Karla nem popula o cache de novo.
                assert rq.request.call_count == 0
                cache_set.assert_not_called()


def test_get_app_by_id_cache_miss_busca_karla_e_seta_cache():
    with patch.object(ak, "get_app_cache_by_id", return_value=None):
        with patch.object(ak, "set_app_cache_by_id") as cache_set:
            with patch.object(odk, "requests") as rq:
                rq.request.return_value = _resp({"dados": _app_doc("app-2")})
                result = ak.get_app_by_id_db("app-2")
                assert result == _app_doc("app-2")

                get_call = rq.request.call_args_list[0]
                assert get_call[0][0] == "GET"
                assert get_call[0][1].endswith("/u/tok-teste/omi-docs/apps/app-2")

                cache_set.assert_called_once_with("app-2", _app_doc("app-2"))


def test_get_app_by_id_ausente_devolve_none_sem_setar_cache():
    with patch.object(ak, "get_app_cache_by_id", return_value=None):
        with patch.object(ak, "set_app_cache_by_id") as cache_set:
            with patch.object(odk, "requests") as rq:
                rq.request.return_value = _resp(None, status=404)
                assert ak.get_app_by_id_db("missing") is None
                cache_set.assert_not_called()


def test_get_app_by_id_erro_de_rede_devolve_none_fail_open():
    with patch.object(ak, "get_app_cache_by_id", return_value=None):
        with patch.object(ak, "set_app_cache_by_id") as cache_set:
            rq = _requests_mock()
            rq.request.side_effect = RuntimeError("boom")
            with patch.object(odk, "requests", new=rq):
                assert ak.get_app_by_id_db("app-err") is None
                cache_set.assert_not_called()


# ── search_apps_db: filtra client-side (default approved+public) ──────────────


def test_search_apps_default_filtra_approved_public():
    apps = [
        _app_doc("public-approved", private=False, approved=True),
        _app_doc("public-unapproved", private=False, approved=False),
        _app_doc("private-app", private=True, approved=True),
    ]
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": [{"dados": a} for a in apps]})
        result = ak.search_apps_db(uid="uid-owner")
        assert [a['id'] for a in result] == ["public-approved"]

        list_call = rq.request.call_args_list[0]
        assert list_call[0][0] == "GET"
        assert list_call[0][1].endswith("/u/tok-teste/omi-docs/apps")


def test_search_apps_my_apps_filtra_por_uid_e_category():
    apps = [
        _app_doc("mine-productivity", uid="uid-owner", category="productivity"),
        _app_doc("mine-other-cat", uid="uid-owner", category="social"),
        _app_doc("not-mine", uid="someone-else", category="productivity"),
    ]
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": [{"dados": a} for a in apps]})
        result = ak.search_apps_db(uid="uid-owner", category="productivity", my_apps=True)
        assert [a['id'] for a in result] == ["mine-productivity"]


def test_search_apps_capability_filtra_array_contains():
    apps = [
        _app_doc("has-cap", capabilities=["chat", "memories"]),
        _app_doc("no-cap", capabilities=["chat"]),
    ]
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": [{"dados": a} for a in apps]})
        result = ak.search_apps_db(uid="uid-owner", capability="memories")
        assert [a['id'] for a in result] == ["has-cap"]


def test_search_apps_installed_apps_sem_enabled_ids_lista_vazia():
    with patch.object(odk, "requests") as rq:
        result = ak.search_apps_db(uid="uid-owner", installed_apps=True, enabled_app_ids=[])
        assert result == []
        assert rq.request.call_count == 0


def test_search_apps_installed_apps_filtra_por_ids():
    apps = [
        _app_doc("enabled-1"),
        _app_doc("enabled-2"),
        _app_doc("not-enabled"),
    ]
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": [{"dados": a} for a in apps]})
        result = ak.search_apps_db(uid="uid-owner", installed_apps=True, enabled_app_ids=["enabled-1", "enabled-2"])
        assert {a['id'] for a in result} == {"enabled-1", "enabled-2"}


def test_search_apps_installed_apps_mais_de_30_nao_re_filtra_apps_do_usuario():
    """Mirror do original (database/apps.py:140-154): com > 30 enabled_app_ids,
    category/capability só se aplicam à base approved+public — os apps
    instalados do PRÓPRIO usuário são somados sem re-filtrar por category/
    capability. Um app instalado do usuário com category diferente do filtro
    deve ser MANTIDO no resultado (não descartado como no bug do shim)."""
    enabled_ids = [f"public-{i}" for i in range(31)] + ["mine-other-category"]
    apps = [_app_doc(app_id, category="productivity") for app_id in enabled_ids if app_id.startswith("public-")]
    # App instalado do próprio usuário, categoria DIFERENTE do filtro aplicado.
    apps.append(
        _app_doc(
            "mine-other-category",
            uid="uid-owner",
            approved=False,
            private=True,
            category="social",
        )
    )
    # App aprovado/público mas de categoria diferente — deve ser excluído (é
    # da base, sujeita ao filtro de category).
    apps.append(_app_doc("public-wrong-category", category="social"))

    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": [{"dados": a} for a in apps]})
        result = ak.search_apps_db(
            uid="uid-owner",
            category="productivity",
            installed_apps=True,
            enabled_app_ids=enabled_ids,
        )
        result_ids = {a['id'] for a in result}

        # Apps públicos aprovados batendo a category permanecem.
        assert {f"public-{i}" for i in range(31)} <= result_ids
        # App do usuário com category diferente é MANTIDO (paridade com o
        # original — não é re-filtrado por category/capability).
        assert "mine-other-category" in result_ids
        # App público de categoria errada (da base) continua excluído.
        assert "public-wrong-category" not in result_ids


# ── get_public_approved_apps_db / get_private_apps_db: filtro client-side ─────


def test_get_public_approved_apps_filtra_approved_e_public():
    apps = [
        _app_doc("a1", approved=True, private=False),
        _app_doc("a2", approved=True, private=True),
        _app_doc("a3", approved=False, private=False),
    ]
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": [{"dados": a} for a in apps]})
        result = ak.get_public_approved_apps_db()
        assert [a['id'] for a in result] == ["a1"]


def test_get_private_apps_filtra_uid_e_private():
    apps = [
        _app_doc("p1", uid="uid-owner", private=True),
        _app_doc("p2", uid="uid-owner", private=False),
        _app_doc("p3", uid="someone-else", private=True),
    ]
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": [{"dados": a} for a in apps]})
        result = ak.get_private_apps_db("uid-owner")
        assert [a['id'] for a in result] == ["p1"]


def test_listagens_erro_de_rede_devolvem_lista_vazia_fail_open():
    rq = _requests_mock()
    rq.request.side_effect = RuntimeError("boom")
    with patch.object(odk, "requests", new=rq):
        assert ak.get_public_approved_apps_db() == []
        assert ak.get_private_apps_db("uid-owner") == []
        assert ak.search_apps_db(uid="uid-owner") == []


# ── CRUD: upsert/patch com id em dados + invalidação de cache ─────────────────


def test_add_app_to_db_upsert_com_id_em_dados_e_invalida_cache():
    with patch.object(ak, "delete_app_cache_by_id") as cache_del:
        with patch.object(odk, "requests") as rq:
            rq.request.return_value = _resp({"ok": True}, status=201)
            ak.add_app_to_db(_app_doc("new-app"))

            upsert_call = rq.request.call_args_list[0]
            assert upsert_call[0][0] == "POST"
            assert upsert_call[0][1].endswith("/u/tok-teste/omi-docs/apps")
            body = upsert_call[1]["json"]
            assert body["doc_id"] == "new-app"
            assert body["dados"]["id"] == "new-app"

            cache_del.assert_called_once_with("new-app")


def test_update_app_in_db_patch_raso_e_invalida_cache():
    with patch.object(ak, "delete_app_cache_by_id") as cache_del:
        with patch.object(odk, "requests") as rq:
            rq.request.return_value = _resp({"dados": {}})
            ak.update_app_in_db({'id': 'app-1', 'name': 'Novo nome'})

            patch_call = rq.request.call_args_list[0]
            assert patch_call[0][0] == "PATCH"
            assert patch_call[0][1].endswith("/u/tok-teste/omi-docs/apps/app-1")
            body = patch_call[1]["json"]["dados_merge"]
            assert body["name"] == "Novo nome"

            cache_del.assert_called_once_with("app-1")


def test_delete_app_from_db_deleta_e_invalida_cache():
    with patch.object(ak, "delete_app_cache_by_id") as cache_del:
        with patch.object(odk, "requests") as rq:
            rq.request.return_value = _resp(None, status=204)
            ak.delete_app_from_db("app-1")

            delete_call = rq.request.call_args_list[0]
            assert delete_call[0][0] == "DELETE"
            assert delete_call[0][1].endswith("/u/tok-teste/omi-docs/apps/app-1")

            cache_del.assert_called_once_with("app-1")


def test_set_app_popular_db_patch_is_popular():
    with patch.object(ak, "delete_app_cache_by_id") as cache_del:
        with patch.object(odk, "requests") as rq:
            rq.request.return_value = _resp({"dados": {}})
            ak.set_app_popular_db("app-1", True)

            patch_call = rq.request.call_args_list[0]
            assert patch_call[0][0] == "PATCH"
            assert patch_call[1]["json"]["dados_merge"] == {"is_popular": True}
            cache_del.assert_called_once_with("app-1")


def test_update_app_visibility_patch_simples_quando_nao_private_suffix():
    with patch.object(ak, "delete_app_cache_by_id") as cache_del:
        with patch.object(odk, "requests") as rq:
            rq.request.return_value = _resp({"dados": {}})
            ak.update_app_visibility_in_db("app-1", False)

            patch_call = rq.request.call_args_list[0]
            assert patch_call[0][0] == "PATCH"
            assert patch_call[1]["json"]["dados_merge"] == {"private": False}
            cache_del.assert_called_once_with("app-1")


def test_update_app_visibility_recria_doc_quando_publica_app_privado():
    app = _app_doc("base-private", private=True)
    with patch.object(ak, "delete_app_cache_by_id") as cache_del:
        with patch.object(odk, "requests") as rq:
            rq.request.side_effect = [
                _resp({"dados": app}),  # obter app-private
                _resp(None, status=204),  # deletar app-private
                _resp({"ok": True}, status=201),  # upsert novo id
            ]
            ak.update_app_visibility_in_db("base-private", False)

            delete_call = rq.request.call_args_list[1]
            assert delete_call[0][0] == "DELETE"
            assert delete_call[0][1].endswith("/u/tok-teste/omi-docs/apps/base-private")

            upsert_call = rq.request.call_args_list[2]
            assert upsert_call[0][0] == "POST"
            new_id = upsert_call[1]["json"]["doc_id"]
            assert new_id.startswith("base-")
            assert new_id != "base-private"
            assert upsert_call[1]["json"]["dados"]["private"] is False
            assert upsert_call[1]["json"]["dados"]["id"] == new_id

            assert cache_del.call_count == 2
            deleted_ids = {c.args[0] for c in cache_del.call_args_list}
            assert deleted_ids == {"base-private", new_id}


def test_update_app_visibility_noop_quando_doc_ausente():
    with patch.object(ak, "delete_app_cache_by_id") as cache_del:
        with patch.object(odk, "requests") as rq:
            rq.request.return_value = _resp(None, status=404)
            ak.update_app_visibility_in_db("gone-private", False)
            # só o GET aconteceu — nem DELETE nem POST.
            assert rq.request.call_count == 1
            cache_del.assert_not_called()


# ── footer rebind: só as funções shimadas são rebindadas ──────────────────────


def test_apps_footer_rebind_parcial():
    assert apps_mod.get_app_by_id_db is ak.get_app_by_id_db
    assert apps_mod.get_public_approved_apps_db is ak.get_public_approved_apps_db
    assert apps_mod.get_private_apps_db is ak.get_private_apps_db
    assert apps_mod.search_apps_db is ak.search_apps_db
    assert apps_mod.add_app_to_db is ak.add_app_to_db
    assert apps_mod.update_app_in_db is ak.update_app_in_db
    assert apps_mod.delete_app_from_db is ak.delete_app_from_db
    assert apps_mod.update_app_visibility_in_db is ak.update_app_visibility_in_db
    assert apps_mod.set_app_popular_db is ak.set_app_popular_db

    # NÃO shimada — permanece Firestore, sem rebind pro módulo apps_karla.
    assert apps_mod.set_app_review_in_db is not getattr(ak, "set_app_review_in_db", None)
    assert not hasattr(ak, "set_app_review_in_db")
