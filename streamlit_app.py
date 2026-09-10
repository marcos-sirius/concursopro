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
import banco_compartilhado_manager as bcm
import categorias_manager as cat
import geracao_manager as gm
import participantes_manager as pm
import resultados_manager as rm
import sorteio

st.set_page_config(page_title="Simulador EARA", page_icon="📚", layout="centered")


def _aplicar_estilo_visual():
    """
    Injeta CSS puramente COSMÉTICO (fonte, animações, hover, sombra, barra de
    progresso fixa) — sem tocar em cor de fundo/texto.

    Por quê? Claro/escuro agora é resolvido pelo tema NATIVO do Streamlit
    (.streamlit/config.toml com [theme.light] e [theme.dark]), escolhido pela
    pessoa no menu "⋮" > Settings > Theme. O motor de tema de verdade do
    Streamlit troca a cor de TODOS os componentes internos corretamente
    (rótulo de rádio, legenda, cabeçalho etc.) — coisa que um CSS manual por
    cima não conseguia cobrir por completo (foi isso que causou o texto
    ilegível no modo escuro na primeira tentativa). Esse CSS aqui só cuida do
    que é puramente visual e não depende de qual tema está ativo.

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
        box-shadow: 0 6px 16px rgba(108, 92, 231, 0.35);
    }

    /* cards com sombra suave para separar visualmente as questões — sombra
       neutra e discreta, funciona tanto no tema claro quanto no escuro */
    div[data-testid="stExpander"] {
        border-radius: 14px;
        box-shadow: 0 2px 10px rgba(128, 128, 128, 0.18);
        transition: box-shadow 0.2s ease;
    }
    div[data-testid="stExpander"]:hover {
        box-shadow: 0 4px 16px rgba(128, 128, 128, 0.28);
    }

    /* barra de progresso com gradiente em vez de cor sólida (usada em
       outros lugares do app, ex: geração de simulado) */
    div[data-testid="stProgress"] > div > div > div {
        background-image: linear-gradient(90deg, #6C5CE7, #A29BFE);
        border-radius: 8px;
    }

    /* fixa a barra de botões (Salvar progresso / Enviar respostas) no
       rodapé, sempre visível independente da rolagem. Trocamos de
       "position: sticky" pra "position: fixed" porque sticky com
       "bottom" costuma falhar dentro de containers flexíveis (é como o
       Streamlit organiza os blocos por baixo dos panos) — fixed gruda na
       tela de verdade, sem depender dessa estrutura interna.
       O wrapper interno limita a largura pra imitar o layout "centered"
       do Streamlit (senão a barra ocuparia a tela toda, largura cheia). */
    .st-key-botoes_envio_fixos {
        position: fixed;
        left: 0;
        right: 0;
        bottom: 0;
        z-index: 999;
        background-color: inherit;
        box-shadow: 0 -4px 10px rgba(128, 128, 128, 0.18);
    }
    .st-key-botoes_envio_fixos > div {
        max-width: 730px;
        margin: 0 auto;
        padding: 0.6rem 1rem;
    }

    /* dá espaço no final da página pra barra fixa não tampar a última
       questão/pergunta */
    div[data-testid="stMainBlockContainer"] {
        padding-bottom: 5rem;
    }
    </style>
    """, unsafe_allow_html=True)


LETRAS = ["A", "B", "C", "D", "E"]

_aplicar_estilo_visual()

st.sidebar.caption("💡 Para trocar entre claro/escuro: menu \"⋮\" (canto superior direito) → Settings → Theme.")
st.sidebar.divider()
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


def _passo_geracao(folder_id: str, config: dict, wb, ck: dict) -> str:
    """
    Processa UMA unidade de trabalho e devolve "continuar", "concluido",
    "cancelado" ou "erro". Salva o checkpoint no Drive a cada passo: se a
    aba for fechada, der erro, ou você clicar em Cancelar, nada além do
    passo atual se perde.

    Antes de gerar um tema novo, verifica se dá pra REAPROVEITAR questões
    de um tema parecido de outro estudo (mesmo nível, dentro da janela de
    meses configurada) — só chama a API pro que sobrar depois do
    reaproveitamento (se houver).
    """
    chave_cancelar = f"geracao_cancelar_{folder_id}"
    meses_janela = ck.get("meses_janela_reuso", 6)

    for eixo, dados_eixo in config["eixos"].items():
        eixo_ck = ck["eixos"].get(eixo)

        if eixo_ck is None:
            # decide a distribuição desse eixo uma única vez (mexe no
            # historico_temas do config, por isso salva junto)
            distribuicao = sorteio.distribuir_temas(config, eixo, dados_eixo["qtd_questoes"])
            ck["eixos"][eixo] = {"distribuicao": distribuicao, "prontos": {}, "origem": {}}
            emd.salvar_config(folder_id, config)
            gm.salvar_checkpoint(folder_id, ck)
            return "continuar"

        pendentes = [t for t in eixo_ck["distribuicao"] if t not in eixo_ck["prontos"]]
        if not pendentes:
            continue  # este eixo já está pronto, passa pro próximo

        if st.session_state.get(chave_cancelar):
            ck["status"] = "cancelado"
            gm.salvar_checkpoint(folder_id, ck)
            st.session_state.pop(chave_cancelar, None)
            return "cancelado"

        tema = pendentes[0]
        qtd_tema = eixo_ck["distribuicao"][tema]

        # --- Passo A: decide (uma vez só) se existe tema parecido em outro
        # estudo pra reaproveitar. Fica salvo no checkpoint pra não perguntar
        # de novo se a geração for retomada depois. ---
        decisoes = eixo_ck.setdefault("decisoes_reuso", {})
        if tema not in decisoes:
            candidatos = bcm.buscar_candidatos(
                nivel=config["nivel"], eixo=eixo, meses_janela=meses_janela,
                excluir_estudo_folder_id=folder_id,
            )
            indice_match = None
            if candidatos:
                try:
                    indice_match = ia.comparar_temas_similares(tema, [c["tema"] for c in candidatos])
                except Exception:
                    indice_match = None  # falha na comparação não deve travar a geração inteira
            decisoes[tema] = candidatos[indice_match] if indice_match is not None else None
            gm.salvar_checkpoint(folder_id, ck)
            return "continuar"

        candidato = decisoes[tema]
        questoes_reaproveitadas = []
        if candidato:
            wb_doador = wb if candidato["estudo_folder_id"] == folder_id else ds.baixar_workbook(candidato["estudo_folder_id"])
            disponiveis = em.questoes_por_tema(wb_doador, candidato["eixo"], candidato["tema"])
            questoes_reaproveitadas = disponiveis[:qtd_tema]

        # --- Passo B: se o reaproveitamento já cobre tudo, nem chama a API ---
        if len(questoes_reaproveitadas) >= qtd_tema:
            eixo_ck["prontos"][tema] = questoes_reaproveitadas[:qtd_tema]
            eixo_ck["origem"][tema] = {
                "tipo": "reaproveitado", "estudo": candidato["estudo_nome"], "tema_origem": candidato["tema"],
            }
            bcm.incrementar_uso(candidato["id"])
            gm.salvar_checkpoint(folder_id, ck)
            return "continuar"

        # --- Passo C: gera só o que falta (0 se não achou nada reaproveitável) ---
        qtd_faltante = qtd_tema - len(questoes_reaproveitadas)
        evitar = em.perguntas_ja_usadas(wb, eixo, tema=tema, limite=30)

        try:
            questoes_novas = ia.gerar_bloco(
                banca=config["banca"], nivel=config["nivel"], tema=tema,
                qtd=qtd_faltante, evitar=evitar, tamanho_bloco=dados_eixo.get("tamanho_bloco", 10),
            )
        except Exception as e:
            ck["status"] = "erro"
            ck["erro_mensagem"] = str(e)
            gm.salvar_checkpoint(folder_id, ck)
            return "erro"

        if not questoes_novas or len(questoes_novas) < qtd_faltante:
            obtido = len(questoes_novas) if questoes_novas else 0
            ck["status"] = "erro"
            ck["erro_mensagem"] = (
                f"Falha ao gerar o tema '{tema}' (obteve {obtido}/{qtd_faltante} questões "
                f"mesmo após as retentativas internas — provável instabilidade da API)."
            )
            gm.salvar_checkpoint(folder_id, ck)
            return "erro"

        if questoes_reaproveitadas:
            bcm.incrementar_uso(candidato["id"])
            eixo_ck["origem"][tema] = {
                "tipo": "parcial", "estudo": candidato["estudo_nome"], "tema_origem": candidato["tema"],
                "qtd_reaproveitada": len(questoes_reaproveitadas),
            }
        else:
            eixo_ck["origem"][tema] = {"tipo": "gerado"}
            # este lote é 100% novo -> vira candidato pra outros estudos reaproveitarem no futuro
            bcm.registrar_novo_lote(
                folder_id, config.get("nome_estudo", ""), config["nivel"], eixo, tema, len(questoes_novas),
            )

        eixo_ck["prontos"][tema] = questoes_reaproveitadas + questoes_novas
        gm.salvar_checkpoint(folder_id, ck)
        return "continuar"

    # nenhum eixo com pendente -> monta tudo e finaliza de vez
    eixos_questoes = {}
    resumo_reaproveitamento = []
    for eixo, eixo_ck in ck["eixos"].items():
        blocos_eixo = [(tema, qs) for tema, qs in eixo_ck["prontos"].items() if qs]

        # só reembaralha o gabarito das questões GERADAS agora — as
        # reaproveitadas já têm o comentário com letra fixa da vez em que
        # foram criadas; reembaralhar de novo bagunçaria essa referência,
        # já que não sobrou nenhum marcador [[LETRA_OPCAO_N]] pra corrigir.
        origem_por_tema = eixo_ck.get("origem", {})
        questoes_para_embaralhar = [
            q for tema, qs in blocos_eixo
            for q in qs
            if origem_por_tema.get(tema, {}).get("tipo") != "reaproveitado"
        ]
        if questoes_para_embaralhar:
            ia.balancear_gabaritos(questoes_para_embaralhar)

        em.resetar_guia_eixo(wb, eixo)
        for tema, questoes in blocos_eixo:
            em.gravar_questoes_no_eixo(wb, eixo, tema, questoes)
            info_origem = origem_por_tema.get(tema, {})
            if info_origem.get("tipo") in ("reaproveitado", "parcial"):
                resumo_reaproveitamento.append(
                    f"{eixo} / {tema} ← \"{info_origem['tema_origem']}\" ({info_origem['estudo']})"
                )
        if blocos_eixo:
            eixos_questoes[eixo] = blocos_eixo

    em.consolidar(wb, ck["numero_simulacao"], eixos_questoes)
    ds.salvar_workbook(folder_id, wb)

    config["simulacao_atual"] = ck["numero_simulacao"]
    emd.salvar_config(folder_id, config)
    gm.apagar_checkpoint(folder_id)

    ck["resumo_reaproveitamento"] = resumo_reaproveitamento
    return "concluido"


@st.cache_data(ttl=30, show_spinner=False)
def _resumo_estudos_cacheado():
    """
    Lê a lista de estudos + o config.json de cada um (pra saber a categoria)
    e guarda em cache por 30s. Sem isso, a tela de Responder simulado re-
    executaria essa leitura a CADA clique de resposta (o progresso ao vivo
    reroda a função inteira a cada widget marcado) — desperdiçando chamadas
    ao Drive sem necessidade. 30s é curto o bastante pra um estudo/categoria
    novo aparecer quase na hora, mas evita reler em toda interação.
    """
    estudos = emd.listar_estudos()
    resumo = emd.carregar_resumo_estudos(estudos)
    return estudos, resumo


@st.cache_data(ttl=30, show_spinner=False)
def _categorias_cacheadas():
    return cat.carregar_categorias()


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

    # --- Cascata: categoria -> estudo, filtrando pelo que o participante pode acessar ---
    try:
        _, resumo_estudos = _resumo_estudos_cacheado()
    except Exception as e:
        _erro_drive_amigavel(e)
        return

    resumo_permitido = [
        r for r in resumo_estudos
        if pm.pode_acessar(participante, r["nome"], r["categoria"])
    ]

    if not resumo_permitido:
        st.info("Você ainda não tem acesso a nenhum estudo. Fale com quem te cadastrou.")
        return

    categorias_disponiveis = sorted({r["categoria"] for r in resumo_permitido})
    categoria_escolhida = st.selectbox("Categoria", categorias_disponiveis)

    estudos_da_categoria = [r for r in resumo_permitido if r["categoria"] == categoria_escolhida]
    nome_estudo = st.selectbox("Estudo", [r["nome"] for r in estudos_da_categoria])
    folder_id = next(r["folder_id"] for r in estudos_da_categoria if r["nome"] == nome_estudo)

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

    # --- Carrega rascunho salvo (se houver) pra pré-marcar as respostas ---
    chave_rascunho = f"rascunho_{folder_id}_{numero_simulacao}"
    if chave_rascunho not in st.session_state:
        with st.spinner("Verificando se há progresso salvo..."):
            st.session_state[chave_rascunho] = rm.buscar_rascunho(
                folder_id, numero_simulacao, participante["usuario"]
            )
    rascunho = st.session_state[chave_rascunho]
    indices_salvos = rascunho["respostas_indices"] if rascunho else [None] * len(questoes)

    st.divider()

    if rascunho:
        st.info(f"📝 Continuando de onde você parou (progresso salvo em {rascunho['timestamp']}).")

    # Formulário de verdade: os widgets só disparam recálculo quando um dos
    # botões é clicado, em vez de reprocessar tudo a cada resposta marcada
    # (mais econômico em chamadas/recursos, abriu mão do progresso ao vivo
    # de propósito por causa disso).
    with st.form("form_respostas"):
        for i, q in enumerate(questoes):
            st.markdown(f"**{i + 1}. {q['pergunta']}**")
            opcoes_exibidas = [f"{LETRAS[j]}) {op}" for j, op in enumerate(q["opcoes"])]
            indice_padrao = indices_salvos[i] if i < len(indices_salvos) else None
            st.radio(
                f"resposta_{i}", opcoes_exibidas, key=f"resp_{i}",
                label_visibility="collapsed", index=indice_padrao,
            )
        st.write("")
        with st.container(key="botoes_envio_fixos"):
            col_rascunho, col_enviar = st.columns(2)
            salvar_progresso = col_rascunho.form_submit_button("💾 Salvar progresso")
            enviar = col_enviar.form_submit_button("✅ Enviar respostas", type="primary")

    # --- Extrai os índices marcados agora (vale tanto pra salvar rascunho
    # quanto pra enviar de vez — os dois botões disparam o mesmo form) ---
    indices_atuais = []
    for i, q in enumerate(questoes):
        opcoes_exibidas_i = [f"{LETRAS[j]}) {op}" for j, op in enumerate(q["opcoes"])]
        valor_selecionado = st.session_state.get(f"resp_{i}")
        indices_atuais.append(opcoes_exibidas_i.index(valor_selecionado) if valor_selecionado else None)

    if salvar_progresso:
        with st.spinner("Salvando progresso..."):
            rm.salvar_rascunho(folder_id, numero_simulacao, participante["usuario"], indices_atuais)
        respondidas = sum(1 for v in indices_atuais if v is not None)
        st.success(f"Progresso salvo ({respondidas}/{len(questoes)} respondidas). Pode fechar e continuar depois.")
        st.session_state.pop(chave_rascunho, None)  # força recarregar na próxima renderização

    if enviar:
        respostas_usuario = dict(enumerate(indices_atuais))

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
            rm.apagar_rascunho(folder_id, numero_simulacao, participante["usuario"])

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

        # limpa os caches locais pra próxima vez que abrir essa tela
        st.session_state.pop(chave_ja_respondeu, None)
        st.session_state.pop(chave_confirmou_reenvio, None)
        st.session_state.pop(chave_rascunho, None)


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
        estudos, resumo_estudos = _resumo_estudos_cacheado()
    except Exception as e:
        _erro_drive_amigavel(e)
        return
    nomes_estudos = [n for n, _ in estudos]

    with st.spinner("Carregando categorias..."):
        try:
            categorias_cadastradas = _categorias_cacheadas()
        except Exception as e:
            _erro_drive_amigavel(e)
            categorias_cadastradas = []

    # União com categorias que já estão em uso por algum estudo, mesmo que
    # tenham sido removidas do cadastro (ou sejam de estudos antigos, tipo o
    # "Geral" automático) — pra nenhum seletor quebrar por falta de opção.
    categorias_existentes = sorted(set(categorias_cadastradas) | set(emd.listar_categorias(resumo_estudos)))

    with st.spinner("Carregando participantes..."):
        try:
            participantes = pm.carregar_participantes()
        except Exception as e:
            _erro_drive_amigavel(e)
            return

    # --- Resumo rápido no topo ---
    total_simulados = emd.contar_simulados_totais(resumo_estudos)
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Estudos", len(estudos))
    col_b.metric("Participantes", len(participantes))
    col_c.metric("Simulados gerados (total)", total_simulados)

    with st.expander("👥 Gerenciar participantes"):
        st.caption(
            "Cada participante recebe login próprio (usuário + senha, não é "
            "a senha de admin) e só enxerga, na tela \"Responder simulado\", "
            "os estudos/categorias que você liberar aqui pra ele. Usuário não "
            "diferencia maiúsculas de minúsculas (Ana = ana = ANA)."
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

        # --- Opções do multiselect: categorias inteiras + estudos individuais,
        # com rótulos visuais diferentes pra distinguir um do outro. Convertemos
        # ida e volta entre o rótulo exibido e o valor gravado (categoria:X / slug). ---
        rotulo_por_valor = {pm.marcador_categoria(c): f"📁 {c} (categoria inteira)" for c in categorias_existentes}
        rotulo_por_valor.update({n: f"📄 {n}" for n in nomes_estudos})
        valor_por_rotulo = {v: k for k, v in rotulo_por_valor.items()}

        opcoes_multiselect = (
            [f"📁 {c} (categoria inteira)" for c in categorias_existentes]
            + [f"📄 {n}" for n in nomes_estudos]
        )
        default_multiselect = [
            rotulo_por_valor[v] for v in permitidos_atuais
            if v != pm.TODOS and v in rotulo_por_valor
        ]

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
            selecao_multiselect = st.multiselect(
                "Categorias inteiras e/ou estudos individuais permitidos",
                opcoes_multiselect,
                default=default_multiselect,
                disabled=acesso_total,
                help="Marcar uma categoria libera automaticamente todos os estudos "
                     "dela, inclusive os que forem criados depois.",
            )
            salvar_p = st.form_submit_button("Salvar participante")

        if salvar_p:
            if not usuario_p:
                st.error("Preencha o usuário.")
            else:
                with st.status(f"Salvando participante '{usuario_p}'...", expanded=True) as status_p:
                    try:
                        if acesso_total:
                            permitidos = [pm.TODOS]
                        else:
                            permitidos = [valor_por_rotulo[r] for r in selecao_multiselect]
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

    with st.expander("🏷️ Gerenciar categorias"):
        st.caption(
            "Categorias organizam os estudos em grupos (ex: Analista de "
            "Dados, Contador, Psicóloga). Cadastre aqui antes de criar um "
            "estudo novo, ou antes de liberar acesso por categoria inteira "
            "pra um participante."
        )

        with st.form("form_nova_categoria"):
            nome_nova_categoria = st.text_input("Nova categoria")
            adicionar_cat = st.form_submit_button("Adicionar categoria")

        if adicionar_cat:
            with st.status(f"Adicionando '{nome_nova_categoria}'...", expanded=True) as status_cat:
                try:
                    cat.adicionar_categoria(nome_nova_categoria)
                    status_cat.update(label="✅ Categoria adicionada!", state="complete")
                    _categorias_cacheadas.clear()
                    st.session_state["msg_sucesso"] = f"Categoria '{nome_nova_categoria}' adicionada."
                    st.rerun()
                except ValueError as e:
                    status_cat.update(label="❌ Não foi possível adicionar.", state="error")
                    st.error(str(e))
                except Exception as e:
                    status_cat.update(label="❌ Erro inesperado.", state="error")
                    _erro_drive_amigavel(e)

        if categorias_cadastradas:
            contagem_por_categoria = {}
            for r in resumo_estudos:
                contagem_por_categoria[r["categoria"]] = contagem_por_categoria.get(r["categoria"], 0) + 1

            st.write("**Categorias cadastradas:**")
            for i, nome_cat in enumerate(categorias_cadastradas):
                qtd = contagem_por_categoria.get(nome_cat, 0)
                col_info, col_botao = st.columns([4, 1])
                with col_info:
                    st.write(f"- **{nome_cat}** ({qtd} estudo(s) usando)")
                with col_botao:
                    if st.button("Remover", key=f"remover_categoria_{i}_{nome_cat}"):
                        with st.status(f"Removendo '{nome_cat}'...", expanded=True) as status_rc:
                            cat.remover_categoria(nome_cat)
                            status_rc.update(label="✅ Removida da lista!", state="complete")
                        _categorias_cacheadas.clear()
                        st.session_state["msg_sucesso"] = (
                            f"Categoria '{nome_cat}' removida da lista de opções "
                            f"(estudos que já usavam ela continuam com esse valor)."
                        )
                        st.rerun()
        else:
            st.caption("Nenhuma categoria cadastrada ainda.")

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
        if not categorias_cadastradas:
            st.warning(
                "Nenhuma categoria cadastrada ainda. Abra \"🏷️ Gerenciar "
                "categorias\" acima e cadastre pelo menos uma antes de criar "
                "um estudo novo."
            )
            return

        categoria_escolhida_form = st.selectbox("Categoria", categorias_cadastradas, key="nova_categoria_estudo_select")

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
                config, folder_id = emd.criar_estudo(nome_estudo, banca, nivel, eixos, categoria_escolhida_form)
            except ValueError as e:
                st.error(str(e))
                return
            except Exception as e:
                _erro_drive_amigavel(e)
                return

            _resumo_estudos_cacheado.clear()
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
        f"Categoria: {config.get('categoria', cat.CATEGORIA_PADRAO)} · "
        f"Banca: {config['banca']} · Nível: {config['nivel']} · "
        f"Simulações já geradas: {config['simulacao_atual']}"
    )

    with st.expander("🏷️ Trocar categoria deste estudo"):
        # Sempre inclui a categoria ATUAL do estudo nas opções, mesmo que ela
        # já tenha sido removida do cadastro (ex: um "Geral" automático de
        # estudo antigo) — evita erro de índice e não muda nada sem querer.
        categoria_atual = config.get("categoria", cat.CATEGORIA_PADRAO)
        opcoes_categoria_edicao = sorted(set(categorias_cadastradas) | {categoria_atual})
        indice_atual = opcoes_categoria_edicao.index(categoria_atual)

        nova_escolha = st.selectbox("Categoria", opcoes_categoria_edicao, index=indice_atual, key="editar_categoria_select")

        if st.button("Salvar categoria"):
            config = emd.atualizar_categoria(folder_id, config, nova_escolha)
            st.session_state["estudo_config"] = config
            _resumo_estudos_cacheado.clear()
            st.session_state["msg_sucesso"] = f"Categoria atualizada para '{nova_escolha}'."
            st.rerun()

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
                    _resumo_estudos_cacheado.clear()
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

    chave_ativa = f"geracao_ativa_{folder_id}"
    chave_escolha = f"geracao_escolha_{folder_id}"
    chave_confirmar_geracao = f"confirmar_geracao_{folder_id}"

    # --- Geração em andamento nesta sessão: processa um passo e continua sozinha ---
    if st.session_state.get(chave_ativa):
        ck = st.session_state[f"geracao_ck_{folder_id}"]
        wb_geracao = st.session_state[f"geracao_wb_{folder_id}"]
        config_geracao = st.session_state[f"geracao_config_{folder_id}"]

        st.subheader(f"🎲 Gerando simulado nº {ck['numero_simulacao']}")
        prontos, pendentes = gm.progresso_resumo(ck)
        if prontos:
            st.write(f"✅ Prontos: {', '.join(prontos)}")
        if pendentes:
            st.write(f"⏳ Faltam: {', '.join(pendentes)}")

        if st.button("🛑 Cancelar geração"):
            st.session_state[f"geracao_cancelar_{folder_id}"] = True

        resultado = _passo_geracao(folder_id, config_geracao, wb_geracao, ck)

        if resultado == "continuar":
            st.rerun()

        for chave in (chave_ativa, f"geracao_ck_{folder_id}", f"geracao_wb_{folder_id}",
                      f"geracao_config_{folder_id}", f"geracao_cancelar_{folder_id}"):
            st.session_state.pop(chave, None)
        _resumo_estudos_cacheado.clear()

        if resultado == "concluido":
            st.session_state["estudo_config"] = config_geracao
            st.success(f"✅ Simulado nº {ck['numero_simulacao']} concluído! Arquivo atualizado no Drive.")
            resumo_reaproveitamento = ck.get("resumo_reaproveitamento") or []
            if resumo_reaproveitamento:
                with st.expander(f"♻️ {len(resumo_reaproveitamento)} tema(s) reaproveitado(s) de outros estudos"):
                    for linha in resumo_reaproveitamento:
                        st.write(f"- {linha}")
        elif resultado == "cancelado":
            st.warning(
                "Geração cancelada. O progresso até aqui foi salvo — clique em "
                "\"Gerar novo simulado\" de novo pra retomar de onde parou."
            )
        elif resultado == "erro":
            st.error(
                f"Geração interrompida: {ck['erro_mensagem']}. O progresso até "
                f"aqui foi salvo — clique em \"Gerar novo simulado\" de novo pra retomar."
            )
        return

    # --- Existe uma geração incompleta de antes: pergunta retomar ou zerar ---
    if st.session_state.get(chave_escolha):
        ck_pendente = st.session_state[chave_escolha]
        prontos, pendentes = gm.progresso_resumo(ck_pendente)
        st.warning(
            f"Existe uma geração incompleta do simulado nº {ck_pendente['numero_simulacao']} "
            f"(status anterior: {ck_pendente['status']}). "
            f"Prontos: {', '.join(prontos) or '—'}. Faltam: {', '.join(pendentes) or '—'}."
        )
        col_retomar, col_zero = st.columns(2)
        if col_retomar.button("▶️ Retomar de onde parou", type="primary"):
            ck_pendente["status"] = "em_andamento"
            ck_pendente["erro_mensagem"] = None
            with st.spinner("Carregando planilha..."):
                st.session_state[f"geracao_wb_{folder_id}"] = ds.baixar_workbook(folder_id)
            st.session_state[f"geracao_ck_{folder_id}"] = ck_pendente
            st.session_state[f"geracao_config_{folder_id}"] = config
            st.session_state.pop(chave_escolha, None)
            st.session_state[chave_ativa] = True
            st.rerun()
        if col_zero.button("🆕 Começar do zero (descarta o anterior)"):
            gm.apagar_checkpoint(folder_id)
            st.session_state.pop(chave_escolha, None)
            st.session_state[chave_confirmar_geracao] = True
            st.rerun()
        return

    # --- Confirmação em duas etapas antes de gerar (gasta créditos reais de API) ---
    if not st.session_state.get(chave_confirmar_geracao):
        if st.button("🎲 Gerar novo simulado", type="primary"):
            with st.spinner("Verificando se há geração pendente..."):
                checkpoint_existente = gm.carregar_checkpoint(folder_id)
            if checkpoint_existente and checkpoint_existente.get("status") in ("em_andamento", "erro", "cancelado"):
                st.session_state[chave_escolha] = checkpoint_existente
            else:
                st.session_state[chave_confirmar_geracao] = True
            st.rerun()
    else:
        total_questoes_estudo = sum(d["qtd_questoes"] for d in config["eixos"].values())
        st.warning(
            f"Isso vai gerar até {total_questoes_estudo} questões novas via IA, "
            f"consumindo créditos reais da sua conta OpenAI. Confirma?"
        )
        meses_janela_reuso = st.slider(
            "Reaproveitar conteúdo parecido de outros estudos gerado nos últimos quantos meses?",
            min_value=1, max_value=12, value=6,
            help="Antes de gerar um tema, o app procura em outros estudos (mesmo nível) "
                 "um tema parecido dentro dessa janela de tempo, e reaproveita as "
                 "questões em vez de chamar a IA de novo, economizando créditos.",
        )
        col_sim, col_nao = st.columns(2)
        confirmou = col_sim.button("Sim, gerar agora", type="primary")
        if col_nao.button("Cancelar"):
            st.session_state.pop(chave_confirmar_geracao, None)
            st.rerun()

        if confirmou:
            st.session_state.pop(chave_confirmar_geracao, None)
            numero_simulacao = config["simulacao_atual"] + 1
            with st.spinner("Carregando planilha..."):
                st.session_state[f"geracao_wb_{folder_id}"] = ds.baixar_workbook(folder_id)
            ck_novo = gm.checkpoint_iniciar(numero_simulacao)
            ck_novo["meses_janela_reuso"] = meses_janela_reuso
            st.session_state[f"geracao_ck_{folder_id}"] = ck_novo
            st.session_state[f"geracao_config_{folder_id}"] = config
            st.session_state[chave_ativa] = True
            st.rerun()


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
