"""
Geração de questões via API da OpenAI (ChatGPT), 5 alternativas (A-E),
no mesmo espírito do gerarBlocoIA / tentarGerarQuestoes do Apps Script.
"""
import json
import random
import time
import os
from openai import OpenAI, RateLimitError
from dotenv import load_dotenv

load_dotenv()  # lê o arquivo .env na raiz do projeto, se existir

MODEL = "gpt-5.6"  # alias sempre apontando para a versão mais recente pinada de GPT-5.6 (gpt-4o virou legado)
MAX_TENTATIVAS = 4

api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError(
        "OPENAI_API_KEY não encontrada. Crie um arquivo .env na raiz do projeto "
        "com a linha: OPENAI_API_KEY=sua_chave_aqui"
    )

client = OpenAI(api_key=api_key)


def montar_prompt(banca: str, nivel: str, tema: str, qtd: int, evitar: list = None) -> str:
    bloco_evitar = ""
    if evitar:
        lista = "\n".join(f"- {p}" for p in evitar)
        bloco_evitar = f"""
NÃO repita, nem crie variações óbvias (mesma pegadinha trocando só nomes/números) das
perguntas abaixo, já usadas em simulados anteriores sobre este mesmo eixo/tema:
{lista}
"""

    return f"""Atue como um Examinador de Elite. Gere exatamente {qtd} questões de múltipla escolha
para a banca {banca}, nível {nivel}, sobre: {tema}.

Cada questão deve ter 5 alternativas (A, B, C, D, E), apenas uma correta.
{bloco_evitar}
Responda em JSON, com um objeto contendo a chave "questoes", cujo valor é um
array no formato abaixo. Não inclua texto adicional nem markdown:
{{
  "questoes": [
    {{
      "pergunta": "...",
      "opcoes": ["texto A", "texto B", "texto C", "texto D", "texto E"],
      "correta": 0,
      "comentario": "O Distrator: ... | Lógica: ... | Flash-Card: ..."
    }}
  ]
}}"""


def _chamar_api(prompt: str):
    # response_format json_object: a OpenAI EXIGE que a raiz da resposta seja
    # um objeto {} (não aceita array [] solto) — por isso o prompt pede um
    # objeto com a chave "questoes", em vez do array direto de antes.
    # Isso blinda contra erros de formatação (texto extra, cercas de markdown
    # ```json```, JSON truncado) que antes exigiam o replace() manual abaixo.
    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    texto = resp.choices[0].message.content
    dados = json.loads(texto)
    return dados["questoes"]


ULTIMO_ERRO = None  # guarda a mensagem da última falha, para a UI poder exibi-la


def gerar_questoes(banca: str, nivel: str, tema: str, qtd: int, evitar: list = None) -> list:
    global ULTIMO_ERRO
    prompt = montar_prompt(banca, nivel, tema, qtd, evitar)

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            print(f"[{tema}] Tentativa {tentativa}/{MAX_TENTATIVAS}...")
            questoes = _chamar_api(prompt)
            print(f"[{tema}] ✓ {len(questoes)} questões geradas.")
            return questoes
        except RateLimitError:
            espera = 30 * tentativa
            print(f"[{tema}] Rate limit. Aguardando {espera}s...")
            time.sleep(espera)
        except Exception as e:
            ULTIMO_ERRO = str(e)
            print(f"[{tema}] Falha na tentativa {tentativa}: {e}")
            if tentativa < MAX_TENTATIVAS:
                time.sleep(15)

    print(f"[{tema}] FALHOU após {MAX_TENTATIVAS} tentativas.")
    return []


def gerar_bloco(banca: str, nivel: str, tema: str, qtd: int, evitar: list = None,
                 tamanho_bloco: int = 10) -> list:
    """
    Gera 'qtd' questões sempre em blocos de até 'tamanho_bloco' (padrão 10),
    para evitar que a IA gere conteúdo genérico/raso em eixos extensos
    (ex: 50 questões de uma vez). A cada bloco, alimenta o prompt com as
    perguntas já geradas (no histórico + nos blocos anteriores desta mesma
    rodada) para reduzir duplicação/repetição de padrão.
    """
    evitar = list(evitar) if evitar else []
    todas_questoes = []

    blocos = [tamanho_bloco] * (qtd // tamanho_bloco)
    resto = qtd % tamanho_bloco
    if resto:
        blocos.append(resto)

    for i, qtd_bloco in enumerate(blocos, 1):
        sufixo_tema = tema if len(blocos) == 1 else f"{tema} (Bloco {i}/{len(blocos)})"
        questoes = gerar_questoes(banca, nivel, sufixo_tema, qtd_bloco, evitar)

        if not questoes and qtd_bloco > 1:
            # fallback: tenta dividir o bloco problemático ao meio
            print(f"[{tema}] Bloco {i} falhou, tentando dividir em 2...")
            metade = qtd_bloco // 2
            p1 = gerar_questoes(banca, nivel, f"{sufixo_tema} (Parte 1)", metade, evitar)
            p2 = gerar_questoes(banca, nivel, f"{sufixo_tema} (Parte 2)", qtd_bloco - metade, evitar)
            questoes = p1 + p2

        todas_questoes.extend(questoes)
        # alimenta o "evitar" do próximo bloco com o que já foi gerado nesta rodada
        evitar.extend(q.get("pergunta", "") for q in questoes if q.get("pergunta"))

    if len(todas_questoes) < qtd:
        print(f"[{tema}] ⚠️ Geradas {len(todas_questoes)}/{qtd} questões (alguns blocos falharam).")

    # OBS: o balanceamento de gabaritos (balancear_gabaritos) NÃO é feito aqui.
    # Um tema isolado pode receber só 1-3 questões neste simulado, o que não dá
    # ciclo suficiente pra cobrir A-E direito. O balanceamento é feito depois,
    # em main.py, juntando TODAS as questões do eixo (todos os temas) antes de
    # gravar — assim cada eixo, no simulado, sai com o gabarito bem distribuído.

    return todas_questoes


def _embaralhar_questao(q: dict, posicao_alvo: int):
    """Reposiciona a alternativa correta para 'posicao_alvo' (0=A ... 4=E),
    embaralhando as demais alternativas nas posições restantes."""
    opcoes = list(q["opcoes"])
    texto_correta = opcoes.pop(q["correta"])
    random.shuffle(opcoes)
    opcoes.insert(posicao_alvo, texto_correta)
    q["opcoes"] = opcoes
    q["correta"] = posicao_alvo


def balancear_gabaritos(questoes: list) -> list:
    """
    Os modelos de IA tendem a concentrar a resposta correta sempre na mesma
    letra (normalmente A). Em vez de depender do prompt para corrigir isso
    (pouco confiável), embaralhamos as alternativas aqui, garantindo que o
    gabarito fique distribuído de forma equilibrada entre A-E em cada bloco
    de questões gerado.
    """
    n = len(questoes)
    if n == 0:
        return questoes

    # sequência balanceada de posições-alvo (0..4 repetido) embaralhada
    posicoes = [i % 5 for i in range(n)]
    random.shuffle(posicoes)

    for q, pos in zip(questoes, posicoes):
        _embaralhar_questao(q, pos)

    return questoes