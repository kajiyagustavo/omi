"""F4.1: exporta TODAS as conversas do Firestore (descriptografadas, com fotos)
em NDJSON no shape esperado pelo host-side uploader (Task 12), que faz POST
para o memory-service (`conversas_omi`). Rodar DENTRO do container backend
(docker exec -w /tmp), nunca gravar em /app.

⚠️ Este script exige que `database.conversations` esteja em modo Firestore
(CONVERSAS_KARLA unset/false) — ele lê Firestore diretamente via
`iter_all_conversations`. Se `CONVERSAS_KARLA=true` estiver setado no
ambiente do container, as conversas já migraram pra Karla e este script
abortaria lendo a fonte errada (ou pior, uma Karla vazia/parcial).

Uso: python export_conversas_f41.py [uid]
"""

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict

sys.path.insert(0, "/app")

if os.getenv("CONVERSAS_KARLA", "").lower() in ("1", "true", "yes"):
    raise SystemExit(
        "ABORT: CONVERSAS_KARLA está ligado neste ambiente — este script precisa ler o "
        "Firestore original (iter_all_conversations), não a Karla. Rode com CONVERSAS_KARLA "
        "unset/false, ou use o export já feito pela Karla em vez deste script."
    )

import database.conversations as conversations_db  # noqa: E402

DEFAULT_UID = "oMFDcKdDLBUSHIDP182t5eoHqMA2"
SAIDA = "/tmp/export_conversas_f41.ndjson"


def _serializar(obj: Any) -> Any:
    """Deep-converte datetimes → ISO-8601 (com tz) preservando o resto."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serializar(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serializar(v) for v in obj]
    return obj


def normalizar_conversa(conv: Dict[str, Any]) -> Dict[str, Any]:
    """Função PURA: normaliza um dict de conversa (já decriptado) pro shape de
    export. Datetimes → ISO com timezone; garante `transcript_segments` como
    lista (levanta se vier str — decrypt falhou upstream); força
    `transcript_segments_compressed=False` (dados exportados nunca vêm
    comprimidos)."""
    segments = conv.get("transcript_segments")
    if isinstance(segments, str):
        raise ValueError(
            f"conversa {conv.get('id')}: transcript_segments ainda é str (decrypt falhou) — abortando export"
        )

    data = dict(conv)
    data["transcript_segments_compressed"] = False
    return _serializar(data)


def main() -> None:
    uid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_UID

    total = 0
    descartadas = 0
    with open(SAIDA, "w", encoding="utf-8") as f:
        for conv in conversations_db.iter_all_conversations(uid):
            cid = conv.get("id")
            if not cid:
                descartadas += 1
                continue

            try:
                photos = conversations_db.get_conversation_photos(uid, cid) or []
            except Exception as e:
                print(f"aviso: falha ao buscar fotos da conversa {cid}: {e}", file=sys.stderr)
                photos = []
            conv["photos"] = photos

            try:
                normalizada = normalizar_conversa(conv)
            except ValueError as e:
                print(f"ABORT: {e}", file=sys.stderr)
                raise SystemExit(1)

            item = {"id": cid, "dados": normalizada}
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            total += 1

    print(f"exportadas: {total} descartadas: {descartadas} -> {SAIDA}")


if __name__ == "__main__":
    main()
