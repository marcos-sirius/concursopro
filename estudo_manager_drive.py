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


def criar_estudo(nome_estudo: str, banca: str, nivel: str, eixos: dict) -> tuple:
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


def contar_simulados_totais(estudos: list) -> int:
    """
    Soma 'simulacao_atual' de todos os estudos — usado só pro resumo no topo
    da tela de Gestão. Faz 1 leitura de config.json por estudo; em troca de
    um resumo mais completo, aceita esse custo extra (baixo, dado que o uso
    é pessoal/pequeno grupo, não uma escala grande de estudos).
    """
    total = 0
    for _, folder_id in estudos:
        try:
            config = ds.ler_config(folder_id)
            total += config.get("simulacao_atual", 0)
        except FileNotFoundError:
            continue
    return total
