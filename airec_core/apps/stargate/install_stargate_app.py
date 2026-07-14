#!/usr/bin/env python3
"""
Instala/atualiza o app privado "Stargate — Copiloto Pessoal" no backend do Omi.

Idempotente: usa upsert_app_to_db (grava por id no Firestore). Rode de novo a
qualquer momento para aplicar edições no app.json — o mesmo id é sobrescrito.

Uso (a partir de ~/omi/backend, com o venv/env do backend ativo):
    python ../airec_core/apps/stargate/install_stargate_app.py --uid SEU_FIREBASE_UID

Ou defina STARGATE_OWNER_UID no ambiente:
    STARGATE_OWNER_UID=xxxx python ../airec_core/apps/stargate/install_stargate_app.py

Para remover:
    python ../airec_core/apps/stargate/install_stargate_app.py --uninstall
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
APP_JSON = os.path.join(HERE, "app.json")


def load_app_definition() -> dict:
    with open(APP_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    parser = argparse.ArgumentParser(description="Instala o app Stargate no Omi")
    parser.add_argument("--uid", default=os.getenv("STARGATE_OWNER_UID"),
                        help="Firebase UID do dono (ou env STARGATE_OWNER_UID)")
    parser.add_argument("--uninstall", action="store_true", help="Remove o app")
    args = parser.parse_args()

    # Importa a camada de dados do backend (rode a partir de backend/ ou com PYTHONPATH nele).
    try:
        from database.apps import upsert_app_to_db, delete_app_from_db, get_app_by_id_db
    except ImportError:
        sys.stderr.write(
            "ERRO: rode este script a partir de ~/omi/backend (para achar database.apps),\n"
            "com o ambiente/venv do backend ativo. Ex.:\n"
            "  cd ~/omi/backend && python ../airec_core/apps/stargate/install_stargate_app.py --uid SEU_UID\n"
        )
        return 2

    app = load_app_definition()

    if args.uninstall:
        delete_app_from_db(app["id"])
        print(f"[stargate] removido: {app['id']}")
        return 0

    if not args.uid:
        sys.stderr.write("ERRO: informe --uid SEU_FIREBASE_UID (ou env STARGATE_OWNER_UID).\n")
        return 2

    now = datetime.now(timezone.utc).isoformat()
    existing = get_app_by_id_db(app["id"])

    app["uid"] = args.uid
    app["private"] = True
    app["approved"] = True          # app pessoal: dispensa revisão
    app["status"] = "approved"
    app["created_at"] = (existing or {}).get("created_at", now)
    app.setdefault("installs", 0)
    app.setdefault("rating_avg", 0)
    app.setdefault("rating_count", 0)

    upsert_app_to_db(app)
    verb = "atualizado" if existing else "instalado"
    print(f"[stargate] {verb}: {app['id']} (dono uid={args.uid})")
    print("[stargate] Abra o Omi > Apps > (privados) e ative 'Stargate — Copiloto Pessoal'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
