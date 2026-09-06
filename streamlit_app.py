"""
Simulador EARA — versão web (Streamlit + Google Drive)
========================================================
Mesma lógica de negócio do projeto original (sorteio.py, ia_gerador.py,
excel_manager.py), só que:
  - a interação é por botões/formulários em vez de input() de terminal
  - a persistência (config.json e simulado.xlsx) é no Google Drive do
    usuário em vez de disco local, via login OAuth com escopo do Drive

Configuração necessária em .streamlit/secrets.toml (local) ou em
"Settings -> Secrets" no Streamlit Community Cloud (produção):

    OPENAI_API_KEY = "sk-..."

    [google_oauth]
    client_id     = "SEU_CLIENT_ID.apps.googleusercontent.com"
    client_secret = "SEU_CLIENT_SECRET"
    redirect_uri  = "https://SEU-APP.streamlit.app/"   # URL do próprio app

    [google_drive]
    root_folder_id = "104KRKkS1hSEfbB6rGfbTpt0kfd0DlyyX"
"""
import os

import streamlit as st

# precisa setar a env var ANTES de importar ia_gerador, pois ele lê
# OPENAI_API_KEY no momento do import (senão levanta RuntimeError)
if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = st.secrets.get("OPENAI_API_KEY", "")

import config_manager as cm
import conteudo_loader as cl
import excel_manager as em
import estudo_manager_drive as emd
import google_drive_auth as auth
import drive_storage as ds
import ia_gerador as ia
import sorteio

st.set_page_config(page_title="Simulador EARA", page_icon="📚", layout="centered")

auth.processar_callback()

st.title("📚 Simulador EARA")

# ---------------- Login ----------------
if not auth.esta_logado():
    st.write(
        "Faça login com sua conta Google para acessar seus estudos "
        "salvos no Drive."
    )
    st.link_button("Entrar com Google", auth.gerar_link_login(), type="primary")
    st.stop()

topo_esq, topo_dir = st.columns([4, 1])
with topo_dir:
    if st.button("Sair"):
        auth.logout()
        st.rerun()

# ---------------- Seleção / criação de estudo ----------------
estudos = emd.listar_estudos()
opcoes = ["➕ Novo estudo"] + [nome for nome, _ in estudos]
escolha = st.selectbox("Estudo", opcoes)

if escolha == "➕ Novo estudo":
    with st.form("form_novo_estudo"):
        nome_estudo = st.text_input("Nome do estudo/concurso")
        banca = st.text_input("Banca examinadora")
        nivel = st.text_input("Nível (ex: Superior)")
        arquivo_excel = st.file_uploader(
            "Excel de conteúdo programático (.xlsx)", type=["xlsx"]
        )
        enviar = st.form_submit_button("Criar estudo")

    if enviar:
        if not (nome_estudo and banca and nivel and arquivo_excel):
            st.error("Preencha todos os campos e envie o Excel de conteúdo programático.")
            st.stop()
        try:
            eixos = cl.carregar_conteudo_programatico(arquivo_excel)
        except Exception as e:
            st.error(f"Erro ao ler o Excel: {e}")
            st.stop()

        config, folder_id = emd.criar_estudo(nome_estudo, banca, nivel, eixos)
        st.session_state["estudo_folder_id"] = folder_id
        st.session_state["estudo_config"] = config
        st.success(f"Estudo '{nome_estudo}' criado! Selecione-o na lista acima.")
        st.rerun()
    st.stop()

else:
    folder_id = dict(estudos)[escolha]
    if st.session_state.get("estudo_folder_id") != folder_id:
        st.session_state["estudo_folder_id"] = folder_id
        st.session_state["estudo_config"] = emd.carregar_config(folder_id)

config = st.session_state["estudo_config"]
folder_id = st.session_state["estudo_folder_id"]

st.subheader(config["nome_estudo"])
st.caption(
    f"Banca: {config['banca']} · Nível: {config['nivel']} · "
    f"Simulações já geradas: {config['simulacao_atual']}"
)

with st.expander("🔄 Ressincronizar eixos/temas a partir de um novo Excel"):
    st.caption(
        "Atualiza quantidades/pesos/temas preservando o histórico de temas já "
        "cobertos (o mesmo comportamento do ressincronizar_eixos do CLI)."
    )
    novo_arquivo = st.file_uploader("Excel atualizado", type=["xlsx"], key="resync_uploader")
    if novo_arquivo and st.button("Ressincronizar agora"):
        try:
            novos_eixos = cl.carregar_conteudo_programatico(novo_arquivo)
        except Exception as e:
            st.error(f"Erro ao ler o Excel: {e}")
            st.stop()
        config = emd.ressincronizar_eixos(folder_id, config, novos_eixos)
        st.session_state["estudo_config"] = config
        st.success("Eixos/temas atualizados com sucesso.")
        st.rerun()

st.divider()

# ---------------- Geração do simulado ----------------
if st.button("🎲 Gerar novo simulado", type="primary"):
    numero_simulacao = config["simulacao_atual"] + 1

    with st.status(f"Gerando simulação nº {numero_simulacao}...", expanded=True) as status:
        wb = ds.baixar_workbook(folder_id)
        eixos_questoes = {}

        for eixo, dados in config["eixos"].items():
            st.write(f"**{eixo}**")
            distribuicao = sorteio.distribuir_temas(config, eixo, dados["qtd_questoes"])

            blocos_eixo = []
            for tema, qtd_tema in distribuicao.items():
                evitar = em.perguntas_ja_usadas(wb, eixo, tema=tema, limite=30)
                st.write(f"　↳ {qtd_tema} questão(ões) — {tema[:70]}")
                questoes = ia.gerar_bloco(
                    banca=config["banca"],
                    nivel=config["nivel"],
                    tema=tema,
                    qtd=qtd_tema,
                    evitar=evitar,
                    tamanho_bloco=dados.get("tamanho_bloco", 10),
                )
                if questoes:
                    blocos_eixo.append((tema, questoes))
                else:
                    st.write(f"　⚠️ Falhou ao gerar: {tema}")

            # balanceamento de gabarito por EIXO (junta todos os temas antes)
            todas_questoes_eixo = [q for _, qs in blocos_eixo for q in qs]
            if todas_questoes_eixo:
                ia.balancear_gabaritos(todas_questoes_eixo)

            if not blocos_eixo:
                st.write(f"　⚠️ Nenhuma questão gerada para {eixo} — guia ficará vazia.")
                em.resetar_guia_eixo(wb, eixo)
                continue

            em.resetar_guia_eixo(wb, eixo)
            for tema, questoes in blocos_eixo:
                em.gravar_questoes_no_eixo(wb, eixo, tema, questoes)
            eixos_questoes[eixo] = blocos_eixo
            st.write(f"　✓ {len(todas_questoes_eixo)} questões gravadas ({len(blocos_eixo)} temas).")

        em.consolidar(wb, numero_simulacao, eixos_questoes)
        ds.salvar_workbook(folder_id, wb)

        config["simulacao_atual"] = numero_simulacao
        emd.salvar_config(folder_id, config)
        st.session_state["estudo_config"] = config

        status.update(label=f"✅ Simulado nº {numero_simulacao} concluído!", state="complete")

    st.success("Arquivo `simulado.xlsx` atualizado na pasta do estudo no seu Google Drive.")
