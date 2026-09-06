import random


def distribuir_temas(config: dict, eixo: str, qtd_total: int) -> dict:
    """
    Distribui qtd_total questões entre os temas do eixo, combinando peso
    (Pareto) com um ciclo de cobertura garantida por rotação:

      1. Olha o historico_temas do eixo: separa os temas que AINDA não
         apareceram no ciclo atual ("não vistos") dos que já apareceram.
      2. Prioriza os "não vistos": cada um recebe 1 questão garantida,
         dentro do limite do orçamento (qtd_total). Se o eixo tiver mais
         temas não vistos do que questões disponíveis, cobre o máximo
         possível agora e o resto entra no próximo simulado.
      3. O orçamento restante (depois da garantia) é distribuído entre
         TODOS os temas do eixo proporcionalmente ao peso, pelo método
         dos maiores restos (Hare quota) — então tema de peso alto
         continua puxando mais questões, mesmo já tendo aparecido.
      4. Ao final, marca no historico_temas quem apareceu neste simulado.
         Quando todo o eixo já foi coberto pelo menos uma vez, o ciclo
         reseta (limpa o histórico) e a prioridade volta a valer para
         todos os temas — mas isso NÃO reseta a memória de perguntas já
         feitas: perguntas_ja_usadas() (excel_manager.py) continua lendo
         o Consolidado e evitando repetir a mesma pergunta, mesmo depois
         do ciclo de temas reiniciar.

    Efeito prático: ao longo de vários simulados, todo tema do eixo acaba
    sendo coberto pelo menos uma vez a cada ciclo (ceil(n_temas/qtd_total)
    simulados, no pior caso), em vez de depender só da sorte do resto.

    Retorna um dict {tema: quantidade}, só com temas que receberam >= 1
    questão.
    """
    pesos = config["eixos"][eixo]["temas"]
    temas = list(pesos.keys())
    n = len(temas)
    total_peso = sum(pesos.get(t, 1) for t in temas)

    usados = config["historico_temas"].setdefault(eixo, [])
    nao_vistos = [t for t in temas if t not in usados]

    # se já cobriu tudo (ou histórico vazio na 1ª rodada), todo mundo concorre
    pool_prioritario = nao_vistos if nao_vistos else temas

    contagem = {t: 0 for t in temas}

    # 1) garante 1 questão para os temas prioritários, respeitando o orçamento
    #    (em caso de sobra de temas prioritários vs orçamento, prioriza peso maior)
    garantidos = min(len(pool_prioritario), qtd_total)
    ordem_prioridade = sorted(pool_prioritario, key=lambda t: -pesos.get(t, 1))
    for t in ordem_prioridade[:garantidos]:
        contagem[t] = 1

    if garantidos < len(pool_prioritario):
        faltantes = [t for t in pool_prioritario if contagem[t] == 0]
        print(
            f"[{eixo}] ⚠️ {len(faltantes)} tema(s) não coube(ram) neste simulado "
            f"(orçamento menor que temas pendentes) — ficam para o próximo ciclo."
        )

    # 2) distribui o restante do orçamento proporcionalmente ao peso,
    #    entre TODOS os temas (Hare quota / maiores restos)
    restante = qtd_total - garantidos
    if restante > 0:
        cotas = {t: restante * pesos.get(t, 1) / total_peso for t in temas}
        extra = {t: int(cota) for t, cota in cotas.items()}
        falta_alocar = restante - sum(extra.values())

        restos = sorted(
            temas,
            key=lambda t: (cotas[t] - extra[t], random.random()),
            reverse=True,
        )
        for t in restos[:falta_alocar]:
            extra[t] += 1

        for t in temas:
            contagem[t] += extra[t]

    resultado = {t: q for t, q in contagem.items() if q > 0}

    # 3) atualiza o histórico do ciclo com quem apareceu agora
    for t in resultado:
        if t not in usados:
            usados.append(t)

    # 4) ciclo completo -> reseta para o próximo (perguntas_ja_usadas ainda
    #    evita repetição de conteúdo via o Consolidado, então o reset aqui
    #    é só sobre PRIORIDADE de tema, não sobre memória de perguntas)
    if len(usados) >= n:
        usados.clear()
        print(f"[{eixo}] ✓ Ciclo de temas completo — reiniciando rotação de prioridade.")

    resumo = ", ".join(
        f"{t.split(':')[0].strip()}={q}"
        for t, q in sorted(resultado.items(), key=lambda item: -item[1])
    )
    print(f"[{eixo}] Temas sorteados ({len(resultado)}/{n}): {resumo}")
    return resultado