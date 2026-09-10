"""
Simulador EARA — versão web (Streamlit + Google Drive via token de longa duração)
============================================================================

Três áreas:

1. "Responder simulado" — protegida por USUÁRIO + SENHA individuais (não é
   senha de admin: cada participante tem login próprio, cadastrado em
   "Gestão de estudos", que define QUAIS estudos aquela pessoa pode ver).
   Avisa se a pessoa já respondeu aquele simulado antes, e mostra progresso
   ao vivo enquanto responde.

2. "Gestão de estudos" — protegida por senha (admin_password nos Secrets).
   Criar/excluir estudos, ressincronizar eixos, gerar novos simulados via IA
   (com confirmação, já que cada geração consome créditos reais da API),
   cadastrar/editar participantes e definir o que cada um pode acessar.

3. "Desempenho" — protegida por senha. Dashboard com o histórico de
   respostas de todo mundo, cruzando por participante/eixo/tema/simulado,
   com filtro de período e exportação em CSV.

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


def _aplicar_estilo_visual():
    """
    Injeta CSS customizado pra deixar o app mais vivo: fonte diferente,
    fade-in suave no conteúdo, botões com hover animado, cards com sombra
    pras questões, e uma barra de progresso com gradiente.

    IMPORTANTE: isso usa classes internas do Streamlit (não documentadas
    oficialmente), então pode parar de funcionar se uma atualização futura
    do Streamlit mudar essa estrutura interna. Não quebra o app — na pior
    hipótese, o CSS simplesmente deixa de ter efeito e volta ao visual padrão.
    """
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Poppins', sans-serif;
    }

    /* fade-in suave em cada bloco de conteúdo renderizado */
    div[data-testid="stVerticalBlock"] > div {
        animation: apareceSuave 0.45s ease-out;
    }
    @keyframes apareceSuave {
        from { opacity: 0; transform: translateY(8px); }
        to   { opacity: 1; transform: translateY(0); }
    }

    /* botões com leve efeito de escala e sombra ao passar o mouse */
    .stButton > button, .stFormSubmitButton > button {
        border-radius: 10px;
        transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .stButton > button:hover, .stFormSubmitButton > button:hover {
        transform: translateY(-2px) scale(1.02);
        box-shadow: 0 6px 16px rgba(108, 92, 231, 0.25);
    }

    /* cards com sombra suave para separar visualmente as questões */
    div[data-testid="stExpander"] {
        border-radius: 14px;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
        transition: box-shadow 0.2s ease;
    }
    div[data-testid="stExpander"]:hover {
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.10);
    }

    /* barra de progresso com gradiente em vez de cor sólida */
    div[data-testid="stProgress"] > div > div > div {
        background-image: linear-gradient(90deg, #6C5CE7, #A29BFE);
        border-radius: 8px;
    }

    /* transição suave ao trocar de página na barra lateral */
    section[data-testid="stSidebar"] {
        transition: background-color 0.3s ease;
    }
    </style>
    """, unsafe_allow_html=True)


_aplicar_estilo_visual()

LETRAS = ["A", "B", "C", "D", "E"]

PAGINAS = ["Responder simulado", "Gestão de estudos", "Desempenho"]
pagina = st.sidebar.radio("Navegação", PAGINAS)


def _erro_drive_amigavel(e: Exception):
    """
    Mostra um erro amigável em vez de deixar o Streamlit crashar cru quando
    alguma chamada ao Drive falha (ex: token expirado/revogado). Não sabemos
    reparar isso automaticamente por código, mas pelo menos avisamos com uma
    mensagem que aponta a causa mais provável em vez de um traceback bruto.
    """
    st.error(
        "Não consegui falar com o Google Drive agora. Isso costuma acontecer "
        "quando o token de acesso expirou ou foi revogado. Se você é o "
        "administrador, confira os Secrets (client_id/client_secret/"
        "refresh_token) e, se precisar, gere um novo com obter_refresh_token.py."
    )
    with st.expander("Detalhes técnicos do erro"):
        st.code(str(e))


# =========================================================================
# PÁGINA 1 — RESPONDER SIMULADO (usuário + senha do participante)
# =========================================================================
def pagina_responder():
    st.title("📝 Responder simulado")

    # --- Login por usuário + senha ---
    if "participante" not in st.session_state:
        st.write("Entre com seu usuário e senha pra ver os estudos liberados pra você.")
        usuario_login = st.text_input("Usuário")
        senha_login = st.text_input("Senha", type="password")
        if st.button("Entrar", type="primary"):
            try:
                participante = pm.autenticar(usuario_login, senha_login)
            except Exception as e:
                _erro_drive_amigavel(e)
                return
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
    try:
        todos_estudos = emd.listar_estudos()
    except Exception as e:
        _erro_drive_amigavel(e)
        return

    estudos_permitidos = [
        (nome, folder_id) for nome, folder_id in todos_estudos
        if pm.pode_acessar(participante, nome)
    ]

    if not estudos_permitidos:
        st.info("Você ainda não tem acesso a nenhum estudo. Fale com quem te cadastrou.")
        return

    nome_estudo = st.selectbox("Estudo", [n for n, _ in estudos_permitidos])
    folder_id = dict(estudos_permitidos)[nome_estudo]

    with st.spinner("Carregando simulados disponíveis..."):
        wb = ds.baixar_workbook(folder_id)
        simulacoes = rm.listar_simulacoes_disponiveis(wb)

    if not simulacoes:
        st.info("Esse estudo ainda não tem nenhum simulado gerado.")
        return

    numero_simulacao = st.selectbox("Número do simulado", simulacoes)

    # --- Avisa se esse participante já respondeu esse simulado antes ---
    chave_ja_respondeu = f"ja_verificou_{folder_id}_{numero_simulacao}"
    if chave_ja_respondeu not in st.session_state:
        with st.spinner("Verificando se você já respondeu esse simulado..."):
            st.session_state[chave_ja_respondeu] = rm.buscar_resposta_existente(
                folder_id, numero_simulacao, participante["usuario"]
            )
    resposta_anterior = st.session_state[chave_ja_respondeu]

    chave_confirmou_reenvio = f"confirmou_reenvio_{folder_id}_{numero_simulacao}"

    if resposta_anterior and not st.session_state.get(chave_confirmou_reenvio):
        pct = resposta_anterior["acertos"] / resposta_anterior["total"] if resposta_anterior["total"] else 0
        st.warning(
            f"Você já respondeu esse simulado em {resposta_anterior['timestamp']}, "
            f"acertando {resposta_anterior['acertos']}/{resposta_anterior['total']} ({pct:.0%})."
        )
        col_ver, col_redo = st.columns(2)
        with col_ver:
            with st.expander("Ver meu resultado anterior"):
                for r in resposta_anterior["respostas"]:
                    icone = "✅" if r["acertou"] else "❌"
                    st.write(f"{icone} {r['pergunta']}")
                    st.caption(f"Sua resposta: {r['resposta_dada']} — Correta: {r['resposta_correta']}")
        with col_redo:
            if st.button("Responder de novo (substitui o resultado anterior)"):
                st.session_state[chave_confirmou_reenvio] = True
                st.rerun()
        return

    chave_questoes = f"questoes_{folder_id}_{numero_simulacao}"
    if chave_questoes not in st.session_state:
        st.session_state[chave_questoes] = rm.carregar_questoes_da_simulacao(wb, numero_simulacao)
    questoes = st.session_state[chave_questoes]

    st.divider()

    # --- Progresso ao vivo: cada pergunta é um widget reativo (fora de
    # st.form), então o app re-renderiza a cada resposta marcada e consegue
    # mostrar quantas já foram respondidas em tempo real. ---
    prefixo_resp = f"resp_{folder_id}_{numero_simulacao}"
    respondidas = sum(
        1 for i in range(len(questoes))
        if st.session_state.get(f"{prefixo_resp}_{i}") is not None
    )
    st.progress(
        respondidas / len(questoes) if questoes else 0,
        text=f"{respondidas} de {len(questoes)} questões respondidas",
    )

    for i, q in enumerate(questoes):
        st.markdown(f"**{i + 1}. {q['pergunta']}**")
        opcoes_exibidas = [f"{LETRAS[j]}) {op}" for j, op in enumerate(q["opcoes"])]
        st.radio(
            f"resposta_{i}", opcoes_exibidas, key=f"{prefixo_resp}_{i}",
            label_visibility="collapsed", index=None,
        )

    enviar = st.button("Enviar respostas", type="primary")

    if enviar:
        respostas_usuario = {}
        for i, q in enumerate(questoes):
            opcoes_exibidas_i = [f"{LETRAS[j]}) {op}" for j, op in enumerate(q["opcoes"])]
            valor_selecionado = st.session_state.get(f"{prefixo_resp}_{i}")
            respostas_usuario[i] = opcoes_exibidas_i.index(valor_selecionado) if valor_selecionado else None

        nao_respondidas = [i + 1 for i, v in respostas_usuario.items() if v is None]
        if nao_respondidas:
            st.error(f"Faltou responder a(s) questão(ões): {', '.join(map(str, nao_respondidas))}")
            return

        respostas_completas = [
            {**q, "resposta_idx": respostas_usuario[i]} for i, q in enumerate(questoes)
        ]

        with st.spinner("Enviando respostas..."):
            resultado = rm.gravar_resposta_participante(
                folder_id, numero_simulacao, participante["usuario"], respostas_completas
            )

        acertos, total = resultado["acertos"], resultado["total"]
        percentual = acertos / total if total else 0
        st.success(f"✅ Respostas enviadas! Você acertou {acertos}/{total} ({percentual:.0%}).")

        # celebração visual, variando com o desempenho
        if percentual >= 0.8:
            st.balloons()
        elif percentual >= 0.5:
            st.snow()

        with st.expander("Ver gabarito comentado", expanded=True):
            for i, r in enumerate(respostas_completas):
                certo = r["resposta_idx"] == r["correta"]
                icone = "✅" if certo else "❌"
                st.markdown(
                    f"{icone} **{i+1}.** Sua resposta: {LETRAS[r['resposta_idx']]}) "
                    f"{r['opcoes'][r['resposta_idx']]} — "
                    f"Correta: {LETRAS[r['correta']]}) {r['opcoes'][r['correta']]}"
                )
                comentario = (r.get("comentario") or "").strip()
                if comentario:
                    st.caption(comentario)
                st.write("")

        # limpa o cache local de "já respondeu" pra próxima vez que abrir essa tela
        st.session_state.pop(chave_ja_respondeu, None)
        st.session_state.pop(chave_confirmou_reenvio, None)


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

    try:
        estudos = emd.listar_estudos()
    except Exception as e:
        _erro_drive_amigavel(e)
        return
    nomes_estudos = [n for n, _ in estudos]

    with st.spinner("Carregando participantes..."):
        try:
            participantes = pm.carregar_participantes()
        except Exception as e:
            _erro_drive_amigavel(e)
            return

    # --- Resumo rápido no topo ---
    with st.spinner("Calculando resumo..."):
        total_simulados = emd.contar_simulados_totais(estudos)
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Estudos", len(estudos))
    col_b.metric("Participantes", len(participantes))
    col_c.metric("Simulados gerados (total)", total_simulados)

    with st.expander("👥 Gerenciar participantes"):
        st.caption(
            "Cada participante recebe login próprio (usuário + senha, não é "
            "a senha de admin) e só enxerga, na tela \"Responder simulado\", "
            "os estudos que você liberar aqui pra ele. Usuário não diferencia "
            "maiúsculas de minúsculas (Ana = ana = ANA)."
        )

        # --- Seletor de edição: escolher um participante existente pré-carrega
        # o formulário abaixo com os dados dele (exceto senha, por segurança) ---
        def _identificador(p):
            return p.get("usuario") or p.get("nome") or p.get("codigo") or "(sem nome)"

        opcoes_edicao = ["➕ Novo participante"] + [_identificador(p) for p in participantes]
        escolha_edicao = st.selectbox("Cadastrar novo ou editar existente", opcoes_edicao)

        if escolha_edicao == "➕ Novo participante":
            dados_padrao = {"usuario": "", "email": "", "estudos_permitidos": []}
        else:
            dados_padrao = next(p for p in participantes if _identificador(p) == escolha_edicao)

        permitidos_atuais = dados_padrao.get("estudos_permitidos", [])

        with st.form("form_participante"):
            usuario_p = st.text_input("Usuário", value=dados_padrao.get("usuario", ""))
            email_p = st.text_input("E-mail", value=dados_padrao.get("email", ""))
            senha_p = st.text_input(
                "Senha",
                type="password",
                help="Deixe em branco para manter a senha atual, se estiver editando alguém que já existe.",
            )
            acesso_total = st.checkbox(
                "Acesso a TODOS os estudos (atuais e futuros)",
                value=(pm.TODOS in permitidos_atuais),
            )
            estudos_selecionados = st.multiselect(
                "Estudos permitidos", nomes_estudos,
                default=[e for e in permitidos_atuais if e != pm.TODOS],
                disabled=acesso_total,
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
                    except Exception as e:
                        status_p.update(label="❌ Erro inesperado.", state="error")
                        _erro_drive_amigavel(e)

        if participantes:
            st.write("**Participantes cadastrados:**")
            for i, p in enumerate(participantes):
                identificador = _identificador(p)
                eh_legado = not p.get("usuario")

                permitidos = p.get("estudos_permitidos", [])
                acesso_txt = "Todos os estudos" if pm.TODOS in permitidos else (", ".join(permitidos) or "Nenhum")
                email_txt = f" · {p['email']}" if p.get("email") else ""
                aviso_legado = " — ⚠️ *cadastro antigo, incompatível; remova e recadastre*" if eh_legado else ""

                col_info, col_botao = st.columns([4, 1])
                with col_info:
                    st.write(f"- **{identificador}**{email_txt} → {acesso_txt}{aviso_legado}")
                with col_botao:
                    if st.button("Remover", key=f"remover_participante_{i}_{identificador}"):
                        with st.status(f"Removendo '{identificador}'...", expanded=True) as status_r:
                            if eh_legado:
                                pm.remover_por_indice(i)
                            else:
                                pm.remover(identificador)
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

            try:
                config, folder_id = emd.criar_estudo(nome_estudo, banca, nivel, eixos)
            except ValueError as e:
                st.error(str(e))
                return
            except Exception as e:
                _erro_drive_amigavel(e)
                return

            st.session_state["estudo_folder_id"] = folder_id
            st.session_state["estudo_config"] = config
            st.session_state["msg_sucesso"] = f"Estudo '{nome_estudo}' criado com sucesso! ✅"
            st.session_state["estudo_recem_criado"] = nome_estudo
            st.rerun()
        return

    folder_id = dict(estudos)[escolha]
    if st.session_state.get("estudo_folder_id") != folder_id:
        st.session_state["estudo_folder_id"] = folder_id
        try:
            st.session_state["estudo_config"] = emd.carregar_config(folder_id)
        except Exception as e:
            _erro_drive_amigavel(e)
            return
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

    with st.expander("🗑️ Excluir este estudo"):
        st.warning(
            "Move a pasta inteira do estudo (config, simulado.xlsx e todas as "
            "respostas) para a lixeira do Drive. Não é uma exclusão definitiva "
            "imediata — ainda dá pra restaurar pela lixeira do Google Drive."
        )
        confirmar_exclusao = st.checkbox(f"Sim, quero excluir '{config['nome_estudo']}'")
        if st.button("Excluir estudo", type="primary", disabled=not confirmar_exclusao):
            with st.status(f"Movendo '{config['nome_estudo']}' para a lixeira...", expanded=True) as status_del:
                try:
                    emd.excluir_estudo(folder_id)
                    status_del.update(label="✅ Estudo excluído!", state="complete")
                except Exception as e:
                    status_del.update(label="❌ Erro ao excluir.", state="error")
                    _erro_drive_amigavel(e)
                    return
            st.session_state.pop("estudo_folder_id", None)
            st.session_state.pop("estudo_config", None)
            st.session_state["msg_sucesso"] = f"Estudo '{config['nome_estudo']}' excluído."
            st.rerun()

    st.divider()

    # --- Confirmação em duas etapas antes de gerar (gasta créditos reais de API) ---
    chave_confirmar_geracao = f"confirmar_geracao_{folder_id}"

    if not st.session_state.get(chave_confirmar_geracao):
        if st.button("🎲 Gerar novo simulado", type="primary"):
            st.session_state[chave_confirmar_geracao] = True
            st.rerun()
    else:
        total_questoes_estudo = sum(d["qtd_questoes"] for d in config["eixos"].values())
        st.warning(
            f"Isso vai gerar {total_questoes_estudo} questões novas via IA, "
            f"consumindo créditos reais da sua conta OpenAI. Confirma?"
        )
        col_sim, col_nao = st.columns(2)
        confirmou = col_sim.button("Sim, gerar agora", type="primary")
        if col_nao.button("Cancelar"):
            st.session_state.pop(chave_confirmar_geracao, None)
            st.rerun()

        if confirmou:
            st.session_state.pop(chave_confirmar_geracao, None)
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

    try:
        estudos = emd.listar_estudos()
    except Exception as e:
        _erro_drive_amigavel(e)
        return
    if not estudos:
        st.info("Nenhum estudo disponível ainda.")
        return

    nome_estudo = st.selectbox("Estudo", [n for n, _ in estudos])
    folder_id = dict(estudos)[nome_estudo]

    with st.spinner("Carregando resultados..."):
        try:
            resultados = rm.carregar_todos_resultados(folder_id)
        except Exception as e:
            _erro_drive_amigavel(e)
            return

    if not resultados:
        st.info("Ainda não há respostas registradas para este estudo.")
        return

    import pandas as pd
    df = pd.DataFrame(resultados)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    # --- Filtro por participante ---
    participantes = sorted(df["participante"].unique())
    filtro_participante = st.multiselect("Filtrar por participante", participantes, default=participantes)

    # --- Filtro por período ---
    datas_validas = df["timestamp"].dropna()
    if not datas_validas.empty:
        data_min, data_max = datas_validas.min().date(), datas_validas.max().date()
        periodo = st.date_input(
            "Filtrar por período", value=(data_min, data_max),
            min_value=data_min, max_value=data_max,
        )
    else:
        periodo = None

    df_filtrado = df[df["participante"].isin(filtro_participante)]
    if periodo and isinstance(periodo, tuple) and len(periodo) == 2:
        inicio, fim = periodo
        df_filtrado = df_filtrado[
            (df_filtrado["timestamp"].dt.date >= inicio) & (df_filtrado["timestamp"].dt.date <= fim)
        ]

    if df_filtrado.empty:
        st.info("Nenhum resultado no filtro selecionado.")
        return

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

    st.subheader("Aproveitamento por tema")
    resumo_tema = (
        df_filtrado.groupby(["participante", "eixo", "tema"])["acertou"]
        .agg(acertos="sum", total="count")
        .assign(percentual=lambda d: (d["acertos"] / d["total"] * 100).round(1))
        .reset_index()
        .sort_values("percentual")
    )
    st.dataframe(resumo_tema, use_container_width=True)

    st.subheader("Evolução por simulado (todos os participantes)")
    evolucao = (
        df_filtrado.groupby("simulacao")["acertou"]
        .agg(acertos="sum", total="count")
        .assign(percentual=lambda d: (d["acertos"] / d["total"] * 100).round(1))
        .sort_index()
    )
    st.line_chart(evolucao["percentual"])

    st.divider()
    st.download_button(
        "⬇️ Exportar resultados filtrados (CSV)",
        data=df_filtrado.to_csv(index=False).encode("utf-8"),
        file_name=f"desempenho_{nome_estudo}.csv",
        mime="text/csv",
    )


# =========================================================================
if pagina == "Responder simulado":
    pagina_responder()
elif pagina == "Gestão de estudos":
    pagina_gestao()
else:
    pagina_desempenho()
