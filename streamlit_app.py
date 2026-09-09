"""
Simulador EARA — versão web (Streamlit + Google Drive via token de longa duração)
============================================================================

Três áreas:

1. "Responder simulado" — protegida por USUÁRIO + SENHA individuais (não é
   senha de admin: cada participante tem login próprio, cadastrado em
   "Gestão de estudos", que define QUAIS estudos aquela pessoa pode ver).

2. "Gestão de estudos" — protegida por senha (admin_password nos Secrets).
   Criar estudos, ressincronizar eixos, gerar novos simulados via IA,
   cadastrar participantes e definir o que cada um pode acessar.

3. "Desempenho" — protegida por senha. Dashboard com o histórico de
   respostas de todo mundo, cruzando por participante/eixo/tema/simulado.

Secrets necessários (.streamlit/secrets.toml local, ou "Settings > Secrets"
no Streamlit Community Cloud):

    OPENAI_API_KEY = "sk-..."
    admin_password = "sua-senha-aqui"

    [google_drive]
    root_folder_id = "104KRKkS1hSEfbB6rGfbTpt0kfd0DlyyX"

    [google_oauth]
    # gerado uma única vez rodando obter_refresh_token.py no seu computador
    client_id = "SUBSTITUA.apps.googleusercontent.com"
    client_secret = "SUBSTITUA"
    refresh_token = "SUBSTITUA"
"""

import os

import streamlit as st

# precisa setar a env var ANTES de importar ia_gerador (ele lê no import)
if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = st.secrets.get("OPENAI_API_KEY", "")

import admin_auth
import config_manager as cm
import conteudo_loader as cl
import drive_storage as ds
import estudo_manager_drive as emd
import excel_manager as em
import ia_gerador as ia
import participantes_manager as pm
import resultados_manager as rm
import sorteio

st.set_page_config(page_title="Simulador EARA", page_icon="📚", layout="centered")

PAGINAS = ["Responder simulado", "Gestão de estudos", "Desempenho"]
pagina = st.sidebar.radio("Navegação", PAGINAS)


# =========================================================================
# PÁGINA 1 — RESPONDER SIMULADO (pública, sem senha)
# =========================================================================
def pagina_responder():
    st.title("📝 Responder simulado")

    # --- Login por usuário + senha ---
    if "participante" not in st.session_state:
        st.write("Entre com seu usuário e senha pra ver os estudos liberados pra você.")
        usuario_login = st.text_input("Usuário")
        senha_login = st.text_input("Senha", type="password")
        if st.button("Entrar", type="primary"):
            participante = pm.autenticar(usuario_login, senha_login)
            if participante:
                st.session_state["participante"] = participante
                st.rerun()
            else:
                st.error("Usuário ou senha inválidos. Confira com quem te cadastrou.")
        return

    participante = st.session_state["participante"]

    topo_esq, topo_dir = st.columns([4, 1])
    with topo_esq:
        st.caption(f"Logado como: **{participante['usuario']}**")
    with topo_dir:
        if st.button("Trocar"):
            del st.session_state["participante"]
            st.rerun()

    # --- Filtra estudos pelo que esse participante pode acessar ---
    todos_estudos = emd.listar_estudos()
    estudos_permitidos = [
        (nome, folder_id) for nome, folder_id in todos_estudos
        if pm.pode_acessar(participante, nome)
    ]

    if not estudos_permitidos:
        st.info("Você ainda não tem acesso a nenhum estudo. Fale com quem te cadastrou.")
        return

    nome_estudo = st.selectbox("Estudo", [n for n, _ in estudos_permitidos])
    folder_id = dict(estudos_permitidos)[nome_estudo]

    wb = ds.baixar_workbook(folder_id)
    simulacoes = rm.listar_simulacoes_disponiveis(wb)
    if not simulacoes:
        st.info("Esse estudo ainda não tem nenhum simulado gerado.")
        return

    numero_simulacao = st.selectbox("Número do simulado", simulacoes)

    chave_questoes = f"questoes_{folder_id}_{numero_simulacao}"
    if chave_questoes not in st.session_state:
        st.session_state[chave_questoes] = rm.carregar_questoes_da_simulacao(wb, numero_simulacao)
    questoes = st.session_state[chave_questoes]

    st.divider()

    respostas_usuario = {}
    letras = ["A", "B", "C", "D", "E"]

    with st.form("form_respostas"):
        for i, q in enumerate(questoes):
            st.markdown(f"**{i + 1}. {q['pergunta']}**")
            opcoes_exibidas = [f"{letras[j]}) {op}" for j, op in enumerate(q["opcoes"])]
            escolha = st.radio(
                f"resposta_{i}", opcoes_exibidas, key=f"resp_{i}",
                label_visibility="collapsed", index=None,
            )
            respostas_usuario[i] = opcoes_exibidas.index(escolha) if escolha else None

        st.write("")
        enviar = st.form_submit_button("Enviar respostas", type="primary")

    if enviar:
        nao_respondidas = [i + 1 for i, v in respostas_usuario.items() if v is None]
        if nao_respondidas:
            st.error(f"Faltou responder a(s) questão(ões): {', '.join(map(str, nao_respondidas))}")
            return

        respostas_completas = [
            {**q, "resposta_idx": respostas_usuario[i]} for i, q in enumerate(questoes)
        ]
        rm.gravar_respostas(wb, numero_simulacao, participante["usuario"], respostas_completas)
        ds.salvar_workbook(folder_id, wb)

        acertos = sum(1 for r in respostas_completas if r["resposta_idx"] == r["correta"])
        total = len(respostas_completas)
        st.success(f"✅ Respostas enviadas! Você acertou {acertos}/{total} ({acertos/total:.0%}).")

        with st.expander("Ver gabarito comentado", expanded=True):
            for i, r in enumerate(respostas_completas):
                certo = r["resposta_idx"] == r["correta"]
                icone = "✅" if certo else "❌"
                st.markdown(
                    f"{icone} **{i+1}.** Sua resposta: {letras[r['resposta_idx']]}) "
                    f"{r['opcoes'][r['resposta_idx']]} — "
                    f"Correta: {letras[r['correta']]}) {r['opcoes'][r['correta']]}"
                )
                comentario = (r.get("comentario") or "").strip()
                if comentario:
                    st.caption(comentario)
                st.write("")


# =========================================================================
# PÁGINA 2 — GESTÃO DE ESTUDOS (senha)
# =========================================================================
def pagina_gestao():
    st.title("⚙️ Gestão de estudos")

    if not admin_auth.esta_autenticado():
        admin_auth.tela_login()
        return

    if st.sidebar.button("Sair da gestão"):
        admin_auth.logout()
        st.rerun()

    # --- feedback que precisa sobreviver ao st.rerun() da ação anterior ---
    if "msg_sucesso" in st.session_state:
        st.success(st.session_state.pop("msg_sucesso"))

    estudos = emd.listar_estudos()
    nomes_estudos = [n for n, _ in estudos]

    with st.expander("👥 Gerenciar participantes"):
        st.caption(
            "Cada participante recebe login próprio (usuário + senha, não é "
            "a senha de admin) e só enxerga, na tela \"Responder simulado\", "
            "os estudos que você liberar aqui pra ele. Usuário não diferencia "
            "maiúsculas de minúsculas (Ana = ana = ANA)."
        )

        with st.form("form_participante"):
            usuario_p = st.text_input("Usuário")
            email_p = st.text_input("E-mail")
            senha_p = st.text_input(
                "Senha",
                type="password",
                help="Deixe em branco para manter a senha atual, se estiver editando alguém que já existe.",
            )
            acesso_total = st.checkbox("Acesso a TODOS os estudos (atuais e futuros)")
            estudos_selecionados = st.multiselect(
                "Estudos permitidos", nomes_estudos, disabled=acesso_total,
            )
            salvar_p = st.form_submit_button("Salvar participante")

        if salvar_p:
            if not usuario_p:
                st.error("Preencha o usuário.")
            else:
                with st.status(f"Salvando participante '{usuario_p}'...", expanded=True) as status_p:
                    try:
                        permitidos = [pm.TODOS] if acesso_total else estudos_selecionados
                        st.write("Gravando participantes.json no Drive...")
                        pm.adicionar_ou_atualizar(usuario_p, email_p, senha_p or None, permitidos)
                        status_p.update(label=f"✅ Participante '{usuario_p}' salvo!", state="complete")
                        st.session_state["msg_sucesso"] = f"Participante '{usuario_p}' salvo."
                        st.rerun()
                    except ValueError as e:
                        status_p.update(label="❌ Não foi possível salvar.", state="error")
                        st.error(str(e))

        with st.spinner("Carregando participantes..."):
            participantes = pm.carregar_participantes()

        if participantes:
            st.write("**Participantes cadastrados:**")
            for i, p in enumerate(participantes):
                # .get() com fallback pra não quebrar em registros do FORMATO
                # ANTIGO (nome/codigo), que podem ter sobrado no Drive de
                # antes dessa mudança de esquema.
                identificador = p.get("usuario") or p.get("nome") or p.get("codigo") or "(registro sem nome)"
                eh_legado = not p.get("usuario")

                permitidos = p.get("estudos_permitidos", [])
                acesso_txt = "Todos os estudos" if pm.TODOS in permitidos else (", ".join(permitidos) or "Nenhum")
                email_txt = f" · {p['email']}" if p.get("email") else ""
                aviso_legado = " — ⚠️ *cadastro antigo, incompatível com o login atual; remova e recadastre*" if eh_legado else ""

                col_info, col_botao = st.columns([4, 1])
                with col_info:
                    st.write(f"- **{identificador}**{email_txt} → {acesso_txt}{aviso_legado}")
                with col_botao:
                    # a chave usa o índice "i" pra nunca colidir, mesmo que dois
                    # registros tenham o mesmo identificador (ex: um legado e um
                    # novo com o mesmo nome, antes de você apagar o antigo)
                    if st.button("Remover", key=f"remover_participante_{i}_{identificador}"):
                        with st.status(f"Removendo '{identificador}'...", expanded=True) as status_r:
                            pm.remover_por_indice(i) if eh_legado else pm.remover(identificador)
                            status_r.update(label=f"✅ '{identificador}' removido!", state="complete")
                        st.session_state["msg_sucesso"] = f"Participante '{identificador}' removido."
                        st.rerun()
        else:
            st.caption("Nenhum participante cadastrado ainda.")

    opcoes = ["➕ Novo estudo"] + nomes_estudos

    # se acabamos de criar um estudo, já abre a página nele em vez de
    # voltar para "➕ Novo estudo" com o formulário vazio
    indice_padrao = 0
    if "estudo_recem_criado" in st.session_state:
        nome_criado = st.session_state.pop("estudo_recem_criado")
        if nome_criado in opcoes:
            indice_padrao = opcoes.index(nome_criado)

    escolha = st.selectbox("Estudo", opcoes, index=indice_padrao)

    if escolha == "➕ Novo estudo":
        with st.form("form_novo_estudo"):
            nome_estudo = st.text_input("Nome do estudo/concurso")
            banca = st.text_input("Banca examinadora")
            nivel = st.text_input("Nível (ex: Superior)")
            arquivo_excel = st.file_uploader("Excel de conteúdo programático (.xlsx)", type=["xlsx"])
            enviar = st.form_submit_button("Criar estudo")

        if enviar:
            if not (nome_estudo and banca and nivel and arquivo_excel):
                st.error("Preencha todos os campos e envie o Excel de conteúdo programático.")
                return
            try:
                eixos = cl.carregar_conteudo_programatico(arquivo_excel)
            except Exception as e:
                st.error(f"Erro ao ler o Excel: {e}")
                return

            config, folder_id = emd.criar_estudo(nome_estudo, banca, nivel, eixos)
            st.session_state["estudo_folder_id"] = folder_id
            st.session_state["estudo_config"] = config
            # guardados no session_state para sobreviver ao rerun abaixo
            st.session_state["msg_sucesso"] = f"Estudo '{nome_estudo}' criado com sucesso! ✅"
            st.session_state["estudo_recem_criado"] = nome_estudo
            st.rerun()
        return

    folder_id = dict(estudos)[escolha]
    if st.session_state.get("estudo_folder_id") != folder_id:
        st.session_state["estudo_folder_id"] = folder_id
        st.session_state["estudo_config"] = emd.carregar_config(folder_id)
    config = st.session_state["estudo_config"]

    st.subheader(config["nome_estudo"])
    st.caption(
        f"Banca: {config['banca']} · Nível: {config['nivel']} · "
        f"Simulações já geradas: {config['simulacao_atual']}"
    )

    with st.expander("🔄 Ressincronizar eixos/temas a partir de um novo Excel"):
        novo_arquivo = st.file_uploader("Excel atualizado", type=["xlsx"], key="resync_uploader")
        if novo_arquivo and st.button("Ressincronizar agora"):
            try:
                novos_eixos = cl.carregar_conteudo_programatico(novo_arquivo)
            except Exception as e:
                st.error(f"Erro ao ler o Excel: {e}")
                return
            config = emd.ressincronizar_eixos(folder_id, config, novos_eixos)
            st.session_state["estudo_config"] = config
            st.session_state["msg_sucesso"] = "Eixos/temas atualizados com sucesso."
            st.rerun()

    st.divider()

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
                    st.write(f"  ↳ {qtd_tema} questão(ões) — {tema[:70]}")
                    questoes = ia.gerar_bloco(
                        banca=config["banca"], nivel=config["nivel"], tema=tema,
                        qtd=qtd_tema, evitar=evitar, tamanho_bloco=dados.get("tamanho_bloco", 10),
                    )
                    if questoes:
                        blocos_eixo.append((tema, questoes))
                    else:
                        st.write(f"  ⚠️ Falhou ao gerar: {tema}")

                todas_questoes_eixo = [q for _, qs in blocos_eixo for q in qs]
                if todas_questoes_eixo:
                    ia.balancear_gabaritos(todas_questoes_eixo)

                if not blocos_eixo:
                    st.write(f"  ⚠️ Nenhuma questão gerada para {eixo} — guia ficará vazia.")
                    em.resetar_guia_eixo(wb, eixo)
                    continue

                em.resetar_guia_eixo(wb, eixo)
                for tema, questoes in blocos_eixo:
                    em.gravar_questoes_no_eixo(wb, eixo, tema, questoes)

                eixos_questoes[eixo] = blocos_eixo
                st.write(f"  ✓ {len(todas_questoes_eixo)} questões gravadas ({len(blocos_eixo)} temas).")

            em.consolidar(wb, numero_simulacao, eixos_questoes)
            ds.salvar_workbook(folder_id, wb)

            config["simulacao_atual"] = numero_simulacao
            emd.salvar_config(folder_id, config)
            st.session_state["estudo_config"] = config

            status.update(label=f"✅ Simulado nº {numero_simulacao} concluído!", state="complete")

        st.success("Arquivo `simulado.xlsx` atualizado na pasta do estudo no seu Google Drive.")


# =========================================================================
# PÁGINA 3 — DESEMPENHO (senha)
# =========================================================================
def pagina_desempenho():
    st.title("📊 Desempenho")

    if not admin_auth.esta_autenticado():
        admin_auth.tela_login()
        return

    estudos = emd.listar_estudos()
    if not estudos:
        st.info("Nenhum estudo disponível ainda.")
        return

    nome_estudo = st.selectbox("Estudo", [n for n, _ in estudos])
    folder_id = dict(estudos)[nome_estudo]

    wb = ds.baixar_workbook(folder_id)
    resultados = rm.carregar_todos_resultados(wb)
    if not resultados:
        st.info("Ainda não há respostas registradas para este estudo.")
        return

    import pandas as pd
    df = pd.DataFrame(resultados)

    participantes = sorted(df["participante"].unique())
    filtro_participante = st.multiselect("Filtrar por participante", participantes, default=participantes)
    df_filtrado = df[df["participante"].isin(filtro_participante)]

    st.subheader("Aproveitamento geral por participante")
    resumo_participante = (
        df_filtrado.groupby("participante")["acertou"]
        .agg(acertos="sum", total="count")
        .assign(percentual=lambda d: (d["acertos"] / d["total"] * 100).round(1))
        .sort_values("percentual", ascending=False)
    )
    st.dataframe(resumo_participante, use_container_width=True)

    st.subheader("Aproveitamento por eixo")
    resumo_eixo = (
        df_filtrado.groupby(["participante", "eixo"])["acertou"]
        .agg(acertos="sum", total="count")
        .assign(percentual=lambda d: (d["acertos"] / d["total"] * 100).round(1))
        .reset_index()
    )
    st.dataframe(resumo_eixo, use_container_width=True)

    st.subheader("Evolução por simulado (todos os participantes)")
    evolucao = (
        df_filtrado.groupby("simulacao")["acertou"]
        .agg(acertos="sum", total="count")
        .assign(percentual=lambda d: (d["acertos"] / d["total"] * 100).round(1))
        .sort_index()
    )
    st.line_chart(evolucao["percentual"])


# =========================================================================
if pagina == "Responder simulado":
    pagina_responder()
elif pagina == "Gestão de estudos":
    pagina_gestao()
else:
    pagina_desempenho()
