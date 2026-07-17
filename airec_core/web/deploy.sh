#!/usr/bin/env bash
#
# deploy.sh — publica o front do Omi (aba Stargate etc.) do REPO para a Karla.
#
# Torna o repo a fonte única de verdade: edite airec_core/web/index.html, rode este
# script, e ele sincroniza para o nginx da Karla (servido em https://omi.elevare.club/web/).
#
# Fluxo seguro:
#   1. valida a sintaxe JS do index.html local (não publica arquivo quebrado)
#   2. compara com o que está no ar (mostra se o servidor divergiu do repo)
#   3. faz backup remoto com timestamp (convenção .bak-deploy-<data>)
#   4. envia atomicamente (escreve .new e faz mv) e confere bytes
#
# Uso (a partir de qualquer lugar):
#   ~/omi/airec_core/web/deploy.sh            # publica
#   ~/omi/airec_core/web/deploy.sh --dry-run  # só mostra o que faria (não escreve)
#   ~/omi/airec_core/web/deploy.sh --diff     # mostra o diff repo↔Karla e sai
#   ~/omi/airec_core/web/deploy.sh --pull     # traz a versão da Karla PARA o repo (reverso)
#
# Requer: acesso `ssh karla` e `node` no local.

set -euo pipefail

SSH_HOST="${OMI_KARLA_SSH:-karla}"
REMOTE_DIR="/root/docs/jamjoy-dashboard-financeiro/certbot/www/web"
REMOTE_FILE="$REMOTE_DIR/index.html"
LOCAL_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/index.html"
STAMP="$(date +%Y%m%d-%H%M%S)"

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_yel=$'\033[33m'; c_dim=$'\033[2m'; c_rst=$'\033[0m'
info(){ printf '%s\n' "$*"; }
ok(){   printf '%s✓%s %s\n' "$c_grn" "$c_rst" "$*"; }
warn(){ printf '%s!%s %s\n' "$c_yel" "$c_rst" "$*"; }
die(){  printf '%s✗ %s%s\n' "$c_red" "$*" "$c_rst" >&2; exit 1; }

MODE="deploy"
case "${1:-}" in
  --dry-run) MODE="dry" ;;
  --diff)    MODE="diff" ;;
  --pull)    MODE="pull" ;;
  "")        MODE="deploy" ;;
  *) die "flag desconhecida: $1 (use --dry-run | --diff | --pull)" ;;
esac

[ -f "$LOCAL_FILE" ] || die "não achei o arquivo local: $LOCAL_FILE"
command -v ssh >/dev/null || die "ssh não encontrado"

# --- modo pull: traz da Karla para o repo (reverso), com backup local ---
if [ "$MODE" = "pull" ]; then
  cp "$LOCAL_FILE" "$LOCAL_FILE.bak-prepull-$STAMP"
  ssh "$SSH_HOST" "cat $REMOTE_FILE" > "$LOCAL_FILE"
  ok "puxado da Karla → repo ($(wc -c < "$LOCAL_FILE") bytes). Backup: index.html.bak-prepull-$STAMP"
  info "${c_dim}Revise e commite no repo se estiver certo.${c_rst}"
  exit 0
fi

# --- 1. valida sintaxe JS do local (bloco <script> inline como ES module) ---
if command -v node >/dev/null; then
  node -e '
    const fs=require("fs");
    const html=fs.readFileSync(process.argv[1],"utf8");
    const m=[...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)];
    if(!m.length){ console.error("nenhum <script> inline encontrado"); process.exit(1); }
    fs.writeFileSync("/tmp/_omideploy.mjs", m.map(x=>x[1]).join("\n;\n"));
  ' "$LOCAL_FILE" || die "falha ao extrair o script do index.html"
  node --check /tmp/_omideploy.mjs 2>/dev/null || die "sintaxe JS inválida no index.html — abortado (nada foi enviado)"
  ok "sintaxe JS do index.html local OK"
else
  warn "node não encontrado — pulando validação de sintaxe (arrisca publicar JS quebrado)"
fi

# --- 2. compara local x remoto ---
REMOTE_TMP="$(mktemp)"; trap 'rm -f "$REMOTE_TMP"' EXIT
ssh "$SSH_HOST" "cat $REMOTE_FILE" > "$REMOTE_TMP" 2>/dev/null || die "não consegui ler o arquivo remoto (ssh $SSH_HOST ok?)"

if diff -q "$REMOTE_TMP" "$LOCAL_FILE" >/dev/null 2>&1; then
  ok "repo e Karla já estão idênticos — nada a fazer."
  exit 0
fi

info "${c_dim}Diferenças repo → Karla:${c_rst}"
diff --unified=1 "$REMOTE_TMP" "$LOCAL_FILE" | sed -n '1,40p' || true
LBYTES=$(wc -c < "$LOCAL_FILE"); RBYTES=$(wc -c < "$REMOTE_TMP")
info "${c_dim}local: ${LBYTES}b · remoto atual: ${RBYTES}b${c_rst}"

if [ "$MODE" = "diff" ]; then exit 0; fi
if [ "$MODE" = "dry" ]; then warn "--dry-run: NADA foi enviado."; exit 0; fi

# --- 3+4. backup remoto + envio atômico ---
ssh "$SSH_HOST" "cp $REMOTE_FILE $REMOTE_FILE.bak-deploy-$STAMP" \
  && ok "backup remoto: index.html.bak-deploy-$STAMP"

cat "$LOCAL_FILE" | ssh "$SSH_HOST" "cat > $REMOTE_FILE.new && mv $REMOTE_FILE.new $REMOTE_FILE"
NEW_RBYTES=$(ssh "$SSH_HOST" "wc -c < $REMOTE_FILE")
[ "$NEW_RBYTES" = "$LBYTES" ] || die "bytes divergiram após envio (local=$LBYTES remoto=$NEW_RBYTES) — verifique!"
ok "publicado na Karla ($NEW_RBYTES bytes). No ar em https://omi.elevare.club/web/"
info "${c_dim}nginx serve estático — sem restart necessário. Faça hard-refresh (Cmd+Shift+R) no browser.${c_rst}"
