"""F4: exporta TODAS as memórias do Firestore (descriptografadas) em JSONL no
shape do POST /admin/memorias/lote do memory-service. Rodar DENTRO do container
backend (docker exec -w /tmp), nunca gravar em /app.

Uso: python export_memorias_f4.py <uid> [saida.jsonl]
"""
import json
import sys

sys.path.insert(0, "/app")

import database.memories as memories_db  # noqa: E402

CATEGORIAS_VALIDAS = {"system", "manual", "interesting"}
# campos que fazem round-trip em extras (espelho do shim memories_karla)
EXTRAS = [
    "scoring",
    "visibility",
    "reviewed",
    "user_review",
    "manually_added",
    "edited",
    "conversation_id",
    "memory_id",
    "app_id",
    "source",
    "headline",
    "data_protection_level",
]


def main() -> None:
    uid = sys.argv[1]
    saida = sys.argv[2] if len(sys.argv) > 2 else "/tmp/omi-memorias-f4.jsonl"
    total = 0
    with open(saida, "w", encoding="utf-8") as f:
        offset = 0
        while True:
            pagina = memories_db.get_non_filtered_memories(uid, limit=500, offset=offset)
            if not pagina:
                break
            for m in pagina:
                cat = str(m.get("category") or "system")
                if cat not in CATEGORIAS_VALIDAS:
                    cat = "system"  # legadas (core, hobbies...) colapsam como no validator
                extras = {k: m.get(k) for k in EXTRAS if m.get(k) is not None}
                extras["omi_id"] = m["id"]
                created = m.get("created_at")
                item = {
                    "origem": f"omi:{m['id']}",
                    "area": "gustavo",
                    "texto": m.get("content") or "",
                    "categoria": cat,
                    "tags": list(m.get("tags") or []),
                    "criado_em": created.isoformat() if created else None,
                    "extras": json.loads(json.dumps(extras, default=str)),
                }
                if not item["texto"].strip():
                    continue
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
                total += 1
            offset += 500
    print(f"exportadas: {total} -> {saida}")


if __name__ == "__main__":
    main()
