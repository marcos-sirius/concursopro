"""
Equivalente a config_manager.py, mas para o fluxo web: reaproveita a parte
PURA da lógica (slugify, transformação de dict) e persiste no Drive via
token de longa duração da conta pessoal (drive_storage.py).
"""
import config_manager as cm
import drive_storage as ds


def listar_estudos() -> list:
    """Retorna [(nome_da_pasta_no_drive, folder_id), ...] ordenado por nome."""
    return [(f["name"], f["id"]) for f in ds.listar_estudos()]


CATEGORIA_PADRAO = "Geral"  # usada quando um estudo antigo não tem categoria definida


def criar_estudo(nome_estudo: str, banca: str, nivel: str, eixos: dict, categoria: str = CATEGORIA_PADRAO) -> tuple:
    """
    Cria um estudo novo. IMPORTANTE: se o nome escolhido gerar o mesmo "slug"
    de um estudo que já existe (ex: "DATAPREV 2026" e "DATAPREV - 2026" viram
    o mesmo identificador interno "dataprev_2026"), a função recusa a criação
    em vez de sobrescrever silenciosamente o config.json do estudo existente
    — o que apagaria o histórico de temas e zeraria o contador de simulações
    dele sem aviso nenhum.
    """
    slug = cm.slugify(nome_estudo)
    folder_id = ds.obter_ou_criar_pasta_estudo(slug)

    if ds.buscar_arquivo("config.json", folder_id):
        raise ValueError(
            f"Já existe um estudo cujo nome gera o mesmo identificador interno "
            f"('{slug}'). Escolha um nome mais diferente pra não sobrescrever "
            f"os dados do estudo existente."
        )

    config = {
        "nome_estudo": nome_estudo,
        "slug": slug,
        "banca": banca,
        "nivel": nivel,
        "categoria": (categoria or CATEGORIA_PADRAO).strip() or CATEGORIA_PADRAO,
        "eixos": eixos,
        "historico_temas": {e: [] for e in eixos},
        "simulacao_atual": 0,
    }
    ds.salvar_config(folder_id, config)
    return config, folder_id


def carregar_config(folder_id: str) -> dict:
    return ds.ler_config(folder_id)


def salvar_config(folder_id: str, config: dict):
    ds.salvar_config(folder_id, config)


def ressincronizar_eixos(folder_id: str, config: dict, novos_eixos: dict) -> dict:
    config = cm.aplicar_ressincronizacao(config, novos_eixos)
    ds.salvar_config(folder_id, config)
    return config


def excluir_estudo(folder_id: str):
    """Move a pasta inteira do estudo (config, simulado.xlsx, respostas) para a lixeira do Drive."""
    ds.mover_para_lixeira(folder_id)


def atualizar_categoria(folder_id: str, config: dict, nova_categoria: str) -> dict:
    """Renomeia a categoria de um estudo já existente."""
    config["categoria"] = (nova_categoria or CATEGORIA_PADRAO).strip() or CATEGORIA_PADRAO
    ds.salvar_config(folder_id, config)
    return config


def carregar_resumo_estudos(estudos: list) -> list:
    """
    Lê o config.json de CADA estudo uma única vez e devolve um resumo:
    [{"nome", "folder_id", "categoria", "simulacao_atual"}, ...]

    Consolidado num só lugar pra evitar ler o mesmo config.json várias vezes
    em pontos diferentes da tela (resumo no topo da Gestão, populando a lista
    de categorias, e a cascata categoria->estudo em Responder simulado).
    Quem CHAMA esta função deve cachear o resultado (ver streamlit_app.py) —
    ela mesma não cacheia, porque não depende do Streamlit.
    """
    resumo = []
    for nome, folder_id in estudos:
        try:
            config = ds.ler_config(folder_id)
        except FileNotFoundError:
            continue
        resumo.append({
            "nome": nome,
            "folder_id": folder_id,
            "categoria": config.get("categoria") or CATEGORIA_PADRAO,
            "simulacao_atual": config.get("simulacao_atual", 0),
        })
    return resumo


def contar_simulados_totais(resumo_estudos: list) -> int:
    """Soma 'simulacao_atual' a partir de um resumo já carregado (ver carregar_resumo_estudos)."""
    return sum(r["simulacao_atual"] for r in resumo_estudos)


def listar_categorias(resumo_estudos: list) -> list:
    """Categorias distintas em uso, ordenadas alfabeticamente."""
    return sorted({r["categoria"] for r in resumo_estudos})
