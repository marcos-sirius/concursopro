"""
Lista de categorias de estudo (ex: "Analista de Dados", "Contador",
"Psicóloga"), cadastradas previamente pelo admin numa tela própria — em vez
de digitar uma categoria nova toda vez que cria um estudo (evita duplicata
por erro de digitação, tipo "Contador" e "contador" virando duas coisas
diferentes). Fica em UM ÚNICO arquivo (categorias.json) na RAIZ da pasta do
Drive, no mesmo espírito do participantes.json.
"""
import json

import drive_storage as ds

NOME_ARQUIVO = "categorias.json"
CATEGORIA_PADRAO = "Geral"


def carregar_categorias() -> list:
    arquivo = ds.buscar_arquivo(NOME_ARQUIVO, ds.root_folder_id())
    if not arquivo:
        return [CATEGORIA_PADRAO]
    dados = ds.baixar_bytes(arquivo["id"])
    categorias = json.loads(dados.decode("utf-8")).get("categorias", [])
    return categorias or [CATEGORIA_PADRAO]


def salvar_categorias(categorias: list):
    arquivo = ds.buscar_arquivo(NOME_ARQUIVO, ds.root_folder_id())
    dados = json.dumps({"categorias": categorias}, ensure_ascii=False, indent=2).encode("utf-8")
    ds.salvar_bytes(NOME_ARQUIVO, dados, ds.root_folder_id(), "application/json",
                     file_id=arquivo["id"] if arquivo else None)


def adicionar_categoria(nome: str) -> list:
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Digite um nome pra categoria.")

    categorias = carregar_categorias()
    if any(c.strip().lower() == nome.lower() for c in categorias):
        raise ValueError(f"A categoria '{nome}' já existe.")

    categorias.append(nome)
    salvar_categorias(categorias)
    return categorias


def remover_categoria(nome: str) -> list:
    """
    Remove da LISTA de opções — não mexe em estudos que já usam essa
    categoria (eles continuam com o valor gravado, só param de aparecer
    como opção pra estudos novos).
    """
    categorias = [c for c in carregar_categorias() if c.strip().lower() != (nome or "").strip().lower()]
    salvar_categorias(categorias)
    return categorias
