import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

os.environ.setdefault("MEMORIA_UNIFICADA_URL", "http://karla-fake:8765")
os.environ.setdefault("MEMORIA_UNIFICADA_TOKEN", "tok-teste")
os.environ.setdefault("MEMORIAS_KARLA", "true")

from database import memories_karla  # noqa: E402


def _resp(json_data, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status.return_value = None
    return r


LINHA = {"id": 42, "area": "gustavo", "texto": "prefere vender por processo",
         "categoria": "system", "tags": ["comercial"], "origem": "omi:abc",
         "extras": {"scoring": "00_999_1", "visibility": "private",
                    "reviewed": True, "conversation_id": "c1"},
         "criado_em": "2026-05-02T13:31:02+00:00",
         "atualizado_em": "2026-07-14T00:00:00+00:00"}


def test_is_enabled_exige_flag_e_credenciais():
    assert memories_karla.is_enabled() is True
    with patch.dict(os.environ, {"MEMORIAS_KARLA": "false"}):
        assert memories_karla.is_enabled() is False
    with patch.dict(os.environ, {"MEMORIA_UNIFICADA_URL": ""}):
        assert memories_karla.is_enabled() is False


def test_to_omi_mapeia_e_preenche_defaults():
    m = memories_karla._to_omi(LINHA, "uid-teste")
    assert m["id"] == "42" and m["content"] == "prefere vender por processo"
    assert m["uid"] == "uid-teste"  # sem uid, MemoryDB.model_validate descarta a memória
    assert m["category"] == "system" and m["scoring"] == "00_999_1"
    assert m["conversation_id"] == "c1" and m["reviewed"] is True
    assert m["is_locked"] is False and m["edited"] is False
    assert isinstance(m["created_at"], datetime)
    assert m["created_at"].tzinfo is not None


def test_to_omi_valida_no_modelo_do_router():
    """O contrato real: o dict do shim TEM que passar no MemoryDB.model_validate
    que o GET /v3/memories aplica (senão a memória é pulada em silêncio)."""
    from models.memories import MemoryDB
    m = memories_karla._to_omi(LINHA, "uid-teste")
    validada = MemoryDB.model_validate(m)
    assert validada.id == "42" and validada.uid == "uid-teste"


def test_to_omi_sanitiza_enums_invalidos():
    """Caso real de prod (doc 35cfc6c4): source/topic fora do enum derrubavam a
    memória no model_validate — o shim não pode emitir valores inválidos."""
    from models.memories import MemoryDB
    suja = {**LINHA, "extras": {**LINHA["extras"],
                                "source": "obsidian-backfill-cliente",
                                "topic": "obsidian:dominio/cliente/dores.md"}}
    m = memories_karla._to_omi(suja, "uid-teste")
    assert "source" not in m and "topic" not in m
    MemoryDB.model_validate(m)  # não pode levantar


def test_get_memories_passa_filtros():
    with patch.object(memories_karla, "requests") as rq:
        rq.request.return_value = _resp({"memorias": [LINHA]})
        out = memories_karla.get_memories("uid", limit=50, offset=10,
                                          categories=["manual"])
        args, kwargs = rq.request.call_args
        assert args[0] == "GET" and args[1].endswith("/u/tok-teste/memorias")
        assert kwargs["params"]["categorias"] == "manual"
        assert kwargs["params"]["limite"] == 50 and kwargs["params"]["offset"] == 10
    assert out[0]["id"] == "42"


def test_create_memory_gera_origem_do_id_omi():
    with patch.object(memories_karla, "requests") as rq:
        rq.request.return_value = _resp(LINHA, status=201)
        memories_karla.create_memory("uid", {
            "id": "hash-do-conteudo", "content": "x", "category": "manual",
            "tags": [], "created_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
            "scoring": "01_998_2", "visibility": "private"})
        corpo = rq.request.call_args.kwargs["json_body"]
        assert corpo["origem"] == "omi:hash-do-conteudo"
        assert corpo["categoria"] == "manual"
        assert corpo["extras"]["scoring"] == "01_998_2"
        assert "content" not in corpo["extras"]  # core não vaza pros extras


def test_get_memory_resolve_id_hash_via_origem():
    with patch.object(memories_karla, "requests") as rq:
        rq.request.side_effect = [_resp({"memorias": [LINHA]}), _resp(LINHA)]
        m = memories_karla.get_memory("uid", "hash-legado")
        primeira = rq.request.call_args_list[0]
        assert primeira.kwargs["params"]["origem"] == "omi:hash-legado"
    assert m["id"] == "42"


def test_review_memory_faz_extras_merge():
    with patch.object(memories_karla, "requests") as rq:
        rq.request.side_effect = [_resp(LINHA), _resp(LINHA)]
        memories_karla.review_memory("uid", "42", False)
        patch_call = rq.request.call_args_list[-1]
        assert patch_call.args[0] == "PATCH"
        assert patch_call.kwargs["json_body"]["extras_merge"]["user_review"] is False


def test_find_similar_shape_do_pinecone():
    with patch.object(memories_karla, "requests") as rq:
        rq.request.return_value = _resp({"similares": [
            {"id": 7, "texto": "t", "categoria": "system", "sim": 0.91}]})
        out = memories_karla.find_similar("t", threshold=0.85, limit=5)
    assert out == [{"memory_id": "7", "category": "system", "score": 0.91}]
