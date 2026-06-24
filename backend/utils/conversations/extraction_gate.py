import os


def auto_extraction_enabled() -> bool:
    """Gate global da extração automática derivada (memórias/tarefas/trends/grafo).

    Desligável via env var OMI_AUTO_EXTRACTION_ENABLED='false' no self-host.
    Default ligado (preserva comportamento upstream). Transcrição, criação da
    conversa, título/overview e save_structured_vector NÃO são afetados.
    """
    return os.getenv('OMI_AUTO_EXTRACTION_ENABLED', 'true').strip().lower() != 'false'
