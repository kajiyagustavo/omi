"""F4.5: exporta as coleções de CONFIG do Omi (doc raiz `users/{uid}`, `people`,
`integrations`, `task_integrations`, `fcm_tokens`, `hourly_usage`, `llm_usage`,
`meetings`, `dev_api_keys`, `mcp_api_keys` + `plugins_data` global) do
Firestore em NDJSON, no shape esperado pelo host-side uploader EXISTENTE
(`upload_omi_docs.py`, no host da Karla — sem uploader novo aqui). Rodar DENTRO
do container backend (docker exec -w /tmp), nunca gravar em /app.

Cada linha do NDJSON: {colecao, doc_id, dados, criado_em}, mesmo shape de
`export_omi_docs_f42.py` (mold desta task).

⚠️ Este script exige leitura DIRETA do Firestore via `database._client.db`. Se
`CONFIG_KARLA=true` estiver setado no ambiente do container, os módulos
`database.users`/`database.notifications`/`database.user_usage`/
`database.llm_usage`/`database.apps`/`database.calendar_meetings`/
`database.dev_api_key`/`database.mcp_api_key` rebindam suas funções públicas
pra falar com a MEMÓRIA UNIFICADA (Karla) via shims *_karla.py — mas este
script não usa essas funções públicas, ele lê Firestore direto via
`database._client.db` em TODOS os casos. Ainda assim, abortamos com
CONFIG_KARLA ligada: rodar esta exportação nesse ambiente sugere confusão
sobre a direção do dado (isto é um export FONTE, não um consumidor da Karla) e
arrisca um operador rodar o upload em cima de um ambiente que já pensa estar
na Karla — mesma cautela do mold (que aborta em OMI_DOCS_KARLA).

Uso: python export_config_f45.py [uid]
"""

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, Iterator, Optional, Tuple

sys.path.insert(0, "/app")

if os.getenv("CONFIG_KARLA", "").lower() in ("1", "true", "yes"):
    raise SystemExit(
        "ABORT: CONFIG_KARLA está ligada neste ambiente — este script exporta a config "
        "FONTE do Firestore para depois subir na MEMÓRIA UNIFICADA (Karla). Rodar com "
        "CONFIG_KARLA ligada mistura as pontas (o ambiente já se comporta como consumidor "
        "da Karla) e arrisca um export self-referencial. Rode com CONFIG_KARLA unset/false."
    )

from database._client import db  # noqa: E402

DEFAULT_UID = "oMFDcKdDLBUSHIDP182t5eoHqMA2"
SAIDA = "/tmp/export_config_f45.ndjson"


def _serializar(obj: Any) -> Any:
    """Deep-converte datetimes → ISO-8601 (com tz) preservando o resto."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serializar(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serializar(v) for v in obj]
    return obj


def _criado_em_iso(valor: Any) -> Optional[str]:
    if isinstance(valor, datetime):
        return valor.isoformat()
    return None


def normalizar_doc(colecao: str, doc_id: str, dados: Dict[str, Any], uid: Optional[str] = None) -> Dict[str, Any]:
    """Aplica as regras de injeção de identidade por coleção, replicando a
    convenção de cada shim `database/*_karla.py` — pra que o doc exportado
    seja indistinguível de um doc escrito pelo próprio shim.

    Regras (uma por coleção, ver docstrings dos shims):
      - users            → injeta `uid` (users_karla._merge_user /
                             notifications_karla._merge_user seedam `uid` no
                             corpo pra permitir reconstrução sem doc_id —
                             CRÍTICO: sem isso o cron de daily summary cai no
                             fallback `_SELF_HOST_DEFAULT_UID`).
      - people           → injeta `id` (get_person/get_people fazem
                             `.setdefault('id', person_id)`; o doc Firestore
                             original nem sempre tem `id` gravado).
      - task_integrations→ injeta `app_key` (set_task_integration faz
                             `data.setdefault('app_key', app_key)`, necessário
                             pois `k.listar` não devolve doc_id).
      - fcm_tokens       → injeta `device_key` (save_token grava
                             `device_key` em dados pelo mesmo motivo).
      - hourly_usage     → injeta `id` (update_hourly_usage grava `id` =
                             doc_id do bucket de hora).
      - meetings         → injeta `id` (get_meeting injeta `id` na resposta).
      - dev_api_keys     → injeta `id` (create_dev_key grava `id` = key_id
                             no doc; doc_id = key_id).
      - mcp_api_keys     → injeta `id` (create_mcp_key, idem).
      - integrations     → NENHUMA injeção adicional — get_integration/
                             set_integration não gravam nenhuma chave de
                             identidade além do que já vem em `dados`
                             (doc_id = app_key, mas o shim nunca lê um campo
                             `app_key` interno para reconstruir a chave).
      - llm_usage        → NENHUMA injeção — `dados['date']` já é o campo
                             ORIGINAL do Firestore (mold: `criado_em` vem
                             dele), não uma convenção introduzida pelo shim.
      - apps (plugins_data) → NENHUMA injeção — docs originais já carregam
                             `id` (verificado: `add_app_to_db` do shim faz
                             `k.upsert(_APPS, app_data['id'], app_data)`,
                             assumindo que `app_data['id']` já existe, como
                             no doc Firestore original).
    Passthrough verbatim para as demais.
    """
    dados = dict(dados)
    if colecao == "users":
        dados.setdefault("uid", uid if uid is not None else doc_id)
    elif colecao == "people":
        dados.setdefault("id", doc_id)
    elif colecao == "task_integrations":
        dados.setdefault("app_key", doc_id)
    elif colecao == "fcm_tokens":
        dados.setdefault("device_key", doc_id)
    elif colecao in ("hourly_usage", "meetings", "dev_api_keys", "mcp_api_keys"):
        dados.setdefault("id", doc_id)
    return dados


def _montar_item(
    colecao: str, doc_id: str, dados: Dict[str, Any], campo_criado: str, uid: Optional[str] = None
) -> Dict[str, Any]:
    dados_norm = normalizar_doc(colecao, doc_id, dados, uid=uid)
    return {
        "colecao": colecao,
        "doc_id": doc_id,
        "dados": _serializar(dados_norm),
        "criado_em": _criado_em_iso(dados_norm.get(campo_criado)),
    }


def _iter_subcolecao(uid: str, nome: str) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Itera todos os docs de users/{uid}/{nome} direto via Firestore.
    Retorna (doc_id, dados_dict)."""
    ref = db.collection("users").document(uid).collection(nome)
    for doc in ref.stream():
        data = doc.to_dict() or {}
        yield doc.id, data


def _iter_colecao_global(
    nome: str, campo_uid: Optional[str] = None, uid: Optional[str] = None
) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Itera docs de uma coleção de TOPO (não é subcoleção de users/{uid}).
    `dev_api_keys`/`mcp_api_keys` são coleções globais no Firestore original
    (cross-user por design — ver database/dev_api_key.py, mcp_api_key.py),
    então filtramos client-side por `campo_uid`/`uid` (mesma convenção usada
    pelos shims Karla: `k.listar(filtro={"user_id": user_id})`) pra exportar
    só a config deste tenant. `plugins_data` (colecao_saida `apps`) NÃO é
    filtrada por uid — apps são globais/compartilhados (dono via campo `uid`
    interno do próprio app, não um escopo de export)."""
    ref = db.collection(nome)
    for doc in ref.stream():
        data = doc.to_dict() or {}
        if campo_uid is not None and data.get(campo_uid) != uid:
            continue
        yield doc.id, data


def _exportar_subcolecao(f, uid: str, subcolecao: str, colecao_saida: str, campo_criado: str = "created_at") -> int:
    total = 0
    for doc_id, data in _iter_subcolecao(uid, subcolecao):
        item = _montar_item(colecao_saida, doc_id, data, campo_criado, uid=uid)
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        total += 1
    return total


def _exportar_colecao_global(
    f,
    nome: str,
    colecao_saida: str,
    campo_criado: str = "created_at",
    campo_uid: Optional[str] = None,
    uid: Optional[str] = None,
) -> int:
    total = 0
    for doc_id, data in _iter_colecao_global(nome, campo_uid=campo_uid, uid=uid):
        item = _montar_item(colecao_saida, doc_id, data, campo_criado)
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        total += 1
    return total


def _exportar_users_root(f, uid: str) -> int:
    """Doc raiz `users/{uid}` → colecao `users`, doc_id=uid, dados VERBATIM
    (round-trip completo, incl. campos de payment) + `uid` injetado (ver
    normalizar_doc)."""
    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        return 0
    data = doc.to_dict() or {}
    item = _montar_item("users", uid, data, "created_at", uid=uid)
    f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return 1


def main() -> None:
    uid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_UID

    contagens: Dict[str, int] = {}
    with open(SAIDA, "w", encoding="utf-8") as f:
        contagens["users"] = _exportar_users_root(f, uid)
        contagens["people"] = _exportar_subcolecao(f, uid, "people", "people")
        contagens["integrations"] = _exportar_subcolecao(f, uid, "integrations", "integrations")
        contagens["task_integrations"] = _exportar_subcolecao(f, uid, "task_integrations", "task_integrations")
        contagens["fcm_tokens"] = _exportar_subcolecao(f, uid, "fcm_tokens", "fcm_tokens")
        contagens["hourly_usage"] = _exportar_subcolecao(f, uid, "hourly_usage", "hourly_usage")
        contagens["llm_usage"] = _exportar_subcolecao(f, uid, "llm_usage", "llm_usage")
        contagens["meetings"] = _exportar_subcolecao(f, uid, "meetings", "meetings")
        contagens["dev_api_keys"] = _exportar_colecao_global(
            f, "dev_api_keys", "dev_api_keys", campo_uid="user_id", uid=uid
        )
        contagens["mcp_api_keys"] = _exportar_colecao_global(
            f, "mcp_api_keys", "mcp_api_keys", campo_uid="user_id", uid=uid
        )
        contagens["apps"] = _exportar_colecao_global(f, "plugins_data", "apps")

    for colecao, total in contagens.items():
        print(f"{colecao}: {total}")
    print(f"total: {sum(contagens.values())} -> {SAIDA}")


if __name__ == "__main__":
    main()
