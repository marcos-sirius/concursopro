"""
Proteção leve (senha única) para a área de GESTÃO de estudos.

Por que senha em vez de login Google?
Porque o acesso ao Drive é feito por um token de longa duração único, fixo
nos Secrets (drive_service.py), não depende de QUEM está logado. A senha aqui serve só pra impedir que
qualquer pessoa que ache a URL crie/apague estudos ou dispare gerações de
simulado (que consomem sua cota da API de IA). Quem só vai RESPONDER um
simulado não passa por essa tela.

Configuração: st.secrets["admin_password"] = "sua-senha-aqui"
"""
import streamlit as st


def esta_autenticado() -> bool:
    return st.session_state.get("admin_ok", False)


def tela_login():
    st.subheader("🔒 Área de gestão")
    senha = st.text_input("Senha de administrador", type="password")
    if st.button("Entrar"):
        if senha == st.secrets.get("admin_password", ""):
            st.session_state["admin_ok"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")


def logout():
    st.session_state.pop("admin_ok", None)
