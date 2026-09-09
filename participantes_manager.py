"""
Lista de participantes autorizados a responder simulados, e quais estudos
cada um pode acessar. Fica em UM ÚNICO arquivo (participantes.json) na RAIZ
da pasta do Drive — não dentro de um estudo específico, já que um mesmo
participante pode ter acesso a vários estudos diferentes.

Login por USUÁRIO + SENHA (não é mais um código único):
  - "usuario" NÃO diferencia maiúsculas de minúsculas — nem pra login, nem
    pro agrupamento no dashboard de Desempenho ("Ana", "ana" e "ANA" são a
    mesma pessoa). Internamente, sempre comparamos em minúsculo, mas o valor
    exibido/gravado usa a grafia ORIGINAL cadastrada pelo admin.
  - "senha" nunca é guardada em texto puro — só o hash SHA-256 dela. Não é
    criptografia de nível bancário (sem salt por usuário), mas já é bem
    melhor que texto puro pra esse caso de uso pessoal/familiar.

Formato do participantes.json:
{
  "participantes": [
    {
      "usuario": "Ana",
      "email": "ana@example.com",
      "senha_hash": "<sha256 da senha>",
      "estudos_permitidos": ["dataprev_inteligencia_informacao"]
    }
  ]
}

"*" em estudos_permitidos = acesso a TODOS os estudos (atuais e futuros).
"""
import hashlib
import json

import drive_storage as ds

NOME_ARQUIVO = "participantes.json"
TODOS = "*"


def _hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode("utf-8")).hexdigest()


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


def _buscar_por_usuario(participantes: list, usuario: str) -> dict | None:
    chave = (usuario or "").strip().lower()
    return next(
        (p for p in participantes if p.get("usuario", "").strip().lower() == chave),
        None,
    )


def autenticar(usuario: str, senha: str) -> dict | None:
    """Login por usuário (case-insensitive) + senha. Devolve o participante ou None."""
    usuario = (usuario or "").strip()
    senha = senha or ""
    if not usuario or not senha:
        return None

    participante = _buscar_por_usuario(carregar_participantes(), usuario)
    if participante and participante.get("senha_hash") == _hash_senha(senha):
        return participante
    return None


def pode_acessar(participante: dict, slug_estudo: str) -> bool:
    permitidos = participante.get("estudos_permitidos", [])
    return TODOS in permitidos or slug_estudo in permitidos


def adicionar_ou_atualizar(usuario: str, email: str, senha: str | None, estudos_permitidos: list):
    """
    Adiciona um novo participante ou atualiza um já existente com o mesmo
    usuário (comparação sem diferenciar maiúsculas/minúsculas).

    'senha' pode vir em branco/None ao EDITAR alguém que já existe — nesse
    caso a senha atual é preservada (não dá pra "editar em branco" um
    participante novo, senha é obrigatória na primeira vez).
    """
    usuario_normalizado = (usuario or "").strip()
    if not usuario_normalizado:
        raise ValueError("Usuário é obrigatório.")

    existentes = carregar_participantes()
    existente = _buscar_por_usuario(existentes, usuario_normalizado)

    if senha:
        senha_hash = _hash_senha(senha)
    elif existente:
        senha_hash = existente.get("senha_hash")
    else:
        raise ValueError("Senha é obrigatória para cadastrar um novo participante.")

    participantes = [
        p for p in existentes
        if p.get("usuario", "").strip().lower() != usuario_normalizado.lower()
    ]
    participantes.append({
        "usuario": usuario_normalizado,
        "email": (email or "").strip(),
        "senha_hash": senha_hash,
        "estudos_permitidos": estudos_permitidos,
    })
    salvar_participantes(participantes)


def remover(usuario: str):
    chave = (usuario or "").strip().lower()
    participantes = [
        p for p in carregar_participantes()
        if p.get("usuario", "").strip().lower() != chave
    ]
    salvar_participantes(participantes)

