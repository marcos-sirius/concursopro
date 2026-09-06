"""
Camada de armazenamento no Google Drive, equivalente ao que config_manager.py
e excel_manager.py faziam com Path/disco local — mas aqui cada estudo vira uma
SUBPASTA dentro de uma pasta raiz no Drive (ROOT_FOLDER_ID, em st.secrets),
contendo:
  - config.json    (mesmo conteúdo de sempre)
  - simulado.xlsx  (mesmo arquivo de sempre)

Usa chamadas REST diretas à API do Drive v3 (sem biblioteca google-api-python-
client, pra manter a dependência mínima), autenticando com o access_token do
usuário logado (google_drive_auth.py).
"""
import io
import json

import requests
import streamlit as st
from openpyxl import Workbook, load_workbook

import google_drive_auth as auth

API_FILES = "https://www.googleapis.com/drive/v3/files"
API_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _headers() -> dict:
    token = auth.obter_access_token()
    if not token:
        raise RuntimeError("Usuário não autenticado no Google.")
    return {"Authorization": f"Bearer {token}"}


def root_folder_id() -> str:
    return st.secrets["google_drive"]["root_folder_id"]


def _buscar(nome: str, parent_id: str, mime: str | None = None) -> dict | None:
    nome_escapado = nome.replace("'", r"\'")
    q = f"name = '{nome_escapado}' and '{parent_id}' in parents and trashed = false"
    if mime:
        q += f" and mimeType = '{mime}'"
    resp = requests.get(API_FILES, headers=_headers(), params={
        "q": q, "fields": "files(id, name, mimeType)",
    })
    resp.raise_for_status()
    arquivos = resp.json().get("files", [])
    return arquivos[0] if arquivos else None


def listar_estudos() -> list:
    """Lista as subpastas (cada uma = um estudo) dentro da pasta raiz."""
    resp = requests.get(API_FILES, headers=_headers(), params={
        "q": f"'{root_folder_id()}' in parents and mimeType = '{MIME_FOLDER}' and trashed = false",
        "fields": "files(id, name)",
    })
    resp.raise_for_status()
    return sorted(resp.json().get("files", []), key=lambda f: f["name"])


def obter_ou_criar_pasta_estudo(slug: str) -> str:
    existente = _buscar(slug, root_folder_id(), MIME_FOLDER)
    if existente:
        return existente["id"]

    resp = requests.post(API_FILES, headers=_headers(), json={
        "name": slug,
        "mimeType": MIME_FOLDER,
        "parents": [root_folder_id()],
    })
    resp.raise_for_status()
    return resp.json()["id"]


def _baixar_bytes(file_id: str) -> bytes:
    resp = requests.get(f"{API_FILES}/{file_id}", headers=_headers(), params={"alt": "media"})
    resp.raise_for_status()
    return resp.content


def _upload_bytes(nome: str, dados: bytes, parent_id: str, mime_type: str,
                   file_id: str | None = None) -> dict:
    metadata = {"name": nome}
    if not file_id:
        metadata["parents"] = [parent_id]

    boundary = "eara_boundary_simulador"
    corpo = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8") + dados + f"\r\n--{boundary}--".encode("utf-8")

    headers = _headers()
    headers["Content-Type"] = f"multipart/related; boundary={boundary}"

    if file_id:
        resp = requests.patch(f"{API_UPLOAD}/{file_id}?uploadType=multipart",
                               headers=headers, data=corpo)
    else:
        resp = requests.post(f"{API_UPLOAD}?uploadType=multipart",
                              headers=headers, data=corpo)
    resp.raise_for_status()
    return resp.json()


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
        wb.remove(wb.active)  # equivalente ao início de excel_manager.abrir_ou_criar
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
