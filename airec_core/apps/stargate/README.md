# Stargate — Copiloto Pessoal (app privado do Omi)

Área do Omi que conduz roteiros curtos de operação humana derivados dos manuais Stargate
(vault `jamjoy-core/stargate/`). Ajuda com: **foco, decisão, ansiedade, procrastinação,
craving de cigarro, autoconfiança e reconhecimento próprio** — e mantém um log de vitórias
e calibração de confiança.

## Arquivos
- `app.json` — definição do app (persona/chat_prompt, memory_prompt, capabilities). Validado contra `models.app.App`.
- `install_stargate_app.py` — instala/atualiza o app no Firestore (idempotente, por id).

## Instalar
Precisa do seu **Firebase UID** (o mesmo da sua conta no app Omi).

```bash
cd ~/omi/backend
# com o venv/env do backend ativo (mesmo usado para rodar main.py):
python ../airec_core/apps/stargate/install_stargate_app.py --uid SEU_FIREBASE_UID
```

Depois: abra o **Omi → Apps**, encontre **"Stargate — Copiloto Pessoal"** (privado) e **ative**.

## Atualizar
Edite `app.json` e rode o mesmo comando — o mesmo id é sobrescrito.

## Remover
```bash
python ../airec_core/apps/stargate/install_stargate_app.py --uninstall
```

## O que o app faz
- **chat**: conversa conduzindo os roteiros (início do dia, foco 50min, decisão, craving, fim do dia).
- **memories**: extrai das suas gravações vitórias, decisões+confiança e cravings atravessados → alimenta o log.
- **proactive_notification**: pode te cutucar no momento certo (ex.: sinais de sobrecarga → sugere o roteiro).

## Princípios embutidos (no prompt)
- Honestidade: o efeito vem de foco/respiração/estrutura, não de misticismo.
- Apoio, não tratamento: cigarro/vício sempre com lembrete de tratamento de base (SUS / Disque Saúde 136).
- Passos pequenos (anti-procrastinação) e reconhecimento por evidência (autoconfiança).

Fonte dos roteiros: `~/ObsidianVaults/jamjoy-core/stargate/roteiros-omi-por-dificuldade.md`
