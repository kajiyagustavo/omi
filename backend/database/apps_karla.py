"""Apps sobre a MEMÓRIA UNIFICADA (Karla) — shim PARCIAL de database/apps.py.

F4.5 (Omi self-hosted): SÓ as funções de leitura/escrita do doc CANÔNICO de app
(`plugins_data`, doc_id = app id) saem do Firestore e passam a morar no
doc-store genérico do memory-service (via `omi_docs_karla`), na coleção
allowlisted `apps` (doc_id = app id, `dados` = o doc de `plugins_data` — mesmo
formato, com `id` sempre presente em `dados`, como no original).

Ativação por env `CONFIG_KARLA=true` (mesma flag de `users_karla.py` — ver
`is_enabled()` aqui e o rodapé de `database/apps.py`); com a flag off, nada
muda (rollback = desligar).

⚠️ SHIM PARCIAL — só as funções abaixo são substituídas. O resto do módulo
`apps.py` (reviews, testers, usage_history, personas, aprovação/moderação,
api_keys de app, payment) CONTINUA no Firestore e NÃO é rebindado no rodapé:
não há coleção equivalente allowlisted na Karla para essas subcoleções, e
migrar isso está fora do escopo desta task.

Funções shimadas aqui:
  - get_app_by_id_db          → GET por id, com o MESMO cache Redis em volta
                                  (get_app_cache_by_id/set_app_cache_by_id) do
                                  original — só a leitura de "cache miss" muda
                                  de Firestore para `k.obter('apps', app_id)`.
  - get_public_approved_apps_db, get_private_apps_db, search_apps_db
                                → `k.listar('apps', limite=1000)` (busca tudo)
                                  + filtro CLIENT-SIDE replicando exatamente a
                                  semântica de filtro do original. CLIENT-SIDE
                                  POR SINGLE-USER: aceitável porque o doc-store
                                  genérico não expõe query composta equivalente
                                  ao Firestore `where(AND(...))` — mesmo trade-
                                  off que os demais shims Karla (ver
                                  `omi_docs_karla.listar`, que não filtra).
  - add_app_to_db, update_app_in_db, delete_app_from_db,
    update_app_visibility_in_db, set_app_popular_db
                                → CRUD usado pelo router de apps privados
                                  (`routers/apps.py`) e por `utils/apps.py`.
                                  Upsert/patch/delete em `apps` (doc_id = id),
                                  preservando as invalidações de cache Redis
                                  (`delete_app_cache_by_id`) que os call-sites
                                  já fazem — aqui replicamos a MESMA invalidação
                                  que o original faz implicitamente via TTL/
                                  explicit calls nos routers, mais uma
                                  invalidação defensiva no próprio shim de
                                  escrita, pra nunca servir stale após um write
                                  direto por esta função.

Filosofia de erro: herdada do mold — erros de rede logam (warning+sanitize no
cliente `omi_docs_karla`) e a função devolve o DEFAULT/NEUTRO do ORIGINAL por
função (None / [] / False), nunca raise, pra não derrubar o app se a Karla
estiver indisponível (fail-open).
"""

import logging
import os

from ulid import ULID

from database import omi_docs_karla as k
from database.redis_db import get_app_cache_by_id, set_app_cache_by_id, delete_app_cache_by_id

logger = logging.getLogger(__name__)

_APPS = "apps"


def is_enabled() -> bool:
    return (
        os.getenv("CONFIG_KARLA", "").lower() in ("1", "true", "yes")
        and bool(os.getenv("MEMORIA_UNIFICADA_URL"))
        and bool(os.getenv("MEMORIA_UNIFICADA_TOKEN"))
    )


# ****************************** GET BY ID (com cache) ******************************


def get_app_by_id_db(app_id: str):
    """Idêntico ao original na dança de cache: tenta o cache Redis primeiro
    (`get_app_cache_by_id`); em miss, busca na Karla (`k.obter('apps', app_id)`)
    e, se achar, popula o cache (`set_app_cache_by_id`) antes de devolver.
    Devolve None se ausente (doc não existe ou erro de rede — fail-open)."""
    cached_app = get_app_cache_by_id(app_id)
    if cached_app:
        return cached_app

    app = k.obter(_APPS, app_id)
    if app:
        set_app_cache_by_id(app_id, app)
        return app
    return None


# ****************************** LISTAGENS (client-side) ******************************


def _list_all_apps() -> list:
    """Busca todos os docs da coleção `apps` (limite alto — client-side por
    single-user, ver docstring do módulo). [] em erro de rede (fail-open,
    herdado de `omi_docs_karla.listar`)."""
    return k.listar(_APPS, limite=1000)


def get_private_apps_db(uid: str) -> list:
    """Replica o filtro original: uid == uid AND private == True.
    Client-side por single-user."""
    apps = _list_all_apps()
    return [app for app in apps if app.get('uid') == uid and app.get('private') is True]


def get_public_approved_apps_db() -> list:
    """Replica o filtro original: approved == True AND private == False.
    Client-side por single-user."""
    apps = _list_all_apps()
    return [app for app in apps if app.get('approved') is True and app.get('private') is False]


def search_apps_db(
    uid: str,
    category: str | None = None,
    capability: str | None = None,
    my_apps: bool = False,
    installed_apps: bool = False,
    enabled_app_ids: list[str] | None = None,
) -> list:
    """Replica EXATAMENTE a semântica de filtro de `database.apps.search_apps_db`,
    mas buscando tudo da coleção `apps` e filtrando client-side (o doc-store
    genérico da Karla não expõe query composta equivalente ao Firestore
    `where(AND(...))`, então não há "nível de banco" real aqui — client-side
    por single-user, documentado no módulo).

    Args:
        uid: User ID para apps privados e filtragem
        category: Filtro por category id
        capability: Filtro por capability id
        my_apps: Só devolve os apps do próprio usuário
        installed_apps: Só devolve os apps habilitados do usuário
        enabled_app_ids: Lista pré-buscada de app ids habilitados (para o filtro installed_apps)

    Returns:
        Lista de dicts de app batendo os filtros.
    """
    # Short-circuit ANTES de buscar (replica o original: com installed_apps
    # sem enabled_app_ids, o Firestore nunca chega a montar/rodar a query).
    if installed_apps and (not enabled_app_ids or len(enabled_app_ids) == 0):
        # Usuário não tem apps habilitados.
        return []

    apps = _list_all_apps()

    # 1. Filtro mais restritivo primeiro (mesma ordem do original).
    if my_apps:
        apps = [app for app in apps if app.get('uid') == uid]

    elif installed_apps:
        if len(enabled_app_ids) > 30:
            # Firestore limitava 'in' a 30 itens — aqui não há esse limite real
            # (client-side), mas replicamos o MESMO caminho do original: query
            # base = approved+public, depois soma os apps do próprio usuário.
            base = [app for app in apps if app.get('approved') is True and app.get('private') is False]
            enabled_set = set(enabled_app_ids)
            base = [app for app in base if app.get('id') in enabled_set]

            user_apps = [app for app in apps if app.get('uid') == uid]
            existing_ids = {app.get('id') for app in base}
            for user_app in user_apps:
                if user_app.get('id') in enabled_set and user_app.get('id') not in existing_ids:
                    base.append(user_app)

            apps = base
        else:
            # Query por ids específicos.
            enabled_set = set(enabled_app_ids)
            apps = [app for app in apps if app.get('id') in enabled_set]

    else:
        # Default: apps públicos aprovados.
        apps = [app for app in apps if app.get('approved') is True and app.get('private') is False]

    # 2. Filtro de category (não aplica se já filtrando por my_apps — replica
    #    o original: category/capability de my_apps são pós-filtro abaixo).
    if category and not my_apps:
        apps = [app for app in apps if app.get('category') == category]

    # 3. Filtro de capability.
    if capability and not my_apps:
        apps = [app for app in apps if capability in (app.get('capabilities') or [])]

    # Pós-filtro de category se my_apps estiver ligado.
    if my_apps and category:
        apps = [app for app in apps if app.get('category') == category]

    # Pós-filtro de capability se my_apps estiver ligado.
    if my_apps and capability:
        apps = [app for app in apps if capability in (app.get('capabilities') or [])]

    return apps


# ****************************** CRUD (escrita) ******************************


def add_app_to_db(app_data: dict):
    """Upsert do doc `apps/<id>` (equivalente ao `.add(app_data, app_data['id'])`
    do original — mesmo doc_id determinístico). `dados` carrega `id` (já vem
    setado em `app_data['id']` pelos call-sites do router, como no original)."""
    k.upsert(_APPS, app_data['id'], app_data)
    delete_app_cache_by_id(app_data['id'])


def update_app_in_db(app_data: dict):
    """PATCH raso do doc `apps/<id>` (chaves de topo — equivalente ao
    `.update(app_data)` do original quando `app_data` só tem chaves de topo,
    como os call-sites fazem via `model_dump(exclude_unset=True)`). Invalida o
    cache Redis do app, como os call-sites do router já fazem explicitamente
    após updates — replicado aqui defensivamente para nunca servir stale."""
    k.patch(_APPS, app_data['id'], app_data)
    delete_app_cache_by_id(app_data['id'])


def delete_app_from_db(app_id: str):
    """Deleta o doc `apps/<app_id>` e invalida o cache Redis correspondente."""
    k.deletar(_APPS, app_id)
    delete_app_cache_by_id(app_id)


def update_app_visibility_in_db(app_id: str, private: bool):
    """Replica o original: ao tornar um app 'private-*' público, o doc é
    RECRIADO sob um novo id (`<base>-<ULID>`) — o app privado nasce com um id
    sufixado `-private` e, ao publicar, ganha um id definitivo novo. Fail-open:
    se o doc não existir na Karla, não faz nada (mesma semântica do
    `.get().to_dict()` do original, que levantaria erro só se doc inexistente
    — aqui simplesmente no-op silencioso, pois `k.obter` devolve None)."""
    if 'private' in app_id and not private:
        app = k.obter(_APPS, app_id)
        if app is None:
            return
        k.deletar(_APPS, app_id)
        delete_app_cache_by_id(app_id)

        new_app_id = app_id.split('-private')[0] + '-' + str(ULID())
        app['id'] = new_app_id
        app['private'] = private
        k.upsert(_APPS, new_app_id, app)
        delete_app_cache_by_id(new_app_id)
    else:
        k.patch(_APPS, app_id, {'private': private})
        delete_app_cache_by_id(app_id)


def set_app_popular_db(app_id: str, popular: bool):
    """PATCH raso de `is_popular`. Invalida o cache Redis do app."""
    k.patch(_APPS, app_id, {'is_popular': popular})
    delete_app_cache_by_id(app_id)
