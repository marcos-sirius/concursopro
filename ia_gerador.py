"""
Geração e revisão de questões via API da OpenAI (Versão 3 - LLMOps).

Fluxos implementados:
1. Geração (GPT) com exigência de duplo-fator no gabarito (índice + letra).
2. Sanitização (Python) limpando A), B), C) das alternativas via Regex.
3. Validação Lógica (Python) confirmando se o índice bate com a letra defendida.
4. Revisão Individual (GPT) focada e isolada por questão para evitar alucinações.
5. Loop de Substituição: rejeitadas não entram; substitutas são geradas até bater a meta.
6. Balanceamento e persistência mantidos.
"""

import json
import os
import random
import time
import re

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

# ============================================================
# CONFIGURAÇÃO
# ============================================================

load_dotenv()

MODEL = "gpt-5.6-terra"
MAX_TENTATIVAS = 5 # Aumentado para suportar o loop de substituição
MAX_SUBSTITUTAS = 2

# Marcador literal que a IA deve escrever no comentário no lugar da letra da
# alternativa correta. Como a ordem das alternativas só é decidida DEPOIS
# (em balancear_gabaritos), pedir a letra de verdade deixaria o comentário
# desatualizado assim que a questão fosse embaralhada. Em vez disso, a IA
# escreve esse marcador fixo, e o Python faz um simples replace() pela letra
# final — sem gastar nenhuma chamada extra de API.
MARCADOR_CORRETA = "[[LETRA_CORRETA]]"

api_key = os.environ.get("OPENAI_API_KEY")

if not api_key:
    raise RuntimeError(
        "OPENAI_API_KEY não encontrada. "
        "Configure a variável OPENAI_API_KEY no ambiente "
        "ou nos Secrets do Streamlit."
    )

client = OpenAI(api_key=api_key)

# ============================================================
# SCHEMAS
# ============================================================

SCHEMA_QUESTOES = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "questoes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "pergunta": {"type": "string"},
                    "opcoes": {
                        "type": "array",
                        "items": {"type": "string"},
                        # OBS: "minItems"/"maxItems" foram removidos daqui de
                        # propósito. O modo strict:true da OpenAI NÃO suporta
                        # essas palavras-chave — incluí-las faz a API rejeitar
                        # a chamada inteira com erro 400 "Invalid schema" em
                        # TODAS as tentativas, sem exceção (foi exatamente o
                        # bug que causava "Falhou ao gerar" em tudo). A
                        # validação de "exatamente 5 alternativas" já é feita
                        # em Python por _validar_estrutura_questao(), então
                        # nada de segurança se perde.
                    },
                    "correta": {
                        "type": "integer",
                        # mesma observação: "minimum"/"maximum" também não são
                        # suportados em strict mode. A checagem 0<=correta<=4
                        # já é garantida indiretamente por _gabarito_consistente()
                        # (compara o índice com a letra do enum abaixo).
                    },
                    "letra_gabarito": {
                        "type": "string",
                        "enum": ["A", "B", "C", "D", "E"]
                    },
                    "comentario": {"type": "string"},
                },
                "required": [
                    "pergunta",
                    "opcoes",
                    "correta",
                    "letra_gabarito",
                    "comentario",
                ],
            },
        }
    },
    "required": ["questoes"],
}

# Modificado para avaliar uma questão por vez
SCHEMA_REVISAO = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {
            "type": "string",
            "enum": [
                "aprovada",
                "corrigir_gabarito",
                "rejeitar",
            ],
        },
        "correta": {
            "type": "integer",
            # "minimum"/"maximum" removidos — não suportados em strict mode
            # (ver comentário equivalente em SCHEMA_QUESTOES acima).
        },
        "letra_gabarito": {
            "type": "string",
            "enum": ["A", "B", "C", "D", "E"]
        },
        "comentario": {
            "type": "string"
        },
        "motivo": {
            "type": "string"
        },
    },
    "required": [
        "status",
        "correta",
        "letra_gabarito",
        "comentario",
        "motivo",
    ],
}

# ============================================================
# LÓGICA DE VALIDAÇÃO PYTHON (JUIZ)
# ============================================================

def _gabarito_consistente(correta_idx: int, letra: str) -> bool:
    """Verifica se o índice numérico corresponde à letra declarada."""
    mapa = {0: 'A', 1: 'B', 2: 'C', 3: 'D', 4: 'E'}
    return mapa.get(correta_idx) == letra.upper()

def _limpar_letras_alternativas(texto: str) -> str:
    """Remove A), b-, C. etc do início do texto[cite: 1]."""
    return re.sub(r'^([A-Ea-e][\)\.-]\s*)', '', texto).strip()

# ============================================================
# PROMPT DE GERAÇÃO
# ============================================================

def montar_prompt(banca: str, nivel: str, tema: str, qtd: int, evitar: list = None) -> str:
    bloco_evitar = ""
    if evitar:
        evitar_recentes = evitar[-100:]
        lista = "\n".join(f"- {p}" for p in evitar_recentes)
        bloco_evitar = f"\nNÃO repita nem crie variações óbvias das perguntas abaixo:\n{lista}\n"

    return f"""
Atue como um Examinador de Elite especializado em concursos públicos.
Gere exatamente {qtd} questão(ões) de múltipla escolha.

Banca: {banca}
Nível: {nivel}
Tema: {tema}

REGRAS OBRIGATÓRIAS:
1. Cada questão deve possuir exatamente 5 alternativas.
2. Deve existir exatamente UMA alternativa correta.
3. Não repita questões já utilizadas.
4. Em questões de lógica ou matemática, resolva o problema antes de escolher a resposta.
5. O campo "correta" deve apontar para a alternativa certa: 0=A, 1=B, 2=C, 3=D, 4=E.
6. O campo "letra_gabarito" DEVE ser a letra correspondente ao índice (A, B, C, D ou E).
7. Sempre que o comentário precisar se referir à alternativa correta PELA LETRA,
   use OBRIGATORIAMENTE o texto literal {MARCADOR_CORRETA} no lugar da letra —
   NUNCA escreva a letra de verdade (ex: NÃO escreva "a alternativa C está
   correta"; escreva "a alternativa {MARCADOR_CORRETA} está correta"). Isso é
   obrigatório porque a ordem das alternativas é reorganizada DEPOIS de gerada
   a questão, e o Python substitui esse marcador pela letra final automaticamente.
   Fora isso, pode explicar o raciocínio normalmente, inclusive citando o
   conteúdo de outras alternativas (sem citar a letra delas).
8. NÃO escreva a letra da alternativa dentro do texto da opção. Gere apenas o conteúdo da alternativa.

{bloco_evitar}
"""

# ============================================================
# PROMPT DE REVISÃO INDIVIDUAL
# ============================================================

def montar_prompt_revisao_individual(questao: dict, banca: str, nivel: str, tema: str) -> str:
    questao_json = json.dumps(questao, ensure_ascii=False, indent=2)

    return f"""
Você é o REVISOR FINAL de um banco de questões de concursos públicos.
Sua função é auditar ESTA ÚNICA QUESTÃO antes que ela seja liberada.

Contexto da Prova:
Banca: {banca}
Nível: {nivel}
Tema: {tema}

QUESTÃO PARA AUDITAR:
{questao_json}

Verifique obrigatoriamente:
1. Se a pergunta está bem formulada.
2. Se existe exatamente UMA alternativa inequivocamente correta.
3. Se a alternativa indicada em "correta" e "letra_gabarito" está correta.
4. Refaça cálculos e validações lógicas do zero.
5. O comentário contradiz o gabarito?
6. O comentário cita uma LETRA de verdade (A, B, C, D ou E) em vez do marcador
   "{MARCADOR_CORRETA}"? Se sim, isso é um ERRO — substitua a letra pelo
   marcador literal "{MARCADOR_CORRETA}" no texto do comentário. NUNCA remova
   o marcador nem o troque por uma letra — ele é resolvido pelo Python depois,
   após a ordem final das alternativas ser decidida.

- "aprovada": Questão impecável, gabarito 100% correto e comentário usando o marcador (não uma letra) para se referir à resposta certa.
- "corrigir_gabarito": A questão é boa, mas o gerador errou o índice/letra ou o comentário (incluindo citação de letra no texto). Corrija-os.
- "rejeitar": Questão ambígua, cálculo errado, múltiplas corretas ou sem resposta.

Lembre-se: Você deve preencher "correta" (0 a 4) e "letra_gabarito" (A a E) sempre de forma coerente entre si.
"""

# ============================================================
# NÚCLEO DE REQUISIÇÃO
# ============================================================

def _responder_json(prompt: str, schema: dict, nome_schema: str, reasoning_effort: str = "medium") -> dict:
    response = client.responses.create(
        model=MODEL,
        reasoning={"effort": reasoning_effort},
        input=prompt,
        max_output_tokens=8000,
        text={
            "format": {
                "type": "json_schema",
                "name": nome_schema,
                "strict": True,
                "schema": schema,
            }
        },
    )
    texto = response.output_text
    if not texto: raise ValueError("Resposta vazia.")
    try:
        return json.loads(texto)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON inválido: {e}") from e

# ============================================================
# TRATAMENTO E VALIDAÇÃO DA QUESTÃO
# ============================================================

def _validar_estrutura_questao(q: dict) -> None:
    opcoes = q.get("opcoes", [])
    if len(opcoes) != 5:
        raise ValueError("A questão precisa possuir exatamente 5 alternativas.")
    
    normalizadas = [" ".join(opcao.strip().lower().split()) for opcao in opcoes]
    if len(set(normalizadas)) != 5:
        raise ValueError("Existem alternativas repetidas.")

def _normalizar_questao(q: dict) -> dict:
    _validar_estrutura_questao(q)
    
    # 1. Limpeza automática das alternativas (Regex)
    opcoes_limpas = [_limpar_letras_alternativas(op) for op in q["opcoes"]]
    
    return {
        "pergunta": q["pergunta"].strip(),
        "opcoes": opcoes_limpas,
        "correta": int(q["correta"]),
        "letra_gabarito": q["letra_gabarito"].strip().upper(),
        "comentario": q["comentario"].strip(),
    }

# ============================================================
# AUDITORIA INDIVIDUAL
# ============================================================

def _revisar_questao_individual(questao: dict, banca: str, nivel: str, tema: str) -> dict:
    prompt = montar_prompt_revisao_individual(questao, banca, nivel, tema)
    
    revisao = _responder_json(
        prompt=prompt,
        schema=SCHEMA_REVISAO,
        nome_schema="revisao_individual",
        reasoning_effort="high", # Aumentado para high na auditoria fina
    )

    status = revisao.get("status")
    motivo = revisao.get("motivo", "")

    if status == "rejeitar":
        print(f"  [REVISÃO] ❌ Rejeitada: {motivo}")
        return None

    # Validação Python do Revisor
    if not _gabarito_consistente(revisao["correta"], revisao["letra_gabarito"]):
        print(f"  [REVISÃO] ❌ Rejeitada: Revisor falhou na coerência índice ({revisao['correta']}) vs letra ({revisao['letra_gabarito']}).")
        return None

    q_final = dict(questao)
    q_final["correta"] = revisao["correta"]
    q_final["letra_gabarito"] = revisao["letra_gabarito"]
    q_final["comentario"] = revisao.get("comentario", "").strip()
    
    return q_final

# ============================================================
# FLUXO PRINCIPAL COM LOOP DE SUBSTITUIÇÃO
# ============================================================

def gerar_questoes(banca: str, nivel: str, tema: str, qtd: int, evitar: list = None) -> list:
    if qtd <= 0: return []
    evitar = list(evitar) if evitar else []
    
    aprovadas_finais = []
    tentativa_global = 1

    while len(aprovadas_finais) < qtd and tentativa_global <= MAX_TENTATIVAS:
        faltam = qtd - len(aprovadas_finais)
        print(f"\n[{tema}] Geração {tentativa_global}/{MAX_TENTATIVAS}. Buscando {faltam} questão(ões)...")
        
        try:
            dados = _responder_json(
                prompt=montar_prompt(banca, nivel, tema, faltam, evitar),
                schema=SCHEMA_QUESTOES,
                nome_schema="geracao_questoes",
                reasoning_effort="medium"
            )
            
            questoes_brutas = dados.get("questoes", [])
            
            for q_bruta in questoes_brutas:
                if len(aprovadas_finais) >= qtd: break
                
                try:
                    q_norm = _normalizar_questao(q_bruta)
                    
                    # Validação inicial do Python sobre o Gerador
                    if not _gabarito_consistente(q_norm["correta"], q_norm["letra_gabarito"]):
                        print(f"  [GERAÇÃO] ⚠️ Questão descartada (Gerador incoerente).")
                        continue

                    # Auditoria Individual Isolada
                    q_revisada = _revisar_questao_individual(q_norm, banca, nivel, tema)
                    
                    if q_revisada:
                        print("  [REVISÃO] ✓ Questão blindada e aprovada.")
                        aprovadas_finais.append(q_revisada)
                        evitar.append(q_revisada["pergunta"])

                except Exception as e:
                    print(f"  [ERRO ESTRUTURAL] Questão descartada: {e}")
                    
            tentativa_global += 1
            
        except Exception as e:
            print(f"[{tema}] ❌ ERRO de API na tentativa {tentativa_global}: {e}")
            tentativa_global += 1
            time.sleep(5)

    if len(aprovadas_finais) < qtd:
        print(f"[{tema}] ⚠️ Concluído parcialmente: {len(aprovadas_finais)}/{qtd}.")
    else:
        print(f"[{tema}] ✓ Lote concluído com sucesso: {qtd}/{qtd}.")
        
    return aprovadas_finais

# ============================================================
# FUNÇÕES MANTIDAS INTACTAS
# ============================================================

def gerar_bloco(banca: str, nivel: str, tema: str, qtd: int, evitar: list = None, tamanho_bloco: int = 10) -> list:
    """Mesma lógica original de segmentação[cite: 1]."""
    evitar = list(evitar) if evitar else []
    todas_questoes = []
    if qtd <= 0: return todas_questoes
    tamanho_bloco = max(tamanho_bloco, 1)

    blocos = [tamanho_bloco] * (qtd // tamanho_bloco)
    if qtd % tamanho_bloco: blocos.append(qtd % tamanho_bloco)

    for i, qtd_bloco in enumerate(blocos, 1):
        sufixo_tema = f"{tema} (Bloco {i}/{len(blocos)})" if len(blocos) > 1 else tema
        print(f"\n[{tema}] Iniciando bloco {i}/{len(blocos)} ({qtd_bloco} q)...")
        questoes = gerar_questoes(banca, nivel, sufixo_tema, qtd_bloco, evitar)
        todas_questoes.extend(questoes)
        evitar.extend(q.get("pergunta", "") for q in questoes if q.get("pergunta"))

    return todas_questoes

def _comentario_cita_letra(comentario: str) -> bool:
    """
    Detecta se o texto do comentário ainda menciona uma letra de alternativa
    (ex: "alternativa C", "opção B") — sinal de que a IA não seguiu a regra
    de não citar letras, e que o texto vai ficar desatualizado após o
    embaralhamento de posições feito por balancear_gabaritos().
    """
    padrao = re.compile(r'\b(alternativa|op[cç][aã]o|item|letra)\s+[A-E]\b', re.IGNORECASE)
    return bool(padrao.search(comentario or ""))


def _embaralhar_questao(q: dict, posicao_alvo: int):
    """Reposiciona a alternativa correta mantendo o contrato original[cite: 1]."""
    opcoes = list(q["opcoes"])
    texto_correta = opcoes.pop(q["correta"])
    random.shuffle(opcoes)
    opcoes.insert(posicao_alvo, texto_correta)
    q["opcoes"] = opcoes
    q["correta"] = posicao_alvo
    # Atualiza a letra referencial pós-embaralhamento
    mapa = {0: 'A', 1: 'B', 2: 'C', 3: 'D', 4: 'E'}
    letra_final = mapa[posicao_alvo]
    q["letra_gabarito"] = letra_final

    # Resolve o marcador {MARCADOR_CORRETA} pela letra FINAL, já com a
    # posição definitiva — é aqui que a mágica acontece, em Python puro,
    # sem gastar nenhuma chamada extra de API.
    comentario = q.get("comentario", "") or ""
    if MARCADOR_CORRETA in comentario:
        q["comentario"] = comentario.replace(MARCADOR_CORRETA, letra_final)
    elif _comentario_cita_letra(comentario):
        # Rede de segurança: a IA escapou da regra e escreveu uma letra de
        # verdade em vez do marcador. Não corrigimos automaticamente (arriscado
        # — o comentário pode citar várias letras, inclusive de distratores),
        # só avisamos nos logs pra você saber que essa questão específica
        # precisa de revisão manual.
        pergunta_resumida = (q.get("pergunta", "") or "")[:60]
        print(
            f"  ⚠️ [ATENÇÃO] Comentário citou uma letra literal em vez do "
            f"marcador {MARCADOR_CORRETA} — pode estar desatualizado após o "
            f"embaralhamento. Pergunta: \"{pergunta_resumida}...\""
        )

def balancear_gabaritos(questoes: list) -> list:
    """Distribui uniformemente as respostas entre A-E[cite: 1]."""
    n = len(questoes)
    if n == 0: return questoes
    posicoes = [i % 5 for i in range(n)]
    random.shuffle(posicoes)
    for q, pos in zip(questoes, posicoes):
        _embaralhar_questao(q, pos)
    return questoes
