"""
Resultados de quem respondeu simulados.

MUDANÇA IMPORTANTE (correção de bug de concorrência): antes, cada resposta
virava uma LINHA numa guia "Resultados" dentro do mesmo simulado.xlsx
compartilhado — só que o fluxo de gravação é "baixar a planilha inteira,
adicionar linha, subir a planilha inteira de volta". Se duas pessoas
respondessem ao mesmo estudo com poucos segundos de diferença, a segunda
gravação podia sobrescrever e apagar silenciosamente a resposta da primeira
(porque ela baixou a planilha antes da primeira terminar de subir a dela).

Agora, cada envio vira um ARQUIVO JSON PRÓPRIO, num formato determinístico
por (simulado, usuário): respostas/resposta_sim<N>_<usuario-slugificado>.json
Isso elimina o problema de raiz — a gravação de uma pessoa nunca toca no
arquivo de outra pessoa, então não existe mais janela de conflito entre
respondentes diferentes. Reenviar o MESMO simulado (você mesmo, de novo)
sobrescreve seu próprio arquivo anterior, de propósito — é isso que permite
avisar "você já respondeu esse simulado" antes de deixar responder de novo.

As perguntas em si continuam vindo do Consolidado dentro do simulado.xlsx
(dados gerados pelo admin, só LEITURA por parte de quem responde — leitura
concorrente nunca foi problema, só escrita concorrente era).
"""
import datetime as dt
import json

import config_manager as cm
import drive_storage as ds

NOME_PASTA_RESPOSTAS = "respostas"
LETRAS = ["A", "B", "C", "D", "E"]


# ---------------- leitura do banco de questões (Consolidado, inalterado) ----------------

def listar_simulacoes_disponiveis(wb) -> list:
    """Números de simulação presentes no Consolidado, do mais recente pro mais antigo."""
    if "Consolidado" not in wb.sheetnames:
        return []
    ws = wb["Consolidado"]
    numeros = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[0] is not None:
            numeros.add(int(row[0]))
    return sorted(numeros, reverse=True)


def carregar_questoes_da_simulacao(wb, numero_simulacao: int) -> list:
    """
    Lê o Consolidado e devolve as questões de uma simulação específica, no
    formato: [{"eixo", "tema", "pergunta", "opcoes": [...], "correta": int,
    "comentario": str}, ...]
    """
    ws = wb["Consolidado"]
    questoes = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        simulacao, eixo, pergunta = row[0], row[1], row[2]
        if simulacao != numero_simulacao:
            continue
        opcoes = [row[3], row[4], row[5], row[6], row[7]]
        correta_letra = row[8]
        comentario = row[9] if len(row) > 9 and row[9] is not None else ""
        tema = row[10] if len(row) > 10 else ""

        if isinstance(correta_letra, str) and correta_letra.strip().upper() in LETRAS:
            idx_correta = LETRAS.index(correta_letra.strip().upper())
        else:
            idx_correta = int(correta_letra) if correta_letra is not None else 0

        questoes.append({
            "eixo": eixo,
            "tema": tema,
            "pergunta": pergunta,
            "opcoes": opcoes,
            "correta": idx_correta,
            "comentario": comentario,
        })
    return questoes


# ---------------- respostas (arquivos individuais no Drive) ----------------

def _nome_arquivo_resposta(numero_simulacao: int, usuario: str) -> str:
    return f"resposta_sim{numero_simulacao}_{cm.slugify(usuario)}.json"


def _pasta_respostas(folder_id_estudo: str) -> str:
    return ds.obter_ou_criar_subpasta(NOME_PASTA_RESPOSTAS, folder_id_estudo)


def buscar_resposta_existente(folder_id_estudo: str, numero_simulacao: int, usuario: str) -> dict | None:
    """Se esse participante já respondeu esse simulado antes, devolve o resultado salvo."""
    pasta = _pasta_respostas(folder_id_estudo)
    nome = _nome_arquivo_resposta(numero_simulacao, usuario)
    arquivo = ds.buscar_arquivo(nome, pasta)
    if not arquivo:
        return None
    dados = ds.baixar_bytes(arquivo["id"])
    return json.loads(dados.decode("utf-8"))


def gravar_resposta_participante(folder_id_estudo: str, numero_simulacao: int, usuario: str, respostas: list) -> dict:
    """
    respostas: [{"eixo","tema","pergunta","opcoes","correta","resposta_idx"}, ...]
    Grava (ou sobrescreve, se for um reenvio do mesmo participante+simulado)
    um arquivo JSON isolado — nunca toca no arquivo de mais ninguém.
    """
    linhas = []
    acertos = 0
    for r in respostas:
        resposta_idx = r["resposta_idx"]
        certo = resposta_idx == r["correta"]
        if certo:
            acertos += 1
        linhas.append({
            "eixo": r["eixo"],
            "tema": r["tema"],
            "pergunta": r["pergunta"],
            "resposta_dada": r["opcoes"][resposta_idx],
            "resposta_correta": r["opcoes"][r["correta"]],
            "acertou": certo,
        })

    payload = {
        "usuario": usuario,
        "simulacao": numero_simulacao,
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "acertos": acertos,
        "total": len(linhas),
        "respostas": linhas,
    }

    pasta = _pasta_respostas(folder_id_estudo)
    nome = _nome_arquivo_resposta(numero_simulacao, usuario)
    existente = ds.buscar_arquivo(nome, pasta)
    dados = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    ds.salvar_bytes(nome, dados, pasta, "application/json",
                     file_id=existente["id"] if existente else None)
    return payload


def carregar_todos_resultados(folder_id_estudo: str) -> list:
    """
    Lê TODOS os arquivos de resposta do estudo e devolve no mesmo formato
    "uma linha por questão respondida" de antes, pra não precisar mudar a
    lógica do dashboard de Desempenho.
    """
    pasta = _pasta_respostas(folder_id_estudo)
    arquivos = ds.listar_arquivos(pasta, apenas_extensao=".json")

    linhas = []
    for arq in arquivos:
        dados = json.loads(ds.baixar_bytes(arq["id"]).decode("utf-8"))
        for r in dados.get("respostas", []):
            linhas.append({
                "timestamp": dados.get("timestamp"),
                "simulacao": dados.get("simulacao"),
                "participante": dados.get("usuario"),
                "eixo": r["eixo"],
                "tema": r["tema"],
                "pergunta": r["pergunta"],
                "resposta_dada": r["resposta_dada"],
                "resposta_correta": r["resposta_correta"],
                "acertou": r["acertou"],
            })
    return linhas
