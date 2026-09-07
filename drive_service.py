"""
Acesso ao Google Drive via SERVICE ACCOUNT (conta-robô), em vez de login
OAuth pessoal.

Por que a mudança (era OAuth antes)?
Quando só você usava o app, o login OAuth com sua conta pessoal fazia
sentido. Mas quando outras pessoas passam a RESPONDER simulados (sem
precisar gerenciar nada), exigir que cada uma faça login Google e seja
cadastrada como "testadora" no Google Cloud Console é inviável.

A Service Account resolve isso: é uma identidade do Google que só existe
pra esse app, sem tela de login, sem expiração de 7 dias, sem cadastro de
testador por pessoa. Você só precisa compartilhar sua pasta do Drive com o
e-mail dela (feito uma única vez, veja o guia de configuração).

st.secrets precisa ter uma seção [google_service_account] com o CONTEÚDO
INTEIRO do arquivo .json baixado do Google Cloud Console (veja
secrets.toml.exemplo para o formato exato).
"""
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive"]


@st.cache_resource(show_spinner=False)
def obter_service():
    """
    Cria (e cacheia, uma vez por processo do Streamlit) o cliente da API do
    Drive v3, autenticado como a Service Account.
    """
    info = dict(st.secrets["google_service_account"])
    credenciais = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES
    )
    return build("drive", "v3", credentials=credenciais, cache_discovery=False)
