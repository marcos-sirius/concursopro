"""
Cada "estudo" (concurso) vira uma pasta em estudos/<slug>/ com:
  - config.json   -> banca, nivel, eixos, historico de temas, contador de simulação
  - simulado.xlsx -> guias por eixo (resetadas a cada simulado) + Consolidado (histórico)
"""
import json
import re
import unicodedata
from pathlib import Path

BASE_DIR = Path(__file__).parent / "estudos"


def slugify(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-zA-Z0-9]+", "_", sem_acento).strip("_").lower()


def listar_estudos() -> list:
    if not BASE_DIR.exists():
        return []
    return sorted([p.name for p in BASE_DIR.iterdir() if p.is_dir()])


def caminho_estudo(slug: str) -> Path:
    return BASE_DIR / slug


def criar_estudo(nome_estudo: str, banca: str, nivel: str, eixos: dict) -> dict:
    """
    eixos: estrutura vinda de conteudo_loader.carregar_conteudo_programatico()
    Cria a pasta do estudo + config.json inicial.
    """
    slug = slugify(nome_estudo)
    pasta = caminho_estudo(slug)
    pasta.mkdir(parents=True, exist_ok=True)

    config = {
        "nome_estudo": nome_estudo,
        "slug": slug,
        "banca": banca,
        "nivel": nivel,
        "eixos": eixos,            # qtd, pontos, numero_inicial, temas{tema: peso}
        "historico_temas": {e: [] for e in eixos},
        "simulacao_atual": 0,
        "excel_path": str(pasta / "simulado.xlsx"),
    }
    salvar_config(config)
    return config


def salvar_config(config: dict):
    pasta = caminho_estudo(config["slug"])
    with open(pasta / "config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def aplicar_ressincronizacao(config: dict, novos_eixos: dict) -> dict:
    """
    Versão PURA (sem I/O) da ressincronização: só transforma o dict do config
    em memória. Quem chama decide onde/como persistir depois (disco local,
    Google Drive, etc.). Usada tanto pelo fluxo local (ressincronizar_eixos,
    abaixo) quanto pelo fluxo via Drive (estudo_manager_drive.py).

    Atualiza eixo/qtd/pontos/temas/pesos a partir de uma nova leitura do Excel
    de conteúdo programático, SEM perder o que já existe:
      - simulacao_atual continua de onde estava
      - historico_temas é preservado para eixos/temas que já existiam
      - eixos novos entram com histórico vazio
      - temas removidos do Excel saem do histórico (não tem mais o que evitar)
    """
    historico_antigo = config.get("historico_temas", {})
    novo_historico = {}

    for eixo, dados in novos_eixos.items():
        temas_validos = set(dados["temas"].keys())
        usados_antes = historico_antigo.get(eixo, [])
        novo_historico[eixo] = [t for t in usados_antes if t in temas_validos]

    config["eixos"] = novos_eixos
    config["historico_temas"] = novo_historico
    return config


def ressincronizar_eixos(config: dict, novos_eixos: dict) -> dict:
    """Versão para o fluxo LOCAL (CLI): aplica a transformação e já salva em disco."""
    config = aplicar_ressincronizacao(config, novos_eixos)
    salvar_config(config)
    return config


def carregar_config(slug: str) -> dict:
    pasta = caminho_estudo(slug)
    caminho = pasta / "config.json"
    if not caminho.exists():
        raise FileNotFoundError(f"Estudo '{slug}' não encontrado.")
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)
