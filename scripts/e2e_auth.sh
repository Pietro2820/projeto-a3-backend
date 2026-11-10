#!/usr/bin/env bash
# Teste E2E da autenticação — roda contra a API real (uvicorn) com o
# Supabase falso por trás (mesma interface PostgREST que o supabase-py usa).
set -u
API="http://localhost:8000"
PASS=0; FAIL=0

check() { # check <nome> <esperado> <obtido> [extra]
  if [ "$2" = "$3" ]; then PASS=$((PASS+1)); echo "✔ $1  (HTTP $3) $4"
  else FAIL=$((FAIL+1)); echo "✘ $1  — esperado $2, obtido $3 $4"; fi
}

SUFIXO=$RANDOM$RANDOM
EMAIL_NOVO="joana.silva${SUFIXO}@exemplo.com"
SENHA="s3nh4-Sup3rSegura"

echo "==================== 1. CADASTRO ===================="
COD=$(curl -s -o /tmp/resp.json -w "%{http_code}" -X POST "$API/cadastro" -H "Content-Type: application/json" \
  -d "{\"nome\":\"Joana Silva\",\"email\":\"${EMAIL_NOVO}\",\"senha\":\"${SENHA}\"}")
check "POST /cadastro cria conta" 201 "$COD" "$(python3 -c "import json;d=json.load(open('/tmp/resp.json'));print('| usuario:',d['usuario']['email'],'| nome:',d['usuario']['nome'],'| mensagem:',d['mensagem'])")"

echo
echo "==================== 2. SENHA CRIPTOGRAFADA NO BANCO ===================="
python3 - "$EMAIL_NOVO" <<'PY'
import json, sys, urllib.request
rows = json.load(urllib.request.urlopen("http://localhost:8902/__dump"))
row = next(r for r in rows if r["email"] == sys.argv[1])
h = row["senha_hash"]
print("  linha gravada:", json.dumps({k: (v if k != 'senha_hash' else h[:28] + '...') for k, v in row.items()}, ensure_ascii=False))
assert h.startswith("pbkdf2_sha256$120000$"), "hash fora do formato!"
assert sys.argv[1] not in h, "SENHA EM TEXTO PLANO NO BANCO!!!"
print(f"  ✔ senha_hash = {h[:38]}...  → PBKDF2-SHA256, 120.000 iterações, salt próprio — NÃO é a senha em claro")
PY

echo
echo "==================== 3. LOGIN CORRETO ===================="
COD=$(curl -s -o /tmp/resp.json -w "%{http_code}" -X POST "$API/login" -H "Content-Type: application/json" \
  -d "{\"email\":\"${EMAIL_NOVO}\",\"senha\":\"${SENHA}\"}")
check "POST /login com senha certa" 200 "$COD" "$(python3 -c "import json;d=json.load(open('/tmp/resp.json'));print('| mensagem:',d['mensagem'])")"

echo
echo "==================== 4. LOGIN COM SENHA ERRADA ===================="
COD=$(curl -s -o /tmp/resp.json -w "%{http_code}" -X POST "$API/login" -H "Content-Type: application/json" \
  -d "{\"email\":\"${EMAIL_NOVO}\",\"senha\":\"SenhaErrada123\"}")
MSG=$(python3 -c "import json;print(json.load(open('/tmp/resp.json'))['detail'])")
check "POST /login com senha errada" 401 "$COD" "| mensagem: $MSG"

echo
echo "==================== 5. EMAIL INEXISTENTE (anti-enumeração) ===================="
COD=$(curl -s -o /tmp/resp.json -w "%{http_code}" -X POST "$API/login" -H "Content-Type: application/json" \
  -d "{\"email\":\"nao.existe${SUFIXO}@exemplo.com\",\"senha\":\"qualquerCoisa123\"}")
MSG=$(python3 -c "import json;print(json.load(open('/tmp/resp.json'))['detail'])")
check "POST /login email inexistente" 401 "$COD" "| mensagem: $MSG"
[ "$MSG" = "E-mail ou senha incorretos." ] && { PASS=$((PASS+1)); echo "  ✔ mesma mensagem da senha errada — não revela quais emails existem"; } || { FAIL=$((FAIL+1)); echo "  ✘ mensagem difere da de senha errada (vazamento de enumeração)"; }

echo
echo "==================== 6. E-MAIL DUPLICADO ===================="
COD=$(curl -s -o /tmp/resp.json -w "%{http_code}" -X POST "$API/cadastro" -H "Content-Type: application/json" \
  -d "{\"nome\":\"Outra Joana\",\"email\":\"${EMAIL_NOVO}\",\"senha\":\"OutraSenha123\"}")
MSG=$(python3 -c "import json;print(json.load(open('/tmp/resp.json'))['detail'])")
check "POST /cadastro email duplicado" 409 "$COD" "| mensagem: $MSG"

echo
echo "==================== 7. VALIDAÇÕES (422) ===================="
COD=$(curl -s -o /tmp/resp.json -w "%{http_code}" -X POST "$API/cadastro" -H "Content-Type: application/json" \
  -d "{\"nome\":\"J\",\"email\":\"curta${SUFIXO}@exemplo.com\",\"senha\":\"${SENHA}\"}")
check "cadastro com nome de 1 letra" 422 "$COD" "| $(python3 -c "import json;print(json.load(open('/tmp/resp.json'))['detail'][0]['msg'])")"

COD=$(curl -s -o /tmp/resp.json -w "%{http_code}" -X POST "$API/cadastro" -H "Content-Type: application/json" \
  -d "{\"nome\":\"Ana Teste\",\"email\":\"email-sem-arroba${SUFIXO}\",\"senha\":\"${SENHA}\"}")
check "cadastro com email inválido" 422 "$COD" "| $(python3 -c "import json;print(json.load(open('/tmp/resp.json'))['detail'][0]['msg'])")"

COD=$(curl -s -o /tmp/resp.json -w "%{http_code}" -X POST "$API/cadastro" -H "Content-Type: application/json" \
  -d "{\"nome\":\"Ana Teste\",\"email\":\"ana${SUFIXO}@exemplo.com\",\"senha\":\"curta12\"}")
check "cadastro com senha de 7 caracteres" 422 "$COD" "| $(python3 -c "import json;print(json.load(open('/tmp/resp.json'))['detail'][0]['msg'])")"

COD=$(curl -s -o /tmp/resp.json -w "%{http_code}" -X POST "$API/login" -H "Content-Type: application/json" \
  -d "{\"email\":\"sem-arroba\",\"senha\":\"${SENHA}\"}")
check "login com email inválido" 422 "$COD" "| $(python3 -c "import json;print(json.load(open('/tmp/resp.json'))['detail'][0]['msg'])")"

echo
echo "==================== 8. NORMALIZAÇÃO DE EMAIL (maiúsculas) ===================="
COD=$(curl -s -o /tmp/resp.json -w "%{http_code}" -X POST "$API/login" -H "Content-Type: application/json" \
  -d "{\"email\":\"${EMAIL_NOVO^^}\",\"senha\":\"${SENHA}\"}")
check "login com email EM MAIÚSCULAS também funciona" 200 "$COD" "(gravação e busca normalizam para minúsculas)"

echo
echo "==================== 9. VERIFICAÇÃO PBKDF2 DIRETA ===================="
RAIZ_SCRIPT="$(cd "$(dirname "$0")/.." && pwd)"
python3 - "$RAIZ_SCRIPT" <<'PY'
import json, urllib.request, sys
sys.path.insert(0, sys.argv[1])
from app.auth.senhas import verificar_senha, gerar_hash
rows = json.load(urllib.request.urlopen("http://localhost:8902/__dump"))
row = next(r for r in rows if r["email"] == "teste@edustream.com")
assert verificar_senha("senha12345", row["senha_hash"]) is True
assert verificar_senha("errada12345", row["senha_hash"]) is False
assert verificar_senha("", row["senha_hash"]) is False
assert verificar_senha("senha12345", "hash-corrompido$#@") is False
h1, h2 = gerar_hash("mesma-senha-123"), gerar_hash("mesma-senha-123")
assert h1 != h2, "salt não é aleatório por usuário!"
assert verificar_senha("mesma-senha-123", h1) and verificar_senha("mesma-senha-123", h2)
print("  ✔ hash do usuário de teste valida com a senha certa e rejeita a errada")
print("  ✔ hash corrompido/vazio nunca autentica (falha segura)")
print("  ✔ salt aleatório: mesma senha gera hashes DIFERENTES (defesa contra rainbow table)")
PY

echo
echo "=========================================================="
echo "RESULTADO: $PASS ✔ / $FAIL ✘"
[ "$FAIL" = "0" ]
