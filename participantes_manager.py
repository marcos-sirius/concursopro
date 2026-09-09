"""
Lista de participantes autorizados a responder simulados, e quais estudos
cada um pode acessar. Fica em UM ÚNICO arquivo (participantes.json) na RAIZ
da pasta do Drive — não dentro de um estudo específico, já que um mesmo
participante pode ter acesso a vários estudos diferentes.

Formato do participantes.json:
{
  "participantes": [
    {"nome": "Ana", "codigo": "1234", "estudos_permitidos": ["dataprev_inteligencia_informacao"]},
    {"nome": "Marcos", "codigo": "0000", "estudos_permitidos": ["*"]}
  ]
}

"*" em estudos_permitidos = acesso a TODOS os estudos (atuais e futuros).
"""
import json

import drive_storage as ds

NOME_ARQUIVO = "participantes.json"
TODOS = "*"


def carregar_participantes() -> list:
    arquivo = ds.buscar_arquivo(NOME_ARQUIVO, ds.root_folder_id())
    if not arquivo:
        return []
    dados = ds.baixar_bytes(arquivo["id"])
    return json.loads(dados.decode("utf-8")).get("participantes", [])


def salvar_participantes(participantes: list):
    arquivo = ds.buscar_arquivo(NOME_ARQUIVO, ds.root_folder_id())
    dados = json.dumps({"participantes": participantes}, ensure_ascii=False, indent=2).encode("utf-8")
    ds.salvar_bytes(NOME_ARQUIVO, dados, ds.root_folder_id(), "application/json",
                     file_id=arquivo["id"] if arquivo else None)


def autenticar(codigo: str) -> dict | None:
    """Devolve o participante cujo código bate, ou None se inválido."""
    codigo = (codigo or "").strip()
    if not codigo:
        return None
    for p in carregar_participantes():
        if p.get("codigo") == codigo:
            return p
    return None


def pode_acessar(participante: dict, slug_estudo: str) -> bool:
    permitidos = participante.get("estudos_permitidos", [])
    return TODOS in permitidos or slug_estudo in permitidos


def adicionar_ou_atualizar(nome: str, codigo: str, estudos_permitidos: list):
    """
    Adiciona um novo participante ou substitui um já existente com o mesmo
    código (permite editar permissões de alguém já cadastrado).
    """
    participantes = [p for p in carregar_participantes() if p.get("codigo") != codigo]
    participantes.append({
        "nome": nome,
        "codigo": codigo,
        "estudos_permitidos": estudos_permitidos,
    })
    salvar_participantes(participantes)


def remover(codigo: str):
    participantes = [p for p in carregar_participantes() if p.get("codigo") != codigo]
    salvar_participantes(participantes)
