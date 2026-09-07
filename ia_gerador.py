"""
Geração e revisão de questões via API da OpenAI.

Fluxo:
1. Gera as questões com GPT-5.6 Terra.
2. Valida a estrutura localmente.
3. Envia o bloco para uma segunda revisão da IA.
4. A revisão confere:
   - resposta correta;
   - existência de apenas uma alternativa correta;
   - coerência lógica/matemática/conceitual;
   - coerência entre gabarito e comentário.
5. Se o revisor puder corrigir apenas o gabarito/comentário,
   a questão é corrigida.
6. Se a questão for considerada inválida, ela é descartada e
   uma substituta é gerada.
7. Depois disso, o balanceamento A-E é feito normalmente.

Mantém o contrato público usado pelo restante da aplicação:
- gerar_questoes(...)
- gerar_bloco(...)
- balancear_gabaritos(...)
"""

import json
import os
import random
import time

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError


# ============================================================
# CONFIGURAÇÃO
# ============================================================

load_dotenv()

MODEL = "gpt-5.6-terra"
MAX_TENTATIVAS = 4

# Quantas rodadas de substituição serão tentadas quando o revisor
# considerar uma questão irrecuperável.
MAX_SUBSTITUTAS = 2

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
                        "minItems": 5,
                        "maxItems": 5,
                    },
                    "correta": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 4,
                    },
                    "comentario": {"type": "string"},
                },
                "required": [
                    "pergunta",
                    "opcoes",
                    "correta",
                    "comentario",
                ],
            },
        }
    },
    "required": ["questoes"],
}


SCHEMA_REVISAO = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "revisoes": {
            "type": "array",
            "items": {
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
                        "minimum": 0,
                        "maximum": 4,
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
                    "comentario",
                    "motivo",
                ],
            },
        }
    },
    "required": ["revisoes"],
}


# ============================================================
# PROMPT DE GERAÇÃO
# ============================================================

def montar_prompt(
    banca: str,
    nivel: str,
    tema: str,
    qtd: int,
    evitar: list = None,
) -> str:

    bloco_evitar = ""

    if evitar:
        # Evita prompts gigantes, mantendo as 100 perguntas mais recentes.
        evitar_recentes = evitar[-100:]

        lista = "\n".join(
            f"- {p}" for p in evitar_recentes
        )

        bloco_evitar = f"""
NÃO repita nem crie variações óbvias das perguntas abaixo.
Não basta trocar nomes, números ou pequenas palavras.

Questões já utilizadas:
{lista}
"""

    return f"""
Atue como um Examinador de Elite especializado em concursos públicos.

Gere exatamente {qtd} questão(ões) de múltipla escolha.

Banca: {banca}
Nível: {nivel}
Tema: {tema}

REGRAS OBRIGATÓRIAS:

1. Cada questão deve possuir exatamente 5 alternativas.
2. As alternativas devem ser A, B, C, D e E.
3. Deve existir exatamente UMA alternativa correta.
4. A questão deve ser tecnicamente correta.
5. Evite ambiguidades.
6. Evite alternativas parcialmente corretas quando a questão exigir uma
   resposta única.
7. Os distratores devem ser plausíveis.
8. Não repita questões já utilizadas.
9. Não crie variações superficiais das questões anteriores.
10. Em questões de lógica ou matemática, resolva o problema antes de
    escolher a resposta.
11. O campo "correta" deve apontar para a alternativa realmente correta:
    0 = A, 1 = B, 2 = C, 3 = D, 4 = E.
12. O comentário deve explicar a solução e deve apontar para a MESMA
    alternativa indicada no campo "correta".
13. Não coloque a letra da alternativa no texto da alternativa.
14. Não inclua texto fora da estrutura solicitada.

{bloco_evitar}

Antes de finalizar cada questão, faça uma conferência interna:
- resolva a questão;
- confira as cinco alternativas;
- confirme que só uma pode ser considerada correta;
- confira o índice "correta";
- confira se o comentário corresponde ao gabarito.
"""


# ============================================================
# PROMPT DE REVISÃO
# ============================================================

def montar_prompt_revisao(
    questoes: list,
    banca: str,
    nivel: str,
    tema: str,
) -> str:

    questoes_json = json.dumps(
        questoes,
        ensure_ascii=False,
        indent=2,
    )

    return f"""
Você é o REVISOR FINAL de um banco de questões de concursos públicos.

Sua função é auditar as questões abaixo antes que elas sejam liberadas
para um aluno.

Contexto:
Banca: {banca}
Nível: {nivel}
Tema: {tema}

QUESTÕES PARA AUDITAR:
{questoes_json}

Para CADA questão, faça uma análise independente.

Verifique obrigatoriamente:

1. Se a pergunta está bem formulada.
2. Se existem exatamente cinco alternativas.
3. Se existe exatamente UMA alternativa inequivocamente correta.
4. Se a alternativa indicada no campo "correta" é realmente a correta.
5. Se alguma outra alternativa também poderia ser considerada correta.
6. Em lógica, verifique formalmente a inferência, contrapositiva,
   equivalência ou classificação apresentada.
7. Em matemática/estatística, refaça os cálculos.
8. Em questões conceituais, verifique se a definição e a aplicação
   estão coerentes.
9. Se o comentário explica a resposta correta.
10. Se o comentário NÃO contradiz o campo "correta".

Use exatamente um destes status:

- "aprovada":
  A questão está correta e o gabarito já está correto.

- "corrigir_gabarito":
  A questão e as alternativas são aproveitáveis, existe uma única
  alternativa correta, mas o campo "correta" e/ou o comentário precisam
  ser corrigidos.
  Nesse caso, informe no campo "correta" o índice correto e escreva um
  novo comentário coerente com esse gabarito.

- "rejeitar":
  A questão não é segura para uma prova. Exemplos:
  duas alternativas corretas, nenhuma correta, enunciado ambíguo,
  cálculo inconsistente, informação insuficiente ou alternativas
  defeituosas.

IMPORTANTE:
Não aprove uma questão apenas porque o gabarito fornecido pelo gerador
parece plausível. Resolva a questão você mesmo.

O campo "correta" da sua revisão deve SEMPRE representar a resposta
final correta, mesmo quando o status for "rejeitar".
"""


# ============================================================
# CHAMADA GENÉRICA COM JSON SCHEMA
# ============================================================

def _responder_json(
    prompt: str,
    schema: dict,
    nome_schema: str,
    reasoning_effort: str = "medium",
    max_output_tokens: int = 8000,
) -> dict:

    response = client.responses.create(
        model=MODEL,
        reasoning={"effort": reasoning_effort},
        input=prompt,
        max_output_tokens=max_output_tokens,
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

    if not texto:
        raise ValueError(
            "A OpenAI retornou uma resposta vazia."
        )

    try:
        return json.loads(texto)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"A resposta da OpenAI não é um JSON válido: {e}. "
            f"Resposta recebida: {texto[:1000]}"
        ) from e


# ============================================================
# VALIDAÇÃO LOCAL
# ============================================================

def _validar_estrutura_questao(q: dict) -> None:
    if not isinstance(q, dict):
        raise ValueError(
            "A questão não é um objeto JSON válido."
        )

    pergunta = q.get("pergunta")
    opcoes = q.get("opcoes")
    correta = q.get("correta")
    comentario = q.get("comentario")

    if not isinstance(pergunta, str) or not pergunta.strip():
        raise ValueError(
            "Campo 'pergunta' inválido."
        )

    if not isinstance(opcoes, list) or len(opcoes) != 5:
        raise ValueError(
            "A questão precisa possuir exatamente 5 alternativas."
        )

    if not all(
        isinstance(opcao, str) and opcao.strip()
        for opcao in opcoes
    ):
        raise ValueError(
            "Existe alternativa vazia ou inválida."
        )

    # Evita cinco alternativas idênticas ou repetidas.
    normalizadas = [
        " ".join(opcao.strip().lower().split())
        for opcao in opcoes
    ]

    if len(set(normalizadas)) != 5:
        raise ValueError(
            "Existem alternativas repetidas."
        )

    if not isinstance(correta, int) or not 0 <= correta <= 4:
        raise ValueError(
            f"Índice 'correta' inválido: {correta}"
        )

    if not isinstance(comentario, str):
        raise ValueError(
            "Campo 'comentario' inválido."
        )


def _normalizar_questao(q: dict) -> dict:
    _validar_estrutura_questao(q)

    return {
        "pergunta": q["pergunta"].strip(),
        "opcoes": [
            opcao.strip()
            for opcao in q["opcoes"]
        ],
        "correta": int(q["correta"]),
        "comentario": q["comentario"].strip(),
    }


# ============================================================
# GERAÇÃO
# ============================================================

def _chamar_api(
    prompt: str,
    reasoning_effort: str = "medium",
):
    dados = _responder_json(
        prompt=prompt,
        schema=SCHEMA_QUESTOES,
        nome_schema="geracao_questoes",
        reasoning_effort=reasoning_effort,
        max_output_tokens=8000,
    )

    questoes = dados.get("questoes")

    if not isinstance(questoes, list):
        raise ValueError(
            "A resposta não contém uma lista válida em 'questoes'."
        )

    if not questoes:
        raise ValueError(
            "A OpenAI não gerou nenhuma questão."
        )

    return [
        _normalizar_questao(q)
        for q in questoes
    ]


# ============================================================
# REVISÃO DAS QUESTÕES
# ============================================================

def _revisar_questoes(
    questoes: list,
    banca: str,
    nivel: str,
    tema: str,
) -> list:

    if not questoes:
        return []

    prompt = montar_prompt_revisao(
        questoes=questoes,
        banca=banca,
        nivel=nivel,
        tema=tema,
    )

    dados = _responder_json(
        prompt=prompt,
        schema=SCHEMA_REVISAO,
        nome_schema="revisao_questoes",
        # Revisão usa raciocínio médio para reduzir erros em
        # matemática, lógica e questões conceituais.
        reasoning_effort="medium",
        max_output_tokens=8000,
    )

    revisoes = dados.get("revisoes")

    if not isinstance(revisoes, list):
        raise ValueError(
            "A revisão não retornou uma lista válida."
        )

    if len(revisoes) != len(questoes):
        raise ValueError(
            f"A revisão retornou {len(revisoes)} revisões para "
            f"{len(questoes)} questões."
        )

    resultado = []

    for q, revisao in zip(questoes, revisoes):

        status = revisao.get("status")
        correta = revisao.get("correta")
        comentario = revisao.get("comentario")
        motivo = revisao.get("motivo", "")

        if status not in {
            "aprovada",
            "corrigir_gabarito",
            "rejeitar",
        }:
            raise ValueError(
                f"Status de revisão inválido: {status}"
            )

        if not isinstance(correta, int) or not 0 <= correta <= 4:
            raise ValueError(
                f"Índice correto inválido na revisão: {correta}"
            )

        if not isinstance(comentario, str):
            comentario = ""

        if status == "rejeitar":
            print(
                "[REVISÃO] ❌ Questão rejeitada: "
                f"{motivo}"
            )
            resultado.append(None)
            continue

        # A revisão passa a ser a fonte final do gabarito e comentário.
        q_final = dict(q)
        q_final["correta"] = correta
        q_final["comentario"] = comentario.strip()

        try:
            _validar_estrutura_questao(q_final)
        except Exception as e:
            print(
                "[REVISÃO] ❌ Questão descartada após revisão "
                f"por falha estrutural: {e}"
            )
            resultado.append(None)
            continue

        if status == "corrigir_gabarito":
            print(
                "[REVISÃO] 🔧 Gabarito/comentário corrigidos."
            )
        else:
            print(
                "[REVISÃO] ✓ Questão aprovada."
            )

        resultado.append(q_final)

    return resultado


# ============================================================
# GERAÇÃO + REVISÃO
# ============================================================

def gerar_questoes(
    banca: str,
    nivel: str,
    tema: str,
    qtd: int,
    evitar: list = None,
) -> list:

    if qtd <= 0:
        return []

    evitar = list(evitar) if evitar else []

    todas = []

    # Geramos em uma única chamada quando o pedido é pequeno.
    # O revisor audita o conjunto inteiro.
    for tentativa in range(1, MAX_TENTATIVAS + 1):

        prompt = montar_prompt(
            banca=banca,
            nivel=nivel,
            tema=tema,
            qtd=qtd,
            evitar=evitar,
        )

        try:
            print(
                f"[{tema}] Geração "
                f"{tentativa}/{MAX_TENTATIVAS}..."
            )

            questoes = _chamar_api(
                prompt,
                reasoning_effort="medium",
            )

            if len(questoes) != qtd:
                raise ValueError(
                    f"A IA retornou {len(questoes)} questão(ões), "
                    f"mas eram esperadas {qtd}."
                )

            print(
                f"[{tema}] ✓ {len(questoes)} questões geradas."
            )

            print(
                f"[{tema}] 🔎 Iniciando revisão automática..."
            )

            revisadas = _revisar_questoes(
                questoes=questoes,
                banca=banca,
                nivel=nivel,
                tema=tema,
            )

            aprovadas = [
                q for q in revisadas
                if q is not None
            ]

            if len(aprovadas) == qtd:
                print(
                    f"[{tema}] ✓ Revisão aprovada: "
                    f"{qtd}/{qtd}."
                )
                return aprovadas

            rejeitadas = qtd - len(aprovadas)

            print(
                f"[{tema}] ⚠️ "
                f"{rejeitadas} questão(ões) rejeitada(s) "
                f"na revisão."
            )

            # As questões aprovadas já podem servir de histórico
            # para evitar repetição na próxima rodada.
            evitar.extend(
                q.get("pergunta", "")
                for q in aprovadas
                if q.get("pergunta")
            )

            # Se algumas foram rejeitadas, tentamos gerar o conjunto
            # novamente na próxima tentativa.
            if tentativa < MAX_TENTATIVAS:
                time.sleep(3)

        except RateLimitError as e:
            print(
                f"[{tema}] ⚠️ Rate limit: {e}"
            )

            if tentativa < MAX_TENTATIVAS:
                espera = 10 * tentativa
                print(
                    f"[{tema}] Aguardando {espera}s..."
                )
                time.sleep(espera)

        except Exception as e:
            print(
                f"[{tema}] ❌ ERRO na tentativa "
                f"{tentativa}/{MAX_TENTATIVAS}"
            )
            print(
                f"[{tema}] Tipo: {type(e).__name__}"
            )
            print(
                f"[{tema}] Mensagem: {e}"
            )

            if tentativa < MAX_TENTATIVAS:
                espera = 5 * tentativa
                print(
                    f"[{tema}] Nova tentativa em {espera}s..."
                )
                time.sleep(espera)

    print(
        f"[{tema}] ❌ FALHOU após "
        f"{MAX_TENTATIVAS} tentativas."
    )

    return []


# ============================================================
# GERAÇÃO EM BLOCOS
# ============================================================

def gerar_bloco(
    banca: str,
    nivel: str,
    tema: str,
    qtd: int,
    evitar: list = None,
    tamanho_bloco: int = 10,
) -> list:

    evitar = list(evitar) if evitar else []
    todas_questoes = []

    if qtd <= 0:
        return todas_questoes

    if tamanho_bloco <= 0:
        tamanho_bloco = 10

    blocos = [
        tamanho_bloco
    ] * (qtd // tamanho_bloco)

    resto = qtd % tamanho_bloco

    if resto:
        blocos.append(resto)

    for i, qtd_bloco in enumerate(blocos, 1):

        if len(blocos) == 1:
            sufixo_tema = tema
        else:
            sufixo_tema = (
                f"{tema} "
                f"(Bloco {i}/{len(blocos)})"
            )

        print(
            f"\n[{tema}] "
            f"Gerando bloco {i}/{len(blocos)} "
            f"com {qtd_bloco} questão(ões)..."
        )

        questoes = gerar_questoes(
            banca=banca,
            nivel=nivel,
            tema=sufixo_tema,
            qtd=qtd_bloco,
            evitar=evitar,
        )

        # Fallback para blocos maiores.
        if not questoes and qtd_bloco > 1:

            print(
                f"[{tema}] ⚠️ "
                f"Bloco {i} falhou. "
                f"Tentando dividir em duas partes..."
            )

            metade = qtd_bloco // 2

            p1 = gerar_questoes(
                banca=banca,
                nivel=nivel,
                tema=f"{sufixo_tema} (Parte 1)",
                qtd=metade,
                evitar=evitar,
            )

            evitar_parte_2 = (
                evitar
                + [
                    q.get("pergunta", "")
                    for q in p1
                    if q.get("pergunta")
                ]
            )

            p2 = gerar_questoes(
                banca=banca,
                nivel=nivel,
                tema=f"{sufixo_tema} (Parte 2)",
                qtd=qtd_bloco - metade,
                evitar=evitar_parte_2,
            )

            questoes = p1 + p2

        todas_questoes.extend(questoes)

        evitar.extend(
            q.get("pergunta", "")
            for q in questoes
            if q.get("pergunta")
        )

    if len(todas_questoes) < qtd:
        print(
            f"[{tema}] ⚠️ "
            f"Geradas {len(todas_questoes)}/{qtd} questões."
        )
    else:
        print(
            f"[{tema}] ✓ "
            f"{len(todas_questoes)}/{qtd} questões concluídas."
        )

    return todas_questoes


# ============================================================
# EMBARALHAMENTO DAS ALTERNATIVAS
# ============================================================

def _embaralhar_questao(
    q: dict,
    posicao_alvo: int,
):
    """
    Reposiciona a alternativa correta.

    0 = A
    1 = B
    2 = C
    3 = D
    4 = E
    """

    opcoes = list(q["opcoes"])

    correta = q["correta"]

    texto_correta = opcoes.pop(correta)

    random.shuffle(opcoes)

    opcoes.insert(
        posicao_alvo,
        texto_correta,
    )

    q["opcoes"] = opcoes
    q["correta"] = posicao_alvo


# ============================================================
# BALANCEAMENTO DO GABARITO
# ============================================================

def balancear_gabaritos(
    questoes: list,
) -> list:
    """
    Distribui as respostas corretas entre A-E.

    O balanceamento é feito no código e NÃO por uma nova chamada
    à IA.
    """

    n = len(questoes)

    if n == 0:
        return questoes

    posicoes = [
        i % 5
        for i in range(n)
    ]

    random.shuffle(posicoes)

    for q, pos in zip(questoes, posicoes):
        _embaralhar_questao(
            q,
            pos,
        )

    return questoes
