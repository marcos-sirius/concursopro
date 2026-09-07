"""
Utilitário local (rode no SEU computador, não sobe pro GitHub): lê o .json
da Service Account baixado do Google Cloud Console e imprime o bloco TOML
pronto pra colar nos Secrets do Streamlit, já com o private_key formatado
corretamente (em uma linha só, com \n escapado).

Uso:
    python converter_secrets.py caminho/para/sua-chave.json
"""
import json
import sys

if len(sys.argv) != 2:
    print("Uso: python converter_secrets.py caminho/para/sua-chave.json")
    sys.exit(1)

with open(sys.argv[1], encoding="utf-8") as f:
    dados = json.load(f)

print("[google_service_account]")
for chave, valor in dados.items():
    valor_escapado = valor.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    print(f'{chave} = "{valor_escapado}"')
