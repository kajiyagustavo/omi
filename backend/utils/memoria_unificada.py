"""Cliente da MEMÓRIA UNIFICADA (memory-service na Karla).

A fonte de verdade das memórias é o cérebro unificado (Postgres+pgvector,
RLS por área) — o Omi é a interface de leitura sobre ele. Este cliente lê
via REST `GET /u/<token>/buscar` (mesmo RLS do MCP).

Config por env (ambas obrigatórias; sem elas → is_enabled() False e os
consumidores caem no caminho local Firestore/Pinecone):
  MEMORIA_UNIFICADA_URL    ex.: http://memory-service-app-1:8765
  MEMORIA_UNIFICADA_TOKEN  token de usuário do memory-service (escopo por área)
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    return bool(os.getenv("MEMORIA_UNIFICADA_URL") and os.getenv("MEMORIA_UNIFICADA_TOKEN"))


def buscar(query: str, limite_notas: int = 3, timeout: int = 30) -> dict | None:
    """Busca semântica no cérebro. Retorna {fatos, notas, relacionadas} ou
    None em qualquer erro (o chamador faz fallback local — nunca levanta)."""
    if not is_enabled():
        return None
    base = os.getenv("MEMORIA_UNIFICADA_URL", "").rstrip("/")
    token = os.getenv("MEMORIA_UNIFICADA_TOKEN", "")
    try:
        r = requests.get(
            f"{base}/u/{token}/buscar",
            params={"q": query, "limite_notas": limite_notas},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"memoria_unificada.buscar falhou (fallback local): {e}")
        return None


def atividades_do_dia(de_iso: str, ate_iso: str, timeout: int = 20) -> str | None:
    """Bloco compacto com as notas registradas na memória unificada na janela
    dada (sessões de trabalho com IA/ferramentas) — usado pelo jornal diário.
    None em erro/vazio (o jornal segue só com as conversas de áudio)."""
    if not is_enabled():
        return None
    base = os.getenv("MEMORIA_UNIFICADA_URL", "").rstrip("/")
    token = os.getenv("MEMORIA_UNIFICADA_TOKEN", "")
    try:
        r = requests.get(f"{base}/u/{token}/notas-do-dia",
                         params={"de": de_iso, "ate": ate_iso}, timeout=timeout)
        r.raise_for_status()
        notas = r.json().get("notas") or []
    except Exception as e:
        logger.warning(f"memoria_unificada.atividades_do_dia falhou: {e}")
        return None
    if not notas:
        return None
    linhas = [f"- [{n.get('tipo','?')}/{n.get('area','?')}] {n.get('titulo','')}"
              for n in notas[:60]]
    return "\n".join(linhas)


def formatar_resultado(d: dict | None, query: str, max_corpo: int = 4000) -> str | None:
    """Formata a resposta do /buscar como texto pronto pra LLM (mesmo papel do
    formato Firestore). None se não houver nada útil (chamador faz fallback)."""
    if not d:
        return None
    notas = d.get("notas") or []
    fatos = d.get("fatos") or []
    if not notas and not fatos:
        return None
    partes = [f"Found {len(fatos) + len(notas)} results in the unified memory "
              f"for '{query}':"]
    for f in fatos:
        texto = f.get("texto") if isinstance(f, dict) else str(f)
        if texto:
            partes.append(f"- {texto}")
    for n in notas:
        corpo = (n.get("corpo_md") or "")[:max_corpo]
        partes.append(f"\n## {n.get('titulo', 'Sem título')} "
                      f"(área: {n.get('area', '?')})\n{corpo}")
    relacionadas = d.get("relacionadas") or []
    if relacionadas:
        titulos = "; ".join(r.get("titulo", "") for r in relacionadas[:5])
        partes.append(f"\nRelated notes in the memory graph: {titulos}")
    return "\n".join(partes)
