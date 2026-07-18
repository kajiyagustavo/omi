import json
import os
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("MEMORIA_UNIFICADA_URL", "http://karla-fake:8765")
os.environ.setdefault("MEMORIA_UNIFICADA_TOKEN", "tok-teste")
os.environ.setdefault("OMI_DOCS_KARLA", "true")

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

from database import daily_summaries_karla as dsk  # noqa: E402
from database import journal_summaries_karla as jsk  # noqa: E402
from database import goals_karla as gk  # noqa: E402
from database import knowledge_graph_karla as kgk  # noqa: E402
from database import omi_docs_karla as odk  # noqa: E402
from database import daily_summaries as daily_summaries_mod  # noqa: E402
from database import journal_summaries as journal_summaries_mod  # noqa: E402
from database import goals as goals_mod  # noqa: E402
from database import knowledge_graph as knowledge_graph_mod  # noqa: E402


def _resp(json_data, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status.return_value = None
    return r


# ── daily_summaries_karla ────────────────────────────────────────────────────


def test_create_daily_summary_upsert_colecao_doc_id_dados():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"ok": True}, status=201)
        summary_data = {"id": "sum-1", "date": "2026-07-14", "headline": "oi"}
        result_id = dsk.create_daily_summary("uid", summary_data)
        assert result_id == "sum-1"
        args, kwargs = rq.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/u/tok-teste/omi-docs/daily_summaries")
        corpo = kwargs["json"]
        assert corpo["doc_id"] == "sum-1"
        assert corpo["dados"]["date"] == "2026-07-14"


def test_get_daily_summary_by_date_filtro_date_string():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": [{"dados": {"id": "sum-1", "date": "2026-07-14"}}]})
        result = dsk.get_daily_summary_by_date("uid", "2026-07-14")
        assert result["id"] == "sum-1"
        args, kwargs = rq.request.call_args
        assert args[0] == "GET"
        filtro = json.loads(kwargs["params"]["filtro"])
        assert filtro == {"date": "2026-07-14"}
        assert kwargs["params"]["limite"] == 1


def test_daily_summaries_footer_rebind():
    assert daily_summaries_mod.create_daily_summary is dsk.create_daily_summary
    assert daily_summaries_mod.get_daily_summary_by_date is dsk.get_daily_summary_by_date


# ── journal_summaries_karla ──────────────────────────────────────────────────


def test_set_journal_summary_upsert_doc_id_eh_date():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"ok": True}, status=201)
        result = jsk.set_journal_summary("uid", "2026-07-14", "resumo do dia")
        assert result["date"] == "2026-07-14"
        assert result["summary"] == "resumo do dia"
        args, kwargs = rq.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/u/tok-teste/omi-docs/journal_summaries")
        corpo = kwargs["json"]
        assert corpo["doc_id"] == "2026-07-14"
        assert corpo["dados"]["summary"] == "resumo do dia"


def test_get_journal_summary_obter_por_date():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"dados": {"date": "2026-07-14", "summary": "x"}})
        result = jsk.get_journal_summary("uid", "2026-07-14")
        assert result["summary"] == "x"
        args, kwargs = rq.request.call_args
        assert args[0] == "GET"
        assert args[1].endswith("/u/tok-teste/omi-docs/journal_summaries/2026-07-14")


def test_journal_summaries_footer_rebind():
    assert journal_summaries_mod.set_journal_summary is jsk.set_journal_summary
    assert journal_summaries_mod.get_journal_summary is jsk.get_journal_summary
    assert journal_summaries_mod.delete_journal_summary is jsk.delete_journal_summary


# ── goals_karla ───────────────────────────────────────────────────────────────


def test_create_goal_upsert_colecao_e_gera_id():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"docs": []}),  # listar active_goals (abaixo do max)
            _resp({"ok": True}, status=201),  # upsert do goal novo
        ]
        goal_data = {"title": "correr 5k"}
        result = gk.create_goal("uid", goal_data, max_goals=4)
        assert result["id"].startswith("goal_")
        assert result["is_active"] is True

        # segunda chamada é o upsert
        args, kwargs = rq.request.call_args_list[1]
        assert args[0] == "POST"
        assert args[1].endswith("/u/tok-teste/omi-docs/goals")
        corpo = kwargs["json"]
        assert corpo["doc_id"] == result["id"]


def test_save_goal_progress_history_injeta_goal_id_no_doc_id():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"ok": True}, status=201)
        gk.save_goal_progress_history("uid", "goal-42", 3.5)
        args, kwargs = rq.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/u/tok-teste/omi-docs/goal_history")
        corpo = kwargs["json"]
        assert corpo["doc_id"].startswith("goal-42_")
        assert corpo["dados"]["goal_id"] == "goal-42"
        assert corpo["dados"]["value"] == 3.5


def test_get_goal_history_filtro_goal_id():
    with patch.object(odk, "requests") as rq:
        docs = {
            "docs": [
                {"dados": {"goal_id": "goal-42", "date": "2026-07-10", "value": 1}},
                {"dados": {"goal_id": "goal-42", "date": "2026-07-14", "value": 2}},
            ]
        }
        rq.request.return_value = _resp(docs)
        result = gk.get_goal_history("uid", "goal-42", days=30)
        args, kwargs = rq.request.call_args
        assert args[0] == "GET"
        filtro = json.loads(kwargs["params"]["filtro"])
        assert filtro == {"goal_id": "goal-42"}
        assert kwargs["params"]["limite"] == 30
        # ordenado desc por date
        assert [d["date"] for d in result] == ["2026-07-14", "2026-07-10"]


def test_goals_footer_rebind():
    assert goals_mod.create_goal is gk.create_goal
    assert goals_mod.get_goal_history is gk.get_goal_history
    assert goals_mod.save_goal_progress_history is gk.save_goal_progress_history


# ── knowledge_graph_karla ─────────────────────────────────────────────────────


def test_upsert_knowledge_node_gera_id_quando_ausente():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"docs": []}),  # find_node_by_label_or_alias (scan p/ gerar id)
            _resp(None, status=404),  # obter (novo)
            _resp({"ok": True}, status=201),  # upsert
        ]
        node_data = {"label": "Python"}
        result = kgk.upsert_knowledge_node("uid", node_data)
        assert result["id"]
        assert result["label_lower"] == "python"

        args, kwargs = rq.request.call_args_list[-1]
        assert args[0] == "POST"
        assert args[1].endswith("/u/tok-teste/omi-docs/knowledge_nodes")
        corpo = kwargs["json"]
        assert corpo["doc_id"] == result["id"]


def test_upsert_knowledge_node_usa_id_explicito():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp(None, status=404),  # obter(node_id) — não existe
            _resp({"ok": True}, status=201),  # upsert
        ]
        node_data = {"id": "node-abc", "label": "Rust"}
        result = kgk.upsert_knowledge_node("uid", node_data)
        assert result["id"] == "node-abc"
        args, kwargs = rq.request.call_args_list[-1]
        corpo = kwargs["json"]
        assert corpo["doc_id"] == "node-abc"


def test_delete_knowledge_graph_usa_ids_em_lotes():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"docs": [{"dados": {"id": "n1"}}, {"dados": {"id": "n2"}}]}),  # listar nodes
            _resp({"deletados": 2}),  # deletar_em_lote nodes
            _resp({"docs": [{"dados": {"id": "e1"}}]}),  # listar edges
            _resp({"deletados": 1}),  # deletar_em_lote edges
        ]
        kgk.delete_knowledge_graph("uid")

        calls = rq.request.call_args_list
        assert calls[0][0][0] == "GET"
        assert calls[1][0][0] == "DELETE"
        assert calls[1][0][1].endswith("/u/tok-teste/omi-docs/knowledge_nodes")
        assert calls[1][1]["params"]["ids"] == "n1,n2"
        assert calls[2][0][1].endswith("/u/tok-teste/omi-docs/knowledge_edges")
        assert calls[3][1]["params"]["ids"] == "e1"


def test_knowledge_graph_footer_rebind():
    assert knowledge_graph_mod.upsert_knowledge_node is kgk.upsert_knowledge_node
    assert knowledge_graph_mod.delete_knowledge_graph is kgk.delete_knowledge_graph
    assert knowledge_graph_mod.find_node_by_label_or_alias is kgk.find_node_by_label_or_alias
