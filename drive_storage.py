"""
Camada de armazenamento no Google Drive — equivalente ao que config_manager.py
e excel_manager.py faziam com Path/disco local, agora usando a Service Account
(drive_service.py) em vez de OAuth pessoal.

Estrutura no Drive (dentro da pasta raiz, em st.secrets["google_drive"]["root_folder_id"]):
  <pasta raiz>/
    <slug_do_estudo_1>/
        config.json
        simulado.xlsx      (guias por eixo + Consolidado + Resultados)
    <slug_do_estudo_2>/
        ...
"""
import io
import json

import streamlit as st
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from openpyxl import Workbook, load_workbook

import drive_service as dsvc

MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def root_folder_id() -> str:
    return st.secrets["google_drive"]["root_folder_id"]


def _buscar(nome: str, parent_id: str, mime: str | None = None) -> dict | None:
    service = dsvc.obter_service()
    nome_escapado = nome.replace("'", r"\'")
    q = f"name = '{nome_escapado}' and '{parent_id}' in parents and trashed = false"
    if mime:
        q += f" and mimeType = '{mime}'"
    resp = service.files().list(q=q, fields="files(id, name, mimeType)").execute()
    arquivos = resp.get("files", [])
    return arquivos[0] if arquivos else None


def listar_estudos() -> list:
    """Lista as subpastas (cada uma = um estudo) dentro da pasta raiz."""
    service = dsvc.obter_service()
    resp = service.files().list(
        q=f"'{root_folder_id()}' in parents and mimeType = '{MIME_FOLDER}' and trashed = false",
        fields="files(id, name)",
    ).execute()
    return sorted(resp.get("files", []), key=lambda f: f["name"])


def obter_ou_criar_pasta_estudo(slug: str) -> str:
    existente = _buscar(slug, root_folder_id(), MIME_FOLDER)
    if existente:
        return existente["id"]

    service = dsvc.obter_service()
    metadata = {"name": slug, "mimeType": MIME_FOLDER, "parents": [root_folder_id()]}
    criado = service.files().create(body=metadata, fields="id").execute()
    return criado["id"]


def _baixar_bytes(file_id: str) -> bytes:
    service = dsvc.obter_service()
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=file_id))
    concluido = False
    while not concluido:
        _, concluido = downloader.next_chunk()
    return buffer.getvalue()


def _upload_bytes(nome: str, dados: bytes, parent_id: str, mime_type: str,
                   file_id: str | None = None) -> dict:
    service = dsvc.obter_service()
    media = MediaIoBaseUpload(io.BytesIO(dados), mimetype=mime_type, resumable=False)

    if file_id:
        return service.files().update(fileId=file_id, media_body=media).execute()

    metadata = {"name": nome, "parents": [parent_id]}
    return service.files().create(body=metadata, media_body=media, fields="id").execute()


# ---------------- config.json ----------------

def ler_config(pasta_estudo_id: str) -> dict:
    arquivo = _buscar("config.json", pasta_estudo_id)
    if not arquivo:
        raise FileNotFoundError("config.json não encontrado na pasta do estudo no Drive.")
    dados = _baixar_bytes(arquivo["id"])
    return json.loads(dados.decode("utf-8"))


def salvar_config(pasta_estudo_id: str, config: dict):
    arquivo = _buscar("config.json", pasta_estudo_id)
    dados = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
    _upload_bytes("config.json", dados, pasta_estudo_id, "application/json",
                  file_id=arquivo["id"] if arquivo else None)


# ---------------- simulado.xlsx ----------------

def baixar_workbook(pasta_estudo_id: str) -> Workbook:
    arquivo = _buscar("simulado.xlsx", pasta_estudo_id)
    if not arquivo:
        wb = Workbook()
        wb.remove(wb.active)
        return wb
    dados = _baixar_bytes(arquivo["id"])
    return load_workbook(io.BytesIO(dados))


def salvar_workbook(pasta_estudo_id: str, wb: Workbook):
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    arquivo = _buscar("simulado.xlsx", pasta_estudo_id)
    _upload_bytes("simulado.xlsx", buffer.read(), pasta_estudo_id, MIME_XLSX,
                  file_id=arquivo["id"] if arquivo else None)
