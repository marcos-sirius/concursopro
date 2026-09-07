"""
Equivalente a config_manager.py, mas para o fluxo web: reaproveita a parte
PURA da lógica (slugify, transformação de dict) e persiste no Drive via
Service Account (drive_storage.py).
"""
import config_manager as cm
import drive_storage as ds


def listar_estudos() -> list:
    """Retorna [(nome_da_pasta_no_drive, folder_id), ...] ordenado por nome."""
    return [(f["name"], f["id"]) for f in ds.listar_estudos()]


def criar_estudo(nome_estudo: str, banca: str, nivel: str, eixos: dict) -> tuple:
    slug = cm.slugify(nome_estudo)
    folder_id = ds.obter_ou_criar_pasta_estudo(slug)

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
