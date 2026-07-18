"""Knowledge graph (Firestore nodes/edges) sobre a MEMÓRIA UNIFICADA (Karla) —
drop-in das funções Firestore de database/knowledge_graph.py (L99-L241).

F4.2/F4.4 (Omi self-hosted): os nós e arestas do grafo de conhecimento por
usuário saem do Firestore e moram no doc-store genérico do memory-service (via
`omi_docs_karla`), coleções `knowledge_nodes` e `knowledge_edges`. Este módulo
replica as funções PÚBLICAS Firestore de database/knowledge_graph.py falando
com o cliente genérico. Ativação por env `OMI_DOCS_KARLA=true` (ver rodapé de
database/knowledge_graph.py); com a flag off, nada muda (rollback = desligar).
A parte NEO4J do arquivo original (não Firestore) NÃO tem equivalente aqui —
fica intacta no módulo original.

Diferenças de semântica vs Firestore:
  - doc_id == node_data['id'] / edge_data['id'] (o original usa
    `.document(node_id)`/`.document(edge_id)` diretamente).
  - `find_node_by_label_or_alias`: o original faz 2 queries indexadas
    (label_lower ==, depois aliases_lower array_contains), ambas limit(1). O
    doc-store genérico não tem array_contains; replica-se via scan
    client-side sobre `listar(limite=5000)`, igual ao padrão documentado no
    brief (Karla ainda não tem índice equivalente pra esse volume).
  - `delete_knowledge_graph`: o original faz batch-delete paginado (500 por
    vez) nas duas subcoleções. Aqui, busca-se todos os ids via `listar` e
    deleta em lotes via `deletar_em_lote(ids=...)` (o endpoint aceita CSV de
    ids; lotes de 500 pra manter o parentesco com o BATCH_LIMIT do Firestore).

Filosofia de erro: herdada do cliente — erros de rede logam (warning+sanitize)
e devolvem valor neutro, pra não derrubar o app.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database import omi_docs_karla as k

logger = logging.getLogger(__name__)

_NODES = "knowledge_nodes"
_EDGES = "knowledge_edges"

_SCAN_LIMIT = 5000
_DELETE_BATCH = 500


def get_knowledge_nodes(uid: str) -> List[Dict[str, Any]]:
    """Todos os nós do usuário."""
    return k.listar(_NODES, limite=_SCAN_LIMIT)


def get_knowledge_node(uid: str, node_id: str) -> Optional[Dict[str, Any]]:
    """GET por id, ou None."""
    return k.obter(_NODES, node_id)


def find_node_by_label_or_alias(uid: str, label: str) -> Optional[Dict[str, Any]]:
    """Varredura client-side (igual ao original em espírito: label_lower exato
    primeiro, depois aliases_lower contains) sobre `listar(limite=5000)`."""
    if not label:
        return None

    label_lower = label.lower()
    nodes = k.listar(_NODES, limite=_SCAN_LIMIT)

    for node in nodes:
        if node.get('label_lower') == label_lower:
            return node

    for node in nodes:
        if label_lower in (node.get('aliases_lower') or []):
            return node

    return None


def upsert_knowledge_node(uid: str, node_data: Dict[str, Any]) -> Dict[str, Any]:
    """Cria ou funde um nó por id/label (mesma lógica de merge do original:
    memory_ids e aliases são unidos por conjunto; label_lower/aliases_lower
    recalculados)."""
    node_id = node_data.get('id')
    if not node_id:
        existing_node = find_node_by_label_or_alias(uid, node_data.get('label', ''))
        if existing_node:
            node_id = existing_node['id']
        else:
            node_id = str(uuid.uuid4())
        node_data['id'] = node_id

    existing = k.obter(_NODES, node_id)

    if existing is None:
        existing_by_label = find_node_by_label_or_alias(uid, node_data.get('label', ''))
        if existing_by_label:
            node_id = existing_by_label['id']
            node_data['id'] = node_id
            existing = k.obter(_NODES, node_id)

    now = datetime.now(timezone.utc)
    if existing is not None:
        existing_memory_ids = set(existing.get('memory_ids', []) or [])
        new_memory_ids = set(node_data.get('memory_ids', []) or [])
        merged_memory_ids = list(existing_memory_ids | new_memory_ids)

        existing_aliases = set(existing.get('aliases', []) or [])
        new_aliases = set(node_data.get('aliases', []) or [])
        merged_aliases = list(existing_aliases | new_aliases)

        node_data['memory_ids'] = merged_memory_ids
        node_data['aliases'] = merged_aliases
        node_data['updated_at'] = now
        node_data['created_at'] = existing.get('created_at', now)
        node_data['label_lower'] = (node_data.get('label') or '').lower()
        node_data['aliases_lower'] = [a.lower() for a in node_data.get('aliases', [])]
        criado_em = node_data['created_at']
    else:
        node_data['created_at'] = now
        node_data['updated_at'] = now
        node_data['label_lower'] = (node_data.get('label') or '').lower()
        node_data['aliases_lower'] = [a.lower() for a in node_data.get('aliases', [])]
        criado_em = now

    k.upsert(_NODES, node_id, node_data, criado_em=criado_em)
    return node_data


def get_knowledge_edges(uid: str) -> List[Dict[str, Any]]:
    """Todas as arestas do usuário."""
    return k.listar(_EDGES, limite=_SCAN_LIMIT)


def upsert_knowledge_edge(uid: str, edge_data: Dict[str, Any]) -> Dict[str, Any]:
    """Cria ou funde uma aresta por id (default: `{source}_{label}_{target}`
    com '/' saneado); memory_ids unidos por conjunto, igual ao original."""
    edge_id = edge_data.get('id')
    if not edge_id:
        edge_id = f"{edge_data['source_id']}_{edge_data['label']}_{edge_data['target_id']}"
    edge_id = edge_id.replace('/', '_')
    edge_data['id'] = edge_id

    existing = k.obter(_EDGES, edge_id)
    now = datetime.now(timezone.utc)

    if existing is not None:
        existing_memory_ids = set(existing.get('memory_ids', []) or [])
        new_memory_ids = set(edge_data.get('memory_ids', []) or [])
        merged_memory_ids = list(existing_memory_ids | new_memory_ids)

        edge_data['memory_ids'] = merged_memory_ids
        edge_data['created_at'] = existing.get('created_at', now)
        criado_em = edge_data['created_at']
    else:
        edge_data['created_at'] = now
        criado_em = now

    k.upsert(_EDGES, edge_id, edge_data, criado_em=criado_em)
    return edge_data


def get_knowledge_graph(uid: str) -> Dict[str, Any]:
    """Nós + arestas do usuário."""
    return {
        'nodes': get_knowledge_nodes(uid),
        'edges': get_knowledge_edges(uid),
    }


def delete_knowledge_graph(uid: str) -> None:
    """Deleta todos os nós e arestas do usuário, em lotes de até 500 ids (via
    `deletar_em_lote(ids=...)`), espelhando o batch-delete paginado do
    original."""

    def _batch_delete(colecao: str):
        docs = k.listar(colecao, limite=1_000_000)
        ids = [d['id'] for d in docs if d.get('id')]
        for i in range(0, len(ids), _DELETE_BATCH):
            k.deletar_em_lote(colecao, ids=ids[i : i + _DELETE_BATCH])

    _batch_delete(_NODES)
    _batch_delete(_EDGES)
