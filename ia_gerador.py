```python
"""
Geração de questões via API da OpenAI.

Mantém o mesmo contrato usado pelo restante da aplicação:
- gerar_questoes(...)
- gerar_bloco(...)
- balancear_gabaritos(...)

Cada questão possui:
{
    "pergunta": "...",
    "opcoes": ["A", "B", "C", "D", "E"],
    "correta": 0,
    "comentario": "..."
}
"""

import json
import random
import time
import os

from openai import OpenAI, RateLimitError
from dotenv import load_dotenv


# ============================================================
# CONFIGURAÇÃO
# ============================================================

load_dotenv()

MODEL = "gpt-5.6-terra"
MAX_TENTATIVAS = 4

api_key = os.environ.get("OPENAI_API_KEY")

if not api_key:
    raise RuntimeError(
        "OPENAI_API_KEY não encontrada. "
        "Configure a variável OPENAI_API_KEY no ambiente "
        "ou nos Secrets do Streamlit."
    )

client = OpenAI(api_key=api_key)


# ============================================================
# PROMPT
# ============================================================

def montar_prompt(
    banca: str,
    nivel: str,
    tema: str,
    qtd: int,
    evitar: list = None
) -> str:

    bloco_evitar = ""

    if evitar:
        # Limita o histórico enviado para evitar prompts gigantes.
        # Mantém as questões mais recentes.
        evitar_recentes = evitar[-100:]

        lista = "\n".join(
            f"- {p}" for p in evitar_recentes
        )

        bloco_evitar = f"""
NÃO repita, nem crie variações óbvias das perguntas abaixo.
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
3. Deve existir exatamente uma alternativa correta.
4. A questão deve ser tecnicamente correta.
5. Evite ambiguidades.
6. Evite alternativas obviamente absurdas.
7. Os distratores devem ser plausíveis.
8. Não repita questões já utilizadas.
9. Não crie variações superficiais das questões anteriores.
10. O comentário deve explicar por que a resposta está correta e,
    quando possível, indicar a lógica do erro dos distratores.
11. O índice da resposta correta deve ser:
    0 para A
    1 para B
    2 para C
    3 para D
    4 para E

{bloco_evitar}

Retorne exclusivamente os dados estruturados solicitados pela API.
"""


# ============================================================
# CHAMADA À OPENAI
# ============================================================

def _chamar_api(prompt: str):
    """
    Chama o modelo usando a Responses API + Structured Outputs.

    Retorna uma lista de questões.
    """

    response = client.responses.create(
        model=MODEL,

        # Para geração de questões, não precisamos de raciocínio
        # adicional muito elevado. Isso reduz custo/latência.
        reasoning={
            "effort": "none"
        },

        input=prompt,

        max_output_tokens=8000,

        text={
            "format": {
                "type": "json_schema",
                "name": "geracao_questoes",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "questoes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "pergunta": {
                                        "type": "string"
                                    },
                                    "opcoes": {
                                        "type": "array",
                                        "items": {
                                            "type": "string"
                                        },
                                        "minItems": 5,
                                        "maxItems": 5
                                    },
                                    "correta": {
                                        "type": "integer",
                                        "minimum": 0,
                                        "maximum": 4
                                    },
                                    "comentario": {
                                        "type": "string"
                                    }
                                },
                                "required": [
                                    "pergunta",
                                    "opcoes",
                                    "correta",
                                    "comentario"
                                ]
                            }
                        }
                    },
                    "required": [
                        "questoes"
                    ]
                }
            }
        }
    )

    # --------------------------------------------------------
    # Verificação de resposta
    # --------------------------------------------------------

    texto = response.output_text

    if not texto:
        raise ValueError(
            "A OpenAI retornou uma resposta vazia."
        )

    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"A resposta da OpenAI não é um JSON válido: {e}. "
            f"Resposta recebida: {texto[:1000]}"
        ) from e

    questoes = dados.get("questoes")

    if not isinstance(questoes, list):
        raise ValueError(
            "A resposta não contém uma lista válida em 'questoes'."
        )

    # --------------------------------------------------------
    # Validação das questões
    # --------------------------------------------------------

    questoes_validas = []

    for i, q in enumerate(questoes, start=1):

        if not isinstance(q, dict):
            raise ValueError(
                f"Questão {i} não é um objeto JSON válido."
            )

        pergunta = q.get("pergunta")
        opcoes = q.get("opcoes")
        correta = q.get("correta")
        comentario = q.get("comentario")

        if not isinstance(pergunta, str) or not pergunta.strip():
            raise ValueError(
                f"Questão {i}: campo 'pergunta' inválido."
            )

        if not isinstance(opcoes, list) or len(opcoes) != 5:
            raise ValueError(
                f"Questão {i}: esperadas 5 alternativas, "
                f"recebidas {len(opcoes) if isinstance(opcoes, list) else 'valor inválido'}."
            )

        if not all(
            isinstance(opcao, str) and opcao.strip()
            for opcao in opcoes
        ):
            raise ValueError(
                f"Questão {i}: existe alternativa vazia ou inválida."
            )

        if not isinstance(correta, int) or not 0 <= correta <= 4:
            raise ValueError(
                f"Questão {i}: índice 'correta' inválido: {correta}"
            )

        if not isinstance(comentario, str):
            comentario = ""

        questoes_validas.append({
            "pergunta": pergunta.strip(),
            "opcoes": [op.strip() for op in opcoes],
            "correta": correta,
            "comentario": comentario.strip()
        })

    if not questoes_validas:
        raise ValueError(
            "A OpenAI respondeu corretamente, mas não gerou nenhuma questão."
        )

    return questoes_validas


# ============================================================
# GERAÇÃO COM RETRY
# ============================================================

def gerar_questoes(
    banca: str,
    nivel: str,
    tema: str,
    qtd: int,
    evitar: list = None
) -> list:

    prompt = montar_prompt(
        banca=banca,
        nivel=nivel,
        tema=tema,
        qtd=qtd,
        evitar=evitar
    )

    ultimo_erro = None

    for tentativa in range(1, MAX_TENTATIVAS + 1):

        try:
            print(
                f"[{tema}] "
                f"Tentativa {tentativa}/{MAX_TENTATIVAS}..."
            )

            questoes = _chamar_api(prompt)

            # Não aceita quantidade errada silenciosamente.
            if len(questoes) != qtd:
                raise ValueError(
                    f"A IA retornou {len(questoes)} questão(ões), "
                    f"mas eram esperadas {qtd}."
                )

            print(
                f"[{tema}] ✓ "
                f"{len(questoes)} questões geradas."
            )

            return questoes

        except RateLimitError as e:

            ultimo_erro = e

            espera = 10 * tentativa

            print(
                f"[{tema}] ⚠️ Rate limit na tentativa "
                f"{tentativa}/{MAX_TENTATIVAS}."
            )

            print(
                f"[{tema}] Aguardando {espera}s..."
            )

            if tentativa < MAX_TENTATIVAS:
                time.sleep(espera)

        except Exception as e:

            ultimo_erro = e

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

    if ultimo_erro:
        print(
            f"[{tema}] Último erro: "
            f"{type(ultimo_erro).__name__}: {ultimo_erro}"
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
    tamanho_bloco: int = 10
) -> list:
    """
    Gera 'qtd' questões em blocos de até 'tamanho_bloco'.

    Se um bloco falhar, tenta automaticamente dividi-lo em
    partes menores.
    """

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
            evitar=evitar
        )

        # ----------------------------------------------------
        # FALLBACK
        # ----------------------------------------------------

        if not questoes and qtd_bloco > 1:

            print(
                f"[{tema}] ⚠️ "
                f"Bloco {i} falhou."
            )

            print(
                f"[{tema}] Tentando dividir em duas partes..."
            )

            metade = qtd_bloco // 2

            p1 = gerar_questoes(
                banca=banca,
                nivel=nivel,
                tema=f"{sufixo_tema} (Parte 1)",
                qtd=metade,
                evitar=evitar
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
                evitar=evitar_parte_2
            )

            questoes = p1 + p2

        todas_questoes.extend(questoes)

        # ----------------------------------------------------
        # ATUALIZA HISTÓRICO
        # ----------------------------------------------------

        evitar.extend(
            q.get("pergunta", "")
            for q in questoes
            if q.get("pergunta")
        )

    # --------------------------------------------------------
    # RESULTADO FINAL
    # --------------------------------------------------------

    if len(todas_questoes) < qtd:

        print(
            f"[{tema}] ⚠️ "
            f"Geradas {len(todas_questoes)}/{qtd} "
            f"questões."
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
    posicao_alvo: int
):
    """
    Reposiciona a alternativa correta para 'posicao_alvo'.

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
        texto_correta
    )

    q["opcoes"] = opcoes
    q["correta"] = posicao_alvo


# ============================================================
# BALANCEAMENTO DO GABARITO
# ============================================================

def balancear_gabaritos(
    questoes: list
) -> list:
    """
    Distribui as respostas corretas entre A-E.

    O balanceamento é feito no código, e não depende da IA.
    """

    n = len(questoes)

    if n == 0:
        return questoes

    posicoes = [
        i % 5
        for i in range(n)
    ]

    random.shuffle(posicoes)

    for q, pos in zip(
        questoes,
        posicoes
    ):
        _embaralhar_questao(
            q,
            pos
        )

    return questoes
```
