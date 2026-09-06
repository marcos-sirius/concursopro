"""
Autenticação OAuth2 com o Google, com escopo de acesso ao Drive.

Por que não usar o st.login() nativo do Streamlit?
Porque ele fixa o escopo em "openid profile email" e não permite customizar
para incluir o Drive (https://www.googleapis.com/auth/drive) — isso é uma
limitação conhecida do Streamlit (issue #11703 no GitHub deles). Então
implementamos o fluxo OAuth2 "na mão" aqui, com requests puro.

Fluxo:
  1. Usuário clica em "Entrar com Google" -> vai para gerar_link_login()
  2. Google pede autorização e redireciona de volta para o app com ?code=...
  3. processar_callback() troca esse code por um access_token + refresh_token
  4. obter_access_token() devolve um token válido, renovando via refresh_token
     automaticamente quando expira (tokens de acesso do Google duram ~1h)

IMPORTANTE (app em modo "Testing" no Google Cloud Console):
  Enquanto o app OAuth não passar pela verificação do Google (não é
  necessário para uso pessoal), o refresh_token expira a cada 7 dias.
  Isso significa que, de tempos em tempos, você vai precisar clicar em
  "Entrar com Google" de novo. Não é um bug, é uma restrição do Google
  para apps não verificados.
"""
import time

import requests
import streamlit as st

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive"


def _client_id() -> str:
    return st.secrets["google_oauth"]["client_id"]


def _client_secret() -> str:
    return st.secrets["google_oauth"]["client_secret"]


def _redirect_uri() -> str:
    return st.secrets["google_oauth"]["redirect_uri"]


def gerar_link_login() -> str:
    """Monta a URL de consentimento do Google para o botão 'Entrar com Google'."""
    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",   # necessário para receber refresh_token
        "prompt": "consent",        # força reemissão do refresh_token sempre
    }
    query = "&".join(f"{k}={requests.utils.quote(v, safe='')}" for k, v in params.items())
    return f"{AUTH_URL}?{query}"


def _trocar_code_por_token(code: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        "code": code,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
    })
    resp.raise_for_status()
    return resp.json()


def _renovar_access_token(refresh_token: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        "refresh_token": refresh_token,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "grant_type": "refresh_token",
    })
    resp.raise_for_status()
    return resp.json()


def processar_callback():
    """
    Chamar isso no topo do app, antes de qualquer outra coisa. Se o Google
    acabou de redirecionar de volta com ?code=... na URL, troca por token e
    guarda na sessão.
    """
    if "google_tokens" in st.session_state:
        return

    code = st.query_params.get("code")
    if not code:
        return

    tokens = _trocar_code_por_token(code)
    tokens["_obtido_em"] = time.time()
    st.session_state["google_tokens"] = tokens
    st.query_params.clear()
    st.rerun()


def obter_access_token() -> str | None:
    """Devolve um access_token válido, renovando automaticamente se preciso."""
    tokens = st.session_state.get("google_tokens")
    if not tokens:
        return None

    expira_em = tokens.get("_obtido_em", 0) + tokens.get("expires_in", 0) - 60
    if time.time() < expira_em:
        return tokens["access_token"]

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        # token expirou e não temos como renovar -> precisa logar de novo
        st.session_state.pop("google_tokens", None)
        return None

    try:
        novos = _renovar_access_token(refresh_token)
    except requests.HTTPError:
        st.session_state.pop("google_tokens", None)
        return None

    novos["refresh_token"] = refresh_token
    novos["_obtido_em"] = time.time()
    st.session_state["google_tokens"] = novos
    return novos["access_token"]


def esta_logado() -> bool:
    return obter_access_token() is not None


def logout():
    st.session_state.pop("google_tokens", None)
    st.session_state.pop("estudo_folder_id", None)
    st.session_state.pop("estudo_config", None)
