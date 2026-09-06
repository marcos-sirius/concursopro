"""
Simulador EARA (Python)
========================
Fluxo:
  1. Pergunta se é um estudo novo ou em andamento.
  2a. Novo: pede nome do estudo, banca, nível e o Excel de conteúdo programático
      (template_conteudo_programatico.xlsx preenchido).
  2b. Em andamento: lista estudos existentes e carrega o config.json.
  3. Gera o simulado: para cada eixo, distribui a quantidade de questões
     entre os temas do eixo proporcionalmente ao peso de cada um (mesclando
     vários temas por eixo, não só o de maior peso), gera questões via API
     por tema, grava na guia do eixo (resetada) e depois consolida tudo na
     guia Consolidado com o número da simulação.

Requisitos: pip install openpyxl anthropic python-docx
Variável de ambiente: ANTHROPIC_API_KEY
"""
import sys

import config_manager as cm
import conteudo_loader as cl
import excel_manager as em
import ia_gerador as ia
import sorteio


def perguntar(texto: str) -> str:
    return input(texto).strip()


def perguntar_caminho(texto: str) -> str:
    """Remove aspas que o Windows adiciona ao usar 'Copiar como caminho'."""
    return input(texto).strip().strip('"').strip("'")


def fluxo_novo_estudo() -> dict:
    print("\n=== NOVO ESTUDO ===")
    nome_estudo = perguntar("Nome do estudo/concurso: ")
    banca = perguntar("Banca examinadora: ")
    nivel = perguntar("Nível (ex: Médio/Superior): ")
    caminho_excel = perguntar_caminho(
        "Caminho do Excel de conteúdo programático "
        "(use template_conteudo_programatico.xlsx como base): "
    )

    eixos = cl.carregar_conteudo_programatico(caminho_excel)
    print(f"\nEixos carregados: {', '.join(eixos.keys())}")

    config = cm.criar_estudo(nome_estudo, banca, nivel, eixos)
    print(f"Estudo criado em: estudos/{config['slug']}/")
    return config


def fluxo_estudo_existente() -> dict:
    estudos = cm.listar_estudos()
    if not estudos:
        print("Nenhum estudo encontrado. Vamos criar um novo.")
        return fluxo_novo_estudo()

    print("\n=== ESTUDOS DISPONÍVEIS ===")
    for i, slug in enumerate(estudos, 1):
        print(f"{i}. {slug}")
    escolha = int(perguntar("Escolha o número do estudo: ")) - 1
    slug = estudos[escolha]
    config = cm.carregar_config(slug)

    resync = perguntar(
        "Ressincronizar eixos/temas/pesos com o Excel de conteúdo programático "
        "antes de gerar? (s/n): "
    ).lower()
    if resync.startswith("s"):
        caminho_excel = perguntar_caminho("Caminho do Excel atualizado: ")
        novos_eixos = cl.carregar_conteudo_programatico(caminho_excel)
        config = cm.ressincronizar_eixos(config, novos_eixos)
        print("✓ Eixos/temas atualizados (histórico de temas já usados foi preservado).")

    return config


def gerar_simulado(config: dict):
    excel_path = config["excel_path"]
    wb = em.abrir_ou_criar(excel_path)

    numero_simulacao = config["simulacao_atual"] + 1
    print(f"\n=== Gerando simulação nº {numero_simulacao} ===")

    eixos_questoes = {}

    for eixo, dados in config["eixos"].items():
        distribuicao = sorteio.distribuir_temas(config, eixo, dados["qtd_questoes"])

        blocos_eixo = []
        for tema, qtd_tema in distribuicao.items():
            evitar = em.perguntas_ja_usadas(wb, eixo, tema=tema, limite=30)
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
                print(f"[{eixo}] ⚠️ Nenhuma questão gerada para o tema \"{tema}\".")

        # Balanceamento de gabarito por EIXO (não por tema): junta as questões
        # de TODOS os temas sorteados neste eixo/simulado e só então redistribui
        # a posição da alternativa correta (A-E). Isso evita o problema de temas
        # com poucas questões (1-3) ficarem sempre com o gabarito na mesma letra.
        # balancear_gabaritos muta as questões in-place, então os itens dentro
        # de blocos_eixo já saem atualizados.
        todas_questoes_eixo = [q for _, questoes in blocos_eixo for q in questoes]
        if todas_questoes_eixo:
            ia.balancear_gabaritos(todas_questoes_eixo)
            print(
                f"[{eixo}] ✓ Gabaritos balanceados em bloco único "
                f"({len(todas_questoes_eixo)} questões, {len(blocos_eixo)} tema(s))."
            )

        if not blocos_eixo:
            print(f"[{eixo}] ⚠️ Nenhuma questão gerada — guia ficará vazia.")
            em.resetar_guia_eixo(wb, eixo)
            continue

        em.resetar_guia_eixo(wb, eixo)
        total_eixo = 0
        for tema, questoes in blocos_eixo:
            em.gravar_questoes_no_eixo(wb, eixo, tema, questoes)
            total_eixo += len(questoes)

        eixos_questoes[eixo] = blocos_eixo
        print(f"[{eixo}] ✓ {total_eixo} questões gravadas na guia ({len(blocos_eixo)} temas).")

    em.consolidar(wb, numero_simulacao, eixos_questoes)
    wb.save(excel_path)

    config["simulacao_atual"] = numero_simulacao
    cm.salvar_config(config)

    print(f"\n✅ Simulado nº {numero_simulacao} concluído!")
    print(f"Arquivo: {excel_path}")


def main():
    print("=== SIMULADOR EARA (Python) ===")
    resposta = perguntar("Novo estudo? (s/n): ").lower()

    if resposta.startswith("s"):
        config = fluxo_novo_estudo()
    else:
        config = fluxo_estudo_existente()

    gerar_simulado(config)


if __name__ == "__main__":
    main()
