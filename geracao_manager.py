"""
Checkpoint de geração de simulado — guardado como UM ARQUIVO por estudo
(geracao_em_andamento.json, na própria pasta do estudo no Drive), separado
do simulado.xlsx de propósito: é pequeno e rápido de gravar a cada tema
processado, sem precisar reabrir/regravar a planilha inteira toda hora.

Serve pra dois cenários, com o MESMO mecanismo:
  1. Erro no meio da geração (falha de API) -> para ali, salva o que já foi
     feito, não continua gastando chamada.
  2. Você clica em "Cancelar" -> a chamada que já estava rodando termina
     normalmente, mas a PRÓXIMA não começa.

Na próxima vez que for gerar um simulado nesse estudo, se sobrar um
checkpoint com status "em_andamento", "erro" ou "cancelado", o app oferece
"Retomar" (só gera o que falta) ou "Começar do zero".

Formato do checkpoint:
{
  "numero_simulacao": 8,
  "status": "em_andamento" | "erro" | "cancelado",
  "erro_mensagem": null,
  "eixos": {
    "Português": {
      "distribuicao": {"tema A": 2, "tema B": 1},   # decidida uma vez só
      "prontos": {"tema A": [ {questão...}, ... ]}  # temas já gerados
    }
  }
}
"""
import json

import drive_storage as ds

NOME_ARQUIVO = "geracao_em_andamento.json"


def carregar_checkpoint(folder_id_estudo: str) -> dict | None:
    arquivo = ds.buscar_arquivo(NOME_ARQUIVO, folder_id_estudo)
    if not arquivo:
        return None
    dados = ds.baixar_bytes(arquivo["id"])
    return json.loads(dados.decode("utf-8"))


def salvar_checkpoint(folder_id_estudo: str, checkpoint: dict):
    arquivo = ds.buscar_arquivo(NOME_ARQUIVO, folder_id_estudo)
    dados = json.dumps(checkpoint, ensure_ascii=False, indent=2).encode("utf-8")
    ds.salvar_bytes(NOME_ARQUIVO, dados, folder_id_estudo, "application/json",
                     file_id=arquivo["id"] if arquivo else None)


def apagar_checkpoint(folder_id_estudo: str):
    arquivo = ds.buscar_arquivo(NOME_ARQUIVO, folder_id_estudo)
    if arquivo:
        ds.mover_para_lixeira(arquivo["id"])


def checkpoint_iniciar(numero_simulacao: int) -> dict:
    return {
        "numero_simulacao": numero_simulacao,
        "status": "em_andamento",
        "erro_mensagem": None,
        "eixos": {},
    }


def progresso_resumo(checkpoint: dict) -> str:
    """Texto curto tipo '3 de 5 eixos prontos, faltam: Atualidades, Direito'."""
    eixos = checkpoint.get("eixos", {})
    prontos = [e for e, d in eixos.items() if not _tem_pendente(d)]
    pendentes = [e for e in eixos if e not in prontos]
    return prontos, pendentes


def _tem_pendente(dados_eixo: dict) -> bool:
    distribuicao = dados_eixo.get("distribuicao", {})
    prontos = dados_eixo.get("prontos", {})
    return any(t not in prontos for t in distribuicao)
