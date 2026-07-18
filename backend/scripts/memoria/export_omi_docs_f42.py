"""F4.2: exporta as coleções "doc-store genérico" do Omi (messages, files,
chat_sessions, daily_summaries, journal_summaries, folders, goals +
goal_history, knowledge_nodes, knowledge_edges) do Firestore em NDJSON, no
shape esperado pelo host-side uploader (Task 12), que faz POST para o
memory-service (`omi-docs`). Rodar DENTRO do container backend
(docker exec -w /tmp), nunca gravar em /app.

Leitura: a maioria das coleções não tem criptografia (folders, files,
chat_sessions, daily_summaries, journal_summaries, goals, goal_history,
knowledge_nodes, knowledge_edges) — lidas DIRETO via `database._client.db`.
`messages` é a exceção: o campo `text` pode estar criptografado
(data_protection_level == 'enhanced'), então usa-se
`database.chat.iter_all_messages(uid)`, que já decripta.

Uso: python export_omi_docs_f42.py [uid]
"""

import json
import sys
from datetime import datetime
from typing import Any, Dict, Iterator, Optional, Tuple

sys.path.insert(0, "/app")

import database.chat as chat_db  # noqa: E402
from database._client import db  # noqa: E402

DEFAULT_UID = "oMFDcKdDLBUSHIDP182t5eoHqMA2"
SAIDA = "/tmp/export_omi_docs_f42.ndjson"


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


def _iter_subcolecao(uid: str, nome: str) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Itera todos os docs de users/{uid}/{nome} direto via Firestore.
    Retorna (doc_id, dados_dict)."""
    ref = db.collection("users").document(uid).collection(nome)
    for doc in ref.stream():
        data = doc.to_dict() or {}
        yield doc.id, data


def _exportar_simples(f, uid: str, subcolecao: str, colecao_saida: str, campo_criado: str = "created_at") -> int:
    total = 0
    for doc_id, data in _iter_subcolecao(uid, subcolecao):
        item = {
            "colecao": colecao_saida,
            "doc_id": doc_id,
            "dados": _serializar(data),
            "criado_em": _criado_em_iso(data.get(campo_criado)),
        }
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        total += 1
    return total


def _exportar_messages(f, uid: str) -> int:
    total = 0
    for msg in chat_db.iter_all_messages(uid):
        doc_id = msg.get("id")
        item = {
            "colecao": "messages",
            "doc_id": doc_id,
            "dados": _serializar(msg),
            "criado_em": _criado_em_iso(msg.get("created_at")),
        }
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        total += 1
    return total


def _exportar_goals_e_historico(f, uid: str) -> Tuple[int, int]:
    total_goals = 0
    total_historico = 0
    for goal_id, goal_data in _iter_subcolecao(uid, "goals"):
        if not goal_data.get("id"):
            goal_data["id"] = goal_id
        item = {
            "colecao": "goals",
            "doc_id": goal_id,
            "dados": _serializar(goal_data),
            "criado_em": _criado_em_iso(goal_data.get("created_at")),
        }
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        total_goals += 1

        goals_ref = db.collection("users").document(uid).collection("goals")
        history_ref = goals_ref.document(goal_id).collection("goal_history")
        for history_doc in history_ref.stream():
            history_data = history_doc.to_dict() or {}
            history_data["goal_id"] = goal_id
            data_str = history_data.get("date") or history_doc.id
            item = {
                "colecao": "goal_history",
                "doc_id": f"{goal_id}_{data_str}",
                "dados": _serializar(history_data),
                "criado_em": _criado_em_iso(history_data.get("recorded_at")),
            }
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            total_historico += 1

    return total_goals, total_historico


def main() -> None:
    uid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_UID

    contagens: Dict[str, int] = {}
    with open(SAIDA, "w", encoding="utf-8") as f:
        contagens["messages"] = _exportar_messages(f, uid)
        contagens["chat_files"] = _exportar_simples(f, uid, "files", "chat_files")
        contagens["chat_sessions"] = _exportar_simples(f, uid, "chat_sessions", "chat_sessions")
        contagens["daily_summaries"] = _exportar_simples(f, uid, "daily_summaries", "daily_summaries")
        contagens["journal_summaries"] = _exportar_simples(f, uid, "journal_summaries", "journal_summaries")
        contagens["folders"] = _exportar_simples(f, uid, "folders", "folders")

        goals_total, goal_history_total = _exportar_goals_e_historico(f, uid)
        contagens["goals"] = goals_total
        contagens["goal_history"] = goal_history_total

        contagens["knowledge_nodes"] = _exportar_simples(f, uid, "knowledge_nodes", "knowledge_nodes")
        contagens["knowledge_edges"] = _exportar_simples(f, uid, "knowledge_edges", "knowledge_edges")

    for colecao, total in contagens.items():
        print(f"{colecao}: {total}")
    print(f"total: {sum(contagens.values())} -> {SAIDA}")


if __name__ == "__main__":
    main()
