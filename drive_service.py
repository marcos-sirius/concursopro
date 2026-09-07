"""
Acesso ao Google Drive via TOKEN DE LONGA DURAÇÃO da sua conta pessoal do
Google, em vez de Service Account.

Por que a mudança (era Service Account antes)?
Service Accounts não têm cota de armazenamento própria no Google Drive —
seu limite é 0 bytes. Elas conseguem criar pastas (não ocupam espaço), mas
falham ao criar QUALQUER arquivo com conteúdo (como o config.json de um
estudo novo), porque esse arquivo "pertenceria" à Service Account, que não
tem onde guardá-lo. As soluções oficiais do Google pra isso (Shared Drives
ou delegação OAuth) exigem Google Workspace pago.

Por que não voltar ao login OAuth "cada visitante loga" de antes?
Porque, com o app OAuth em modo "Testing" no Google Cloud Console, o
refresh_token expira a cada 7 dias — inviável para quem só vai RESPONDER
um simulado.

A solução: mudar o app pra "In production" no Google Cloud Console (não
exige a verificação completa do Google pra uso como este — só aparece um
aviso "app não verificado" pra você mesmo, uma única vez, ao autorizar).
Isso faz o refresh_token deixar de expirar. Você autoriza UMA VEZ, rodando
obter_refresh_token.py no seu computador, e guarda o refresh_token nos
Secrets. O app usa esse token pra tudo (gestão e respostas de terceiros),
sem ninguém mais precisar logar — e como é sua conta de verdade, tem cota
de armazenamento normal.

st.secrets precisa ter uma seção [google_oauth] com client_id,
client_secret e refresh_token (veja secrets.toml.exemplo e o guia em
obter_refresh_token.py para como conseguir cada um).
"""
import streamlit as st
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


@st.cache_resource(show_spinner=False)
def obter_service():
    """
    Cria (e cacheia, uma vez por processo do Streamlit) o cliente da API do
    Drive v3, autenticado com o token de longa duração da sua conta.

    Não passamos um access_token pronto — só o refresh_token. A biblioteca
    do Google (google-auth) troca isso por um access_token válido
    automaticamente antes da primeira chamada, e renova sozinha sempre que
    expira (o access_token dura ~1h, mas o refresh_token não expira mais,
    já que o app está em modo "In production").
    """
    cfg = st.secrets["google_oauth"]
    credenciais = Credentials(
        token=None,
        refresh_token=cfg["refresh_token"],
        token_uri=TOKEN_URI,
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=credenciais, cache_discovery=False)
