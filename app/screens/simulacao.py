# -*- coding: utf-8 -*-
"""
Tela da simulação de manejo: o usuário define em que idade acontece o
Raleio, o 1º Desbaste, o 2º Desbaste e o Corte Raso, e escolhe a
intensidade de cada intervenção (Corte Raso não tem intensidade — é só
uma idade de colheita final) entre os valores já testados na Simulação
de Intensidades. A tela gera o "esqueleto" idade a idade da simulação —
cada talhão da base IFC ByTalhao repetido de 1 até a idade máxima de
manejo (Configurações), marcando em qual idade cai cada intervenção e
trazendo o forma/escala/dap_med/dap_max/dap_min/fustes_atual em vigor
pra cada talhão (ver core/simulacao.py:gerar_populacao pra a guarda de
fustes/ha mínimo que decide se cada manejo é de fato aplicado).

Porte completo de app/screens/simulacao.py (Tkinter) — mesma lógica de
geração (única e em lote de múltiplos cenários), mesmas 3 abas do
Gráfico de Resultados, só a camada de widgets trocada pra Qt. Os dois
`threading.Thread`+`queue.Queue`+`self.after(...)` de polling do
original (geração única e geração em lote) viram `QThread` com sinais
(ver _ThreadGerarSimulacao/_ThreadGerarLote) — Qt entrega os sinais
direto na thread da GUI, sem fila nem polling.
"""
import itertools
import os
import shutil
import sqlite3
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..core import construtores, projeto, simulacao
from ..core.db import _formatar_tamanho_bytes, conectar, conectar_caminho
from ..theme import icones, qss
from ..widgets.cartao import Cartao
from ..widgets.grafico_simulacao import GraficoPorClasseSimulacao, GraficoResultadoSimulacao
from ..widgets.tabela import IID_RESUMO, Tabela, emoldurar_tabela
from .base import TelaBase


class _ThreadGerarSimulacao(QThread):
    """Roda inteiramente numa thread de fundo — nada aqui toca em widget
    nenhum; resultado/erro só via sinais Qt, entregues automaticamente na
    thread da GUI. Sem sufixo (grava direto nas tabelas canônicas, mesmo
    comportamento de sempre) — ver _ThreadGerarLote pro modo "Múltiplos
    cenários"."""

    concluido = Signal(dict)
    falhou = Signal(object)

    def __init__(self, caminho_trabalho, configuracao, parent=None):
        super().__init__(parent)
        self._caminho_trabalho = caminho_trabalho
        self._configuracao = configuracao

    def run(self):
        try:
            conn = conectar_caminho(self._caminho_trabalho)
            try:
                resultado = _gerar_uma_simulacao(conn, self._configuracao)
            finally:
                conn.close()
            self.concluido.emit(resultado)
        except Exception as e:
            self.falhou.emit(e)


# Tentativas/espera de _commitar_com_retry abaixo — um lote de milhares
# de cenários pode levar horas, com até 32 processos worker lendo o mesmo
# arquivo de trabalho ao mesmo tempo que o processo principal escreve (ver
# _worker_inicializar_lote/_executar_lote_paralelo): mesmo com
# busy_timeout=30s (core/db.py:_aplicar_pragmas_performance), uma colisão
# pontual mais longa que isso é esperada em runs longos, não um motivo pra
# abortar o lote inteiro.
_TENTATIVAS_COMMIT_LOTE = 6
_ESPERA_INICIAL_COMMIT_LOTE = 5.0


def _commitar_com_retry(conn, tentativas=_TENTATIVAS_COMMIT_LOTE, espera_inicial=_ESPERA_INICIAL_COMMIT_LOTE):
    """conn.commit() já espera até busy_timeout (30s) internamente antes de
    levantar "database is locked" (sqlite3.OperationalError) — mas isso é
    só UMA tentativa. Numa geração em lote de horas (ver _ThreadGerarLote),
    uma colisão de lock que passe até desses 30s (outro processo/thread
    segurando o arquivo por mais tempo que isso, ex: um cenário
    particularmente grande sendo lido por um worker) não deveria derrubar
    o lote inteiro — tenta de novo, com espera exponencial, antes de
    desistir de vez (nesse caso, quem chamou trata como falha daquele
    cenário específico, não do lote)."""
    espera = espera_inicial
    for tentativa in range(tentativas):
        try:
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or tentativa == tentativas - 1:
                raise
            time.sleep(espera)
            espera *= 2


def _marcar_erro_cenario_com_retry(conn, cenario_id, erro):
    """Grava o status "Erro: ..." de um cenário (ver _executar_lote_
    sequencial/_executar_lote_paralelo) tolerando o mesmo tipo de colisão
    de lock que _commitar_com_retry — se mesmo assim não conseguir
    persistir o status (lock longo demais mesmo depois de todas as
    tentativas), desfaz a transação e segue o lote: o cenário já está em
    `com_erro` (guardado por quem chama) e será tentado de novo via "Gerar
    pendentes"/"Reiniciar", mesmo sem o status "Erro: ..." refletido na
    tabela desta vez."""
    try:
        conn.execute(
            "UPDATE simulacao_cenarios SET status = ? WHERE id = ?",
            (f"Erro: {erro}"[:200], cenario_id))
        _commitar_com_retry(conn)
    except sqlite3.OperationalError:
        conn.rollback()


class _ThreadGerarLote(QThread):
    """Roda inteiramente numa thread de fundo — nada aqui toca em widget
    nenhum. Uma falha num cenário só marca AQUELE cenário como erro
    (status na tabela simulacao_cenarios) e segue pro próximo — não aborta
    o lote inteiro (ver `falhou`, reservado só pra uma falha inesperada
    fora do laço, ex: não conseguir abrir a conexão, ou o mapeamento de
    colunas de `preparar_contexto_lote` falhar ANTES do laço começar).

    `solicitar_parada()` (botão "Parar" na GUI) pede uma parada NO
    LIMITE entre dois cenários — nunca no meio da gravação de um (cada
    cenário já commita sozinho antes do laço checar de novo, ver `run`) —
    então o lote sempre para com o último cenário processado num estado
    consistente (commitado, "Gerado" ou "Erro: ..."), nunca pela metade.
    `concluido` sai com "interrompido": True nesse caso, pra GUI avisar
    quantos ficaram pendentes. "parou_por_disco": True é uma parada
    parecida, só que decidida sozinha (não pelo botão "Parar") quando o
    espaço livre em disco fica curto demais pro próximo cenário — ver a
    checagem logo no início de `run` e a estimativa por cenário no laço."""

    progresso = Signal(int, int, str)
    concluido = Signal(dict)
    falhou = Signal(object)

    def __init__(self, caminho_trabalho, configuracao_comum, cenarios, calcular_mip=False, parent=None):
        super().__init__(parent)
        self._caminho_trabalho = caminho_trabalho
        self._configuracao_comum = configuracao_comum
        self._cenarios = cenarios
        self._calcular_mip = calcular_mip
        self._parar_solicitada = False
        self._parou_por_disco = False

    def solicitar_parada(self):
        """Chamado pela GUI (botão "Parar") — NÃO aborta o cenário em
        andamento (cada um já commita sozinho ao terminar, ver `run` logo
        abaixo), só impede o PRÓXIMO da lista de começar. Checado no topo
        de cada iteração do laço em `run`; um bool simples já basta (só a
        GUI escreve, só a própria thread lê, sem seção crítica de
        verdade)."""
        self._parar_solicitada = True

    def run(self):
        total = len(self._cenarios)

        pasta_trabalho = Path(self._caminho_trabalho).parent
        livre_inicial = shutil.disk_usage(pasta_trabalho).free
        if livre_inicial < _ESPACO_MINIMO_ABSOLUTO_LOTE:
            self.falhou.emit(ValueError(
                "Espaço em disco insuficiente pra começar o lote: só "
                f"{_formatar_tamanho_bytes(livre_inicial)} livres em \"{pasta_trabalho}\". "
                "Libere espaço e tente novamente."))
            return
        # Não dá pra prever de antemão quanto UM cenário vai pesar (varia
        # por projeto — nº de talhões, construtores ativos, colunas por
        # classe) — mede o crescimento real do arquivo de trabalho depois
        # do 1º cenário do lote (sucesso ou erro, os dois podem ter escrito
        # algo antes de falhar) e usa isso, com folga
        # (_MARGEM_SEGURANCA_DISCO_LOTE), pra decidir se ainda cabe o
        # próximo cenário antes de tentar — evita o SQLite abortar no meio
        # de uma gravação com "database or disk is full".
        tamanho_antes_lote = Path(self._caminho_trabalho).stat().st_size

        try:
            conn = conectar_caminho(self._caminho_trabalho)
            try:
                # Monta UMA VEZ pro lote inteiro tudo que gerar_populacao
                # recalcularia identicamente a cada cenário (leitura de
                # base_ifc_talhao, ajuste Weibull "Por Talhão", validações de
                # coluna — ver core/simulacao.py:preparar_contexto_lote) —
                # maior ganho de tempo no lote, já que esse trabalho não
                # depende da idade/intensidade de nenhum cenário específico.
                contexto_lote = simulacao.preparar_contexto_lote(
                    conn, self._configuracao_comum["coluna_talhao_ifc"],
                    self._configuracao_comum["coluna_fustes_observados"],
                    coluna_dap_med_observado=self._configuracao_comum["coluna_dap_med_observado"],
                    coluna_dap_max_observado=self._configuracao_comum["coluna_dap_max_observado"],
                    coluna_dap_min_observado=self._configuracao_comum["coluna_dap_min_observado"],
                    coluna_ht_observado=self._configuracao_comum["coluna_ht_observado"],
                    coluna_vtcc_observado=self._configuracao_comum["coluna_vtcc_observado"],
                    coluna_cv_dap_observado=self._configuracao_comum["coluna_cv_dap_observado"],
                    coluna_data_plantio=self._configuracao_comum["coluna_data_plantio"],
                )

                # Paralelo (ProcessPoolExecutor, um cenário por núcleo de
                # CPU) só quando compensa e é seguro: mais de 1 cenário
                # (não vale montar um pool de processos pra um só) e, se
                # algum construtor ativo tiver nó "Custo de Formação", só
                # se a coluna de talhão estiver configurada — nesse caso
                # calcular_cenario_em_memoria sincroniza as linhas de
                # formação direto no DataFrame (ver
                # core/construtores.py:_sincronizar_linhas_formacao_em_
                # memoria); sem coluna de talhão, aplicar_construtores_em_
                # memoria devolve None e o lote cai pro caminho antigo,
                # via banco. Checado uma vez só aqui, antes do lote
                # inteiro começar.
                construtores_ativos = [
                    c for c in construtores.listar_construtores(conn)
                    if c["tabela_origem"] == simulacao.TABELA_POPULACAO and c["ativo"]]
                precisa_coluna_talhao = construtores.grafo_tem_no_custo_formacao(
                    [c["grafo"] for c in construtores_ativos])
                modo_paralelo = total > 1 and not (
                    precisa_coluna_talhao and not simulacao.obter_coluna_talhao(conn))

                # os.cpu_count() conta núcleos LÓGICOS (com hyperthreading/
                # SMT, ex: 32 núcleos físicos = 64 aqui) — deixa 1 de fora
                # pra GUI/SO, e um teto de 32 processos por padrão: cada
                # worker já usa vários núcleos internamente via pandas/numpy
                # pra operações grandes (mesmo com as libs de álgebra linear
                # limitadas a 1 thread cada, ver _worker_inicializar_lote),
                # então N processos não escala 1:1 com núcleos livres pra
                # sempre — 32 já satura a maioria dos gargalos (I/O de disco
                # na persistência, overhead de pickle/IPC) antes de precisar
                # de mais. Calculado aqui (não dentro de
                # _executar_lote_paralelo) pra ficar visível também no
                # perfil do caso abaixo, sem recalcular.
                n_workers = max(1, min(32, (os.cpu_count() or 4) - 1))

                # Windows cria workers por spawn: caches module-level não
                # são herdados. Calcula uma vez no coordenador e envia no
                # contexto inicial de cada processo, evitando que os N
                # primeiros cenários reconstruam a mesma tabela de
                # afilamento. A chave é toda baseada no conteúdo do nó.
                tempo_preaquecimento_afilamento = 0.0
                if modo_paralelo:
                    _marca_preaquecimento = time.perf_counter()
                    contexto_lote["cache_afilamento"] = construtores.preaquecer_cache_afilamento(
                        [c["grafo"] for c in construtores_ativos],
                        contexto_lote["baseline"]["classes_diametricas"],
                        construtores.obter_dimensoes_tora(conn),
                    )
                    tempo_preaquecimento_afilamento = time.perf_counter() - _marca_preaquecimento

                # Perfil do caso — nº de talhões/idade máxima/classes
                # diamétricas/construtores ativos/cenários, calculado UMA
                # VEZ pro lote inteiro (não muda cenário a cenário) — só
                # contexto pro diagnóstico de performance mostrado em
                # TelaSimulacao._finalizar_geracao_lote/_gravar_log_
                # construtores, não usado por nenhum cálculo da simulação.
                perfil_caso = simulacao.obter_perfil_caso(conn)
                perfil_caso.update({
                    "n_construtores_ativos": len(construtores_ativos),
                    "n_cenarios": total,
                    "modo": "paralelo" if modo_paralelo else "sequencial",
                    "n_workers": n_workers if modo_paralelo else 1,
                })

                if modo_paralelo:
                    gerados, com_erro, tempos_totais, tempos_construtores = self._executar_lote_paralelo(
                        conn, contexto_lote, pasta_trabalho, tamanho_antes_lote, total, n_workers)
                else:
                    gerados, com_erro, tempos_totais, tempos_construtores = self._executar_lote_sequencial(
                        conn, contexto_lote, pasta_trabalho, tamanho_antes_lote, total)
                if tempo_preaquecimento_afilamento >= 0.01:
                    # Custo único do lote, não deve ser dividido por cenário
                    # como se tivesse ocorrido N vezes; ainda entra no total
                    # para o relatório não esconder trabalho deslocado dos
                    # workers para o coordenador.
                    tempos_totais["preaquecimento_afilamento"] = tempo_preaquecimento_afilamento
            finally:
                conn.close()
            self.concluido.emit({
                "gerados": gerados, "com_erro": com_erro, "total": total, "tempos_totais": tempos_totais,
                "tempos_construtores": tempos_construtores, "interrompido": self._parar_solicitada,
                "parou_por_disco": self._parou_por_disco, "perfil_caso": perfil_caso,
            })
        except Exception as e:
            self.falhou.emit(e)

    def _montar_configuracao_cenario(self, cenario):
        configuracao = dict(self._configuracao_comum)
        configuracao.update({
            "idade_raleio": cenario["idade_raleio"],
            "intensidade_raleio": cenario["intensidade_raleio"],
            "idade_desbaste_1": cenario["idade_desbaste_1"],
            "intensidade_desbaste_1": cenario["intensidade_desbaste_1"],
            "idade_desbaste_2": cenario["idade_desbaste_2"],
            "intensidade_desbaste_2": cenario["intensidade_desbaste_2"],
            "idade_corte_raso": cenario["idade_corte_raso"],
            "nome_cenario": cenario["nome"],
        })
        return configuracao

    def _executar_lote_sequencial(self, conn, contexto_lote, pasta_trabalho, tamanho_antes_lote, total):
        """Um cenário de cada vez, numa thread só — mesmo laço de sempre,
        usado quando `run` decide que o lote não pode (só 1 cenário) ou
        não deve (algum construtor ativo tem nó "Custo de Formação", ver
        `run`) rodar em paralelo (ver _executar_lote_paralelo)."""
        gerados = 0
        com_erro = []
        # Soma de resultado["tempos_estagios"] (ver _gerar_uma_simulacao)
        # de todo cenário gerado com sucesso — diagnóstico de qual etapa
        # (população/construtores/distribuição/volume por sortimento/MIP)
        # domina o tempo do LOTE inteiro, mostrado em
        # TelaSimulacao._finalizar_geracao_lote. Não inclui cenários com
        # erro (tempo parcial até a falha não é comparável ao resto).
        tempos_totais = {}
        # Detalhe por construtor (ver resumo_construtores["tempos"] em
        # core/construtores.py:aplicar_construtores_salvos/
        # _resumo_tempo_construtor — só lista um construtor aqui se ele
        # sozinho passou de 1s NAQUELE cenário) — quando "construtores"
        # domina tempos_totais acima, isso diz qual construtor E qual fase
        # dele (cálculo dos nós vs. gravação das colunas no banco).
        tempos_construtores = []
        delta_estimado_por_cenario = None
        # Id GLOBAL livre pra gravar em simulacao_lote_populacao — cresce
        # pelo lote inteiro, nunca reinicia por cenário (ver
        # core/simulacao.py:proximo_id_populacao_lote/persistir_cenario_
        # no_lote pro porquê: senão a FK lógica entre população e
        # distribuição, compartilhadas por todos os cenários, colide).
        proximo_id_populacao = simulacao.proximo_id_populacao_lote(conn)

        for numero, cenario in enumerate(self._cenarios, start=1):
            if self._parar_solicitada:
                break
            if delta_estimado_por_cenario:
                livre = shutil.disk_usage(pasta_trabalho).free
                necessario = delta_estimado_por_cenario * _MARGEM_SEGURANCA_DISCO_LOTE
                if livre < necessario:
                    self._parou_por_disco = True
                    break
            sufixo = f"__cenario{cenario['id']}"
            configuracao = self._montar_configuracao_cenario(cenario)
            try:
                resultado_cenario = _gerar_uma_simulacao(
                    conn, configuracao, sufixo_tabela=sufixo,
                    contexto_lote=contexto_lote, calcular_mip=self._calcular_mip,
                    cenario_id=cenario["id"], proximo_id_populacao=proximo_id_populacao)
                proximo_id_populacao = resultado_cenario.get(
                    "_proximo_id_populacao_lote", proximo_id_populacao)
                conn.execute(
                    "UPDATE simulacao_cenarios SET status = 'Gerado', "
                    "gerado_em = datetime('now', 'localtime') WHERE id = ?",
                    (cenario["id"],))
                # Ainda DENTRO do try de propósito (mudou de lugar — antes
                # ficava fora do bloco try/except do cenário): se um lock
                # persistente sobrar mesmo depois das tentativas de
                # _commitar_com_retry, cai no except abaixo e é tratado como
                # falha DESTE cenário — não uma falha inesperada do lote
                # inteiro (ver `falhou`, docstring da classe).
                _commitar_com_retry(conn)
                for etapa, segundos in resultado_cenario.get("tempos_estagios", {}).items():
                    tempos_totais[etapa] = tempos_totais.get(etapa, 0.0) + segundos
                tempos_construtores.extend(
                    f"[{cenario['nome']}] {linha}"
                    for linha in resultado_cenario.get("resumo_construtores", {}).get("tempos", []))
                gerados += 1
            except Exception as e:
                conn.rollback()
                _marcar_erro_cenario_com_retry(conn, cenario["id"], e)
                com_erro.append((cenario["nome"], str(e)))
            self.progresso.emit(numero, total, cenario["nome"])
            if delta_estimado_por_cenario is None:
                tamanho_agora = Path(self._caminho_trabalho).stat().st_size
                delta_estimado_por_cenario = max(0, tamanho_agora - tamanho_antes_lote)

        return gerados, com_erro, tempos_totais, tempos_construtores

    def _executar_lote_paralelo(self, conn, contexto_lote, pasta_trabalho, tamanho_antes_lote, total, n_workers):
        """Mesmo trabalho de _executar_lote_sequencial, só que a etapa
        mais pesada de cada cenário — população + construtores +
        distribuição + volume por sortimento, tudo em memória (ver
        core/simulacao.py:calcular_cenario_em_memoria) — roda num
        ProcessPoolExecutor, um cenário por núcleo de CPU disponível
        (`n_workers`, calculado em `run` — ver comentário lá — pra ficar
        visível também no perfil do caso, sem recalcular aqui), em vez de
        sequencial numa thread só. Só o processo PRINCIPAL (esta thread)
        grava no arquivo de trabalho (persistir_cenario_calculado) e roda
        o MIP contínuo quando pedido (precisa da tabela já gravada, ver
        core/simulacao.py: calcular_mip_para_cenario) — SQLite não aceita
        escrita concorrente de verdade (journal_mode=MEMORY, ver
        core/db.py), então cada resultado calculado por um worker é
        persistido em sequência, assim que fica pronto (ordem de
        CONCLUSÃO, não a ordem original da lista de cenários — só muda em
        que ordem a tabela `simulacao_cenarios` marca "Gerado", não o
        resultado em si).

        `solicitar_parada()`/a guarda de disco (mesma lógica de
        _executar_lote_sequencial) continuam funcionando: param de
        DESPACHAR cenário novo pro pool, mas deixam os que já estão em
        voo terminarem — não dá pra interromper um worker no meio do
        cálculo sem arriscar perder ou corromper o resultado dele."""
        gerados = 0
        com_erro = []
        tempos_totais = {}
        tempos_construtores = []
        delta_estimado_por_cenario = None
        # Id GLOBAL livre pra gravar em simulacao_lote_populacao — cresce
        # pelo lote inteiro, nunca reinicia por cenário (ver
        # core/simulacao.py:proximo_id_populacao_lote/persistir_cenario_
        # no_lote pro porquê: senão a FK lógica entre população e
        # distribuição, compartilhadas por todos os cenários, colide).
        proximo_id_populacao = simulacao.proximo_id_populacao_lote(conn)

        fila = list(self._cenarios)
        em_voo = {}

        with ProcessPoolExecutor(
            max_workers=n_workers, initializer=simulacao._worker_inicializar_lote,
            initargs=(self._caminho_trabalho, contexto_lote),
        ) as pool:
            while fila or em_voo:
                while fila and len(em_voo) < n_workers:
                    if self._parar_solicitada:
                        fila = []
                        break
                    if delta_estimado_por_cenario:
                        livre = shutil.disk_usage(pasta_trabalho).free
                        necessario = delta_estimado_por_cenario * _MARGEM_SEGURANCA_DISCO_LOTE
                        if livre < necessario:
                            self._parou_por_disco = True
                            fila = []
                            break
                    cenario = fila.pop(0)
                    sufixo = f"__cenario{cenario['id']}"
                    configuracao = self._montar_configuracao_cenario(cenario)
                    future = pool.submit(simulacao._worker_calcular_cenario, configuracao, sufixo)
                    em_voo[future] = (cenario, configuracao, sufixo, time.perf_counter())

                if not em_voo:
                    break

                # t_pronto logo após o wait() retornar, não depois de
                # future.result(): quando wait() reporta uma future pronta,
                # o worker já serializou o resultado e a thread interna do
                # ProcessPoolExecutor já desserializou — future.result() daí
                # em diante é quase instantâneo. Comparado contra t_submit
                # (guardado em `em_voo`) e os timestamps que o próprio
                # worker bate (`_t_inicio_worker`/`_t_fim_worker`, ver
                # core/simulacao.py:_worker_calcular_cenario), separa
                # "fila_despacho"/"ipc_retorno" abaixo do tempo de cálculo
                # puro — nada disso era medido antes. Quando wait() devolve
                # mais de uma future pronta de uma vez, todas compartilham
                # este mesmo t_pronto mesmo tendo terminado em instantes
                # ligeiramente diferentes — aproximação aceitável pra uma
                # métrica diagnóstica, não pra cobrança de SLA.
                prontos, _ = wait(list(em_voo.keys()), return_when=FIRST_COMPLETED)
                t_pronto = time.perf_counter()
                for future in prontos:
                    cenario, configuracao, sufixo, t_submit = em_voo.pop(future)
                    try:
                        manifesto_cenario = future.result()
                        # _t_inicio_worker/_t_fim_worker (ver
                        # core/simulacao.py:_worker_calcular_cenario):
                        # batidos NO PROCESSO WORKER, logo antes/depois do
                        # cálculo — junto com t_submit (quando esta thread
                        # despachou) e t_pronto (quando wait() reportou
                        # pronto), separam "fila_despacho" (tempo até o
                        # worker começar de verdade — pode incluir o
                        # processo ainda esquentando, import de numpy/
                        # scipy) de "ipc_retorno" (tempo entre o worker
                        # terminar e o resultado chegar aqui — serialização
                        # + transferência de volta) em vez de uma soma cega
                        # dos dois (a antiga "fila_ipc") que não dizia qual
                        # dos dois pesava.
                        t_fim_calculo = manifesto_cenario.pop("_t_fim_calculo_worker", None)
                        t_fim_worker = manifesto_cenario.pop("_t_fim_worker", None)
                        _marca_blob = time.perf_counter()
                        resultado_cenario = simulacao.persistir_manifesto_parquet(
                            conn, cenario["id"], manifesto_cenario)
                        t_inicio_worker = resultado_cenario.pop("_t_inicio_worker", None)
                        resultado_cenario["tempos_estagios"]["gravacao_blob_parquet"] = (
                            time.perf_counter() - _marca_blob)
                        tempos_estagios = resultado_cenario.get("tempos_estagios")
                        if tempos_estagios and t_inicio_worker is not None and t_fim_worker is not None:
                            tempos_estagios["fila_despacho"] = max(0.0, t_inicio_worker - t_submit)
                            tempos_estagios["ipc_retorno"] = max(0.0, t_pronto - t_fim_worker)
                            if t_fim_calculo is not None:
                                tempos_estagios["gravacao_parquet_worker"] = max(
                                    0.0, t_fim_worker - t_fim_calculo)
                        if self._calcular_mip:
                            # Compatibilidade temporária: o MIP atual lê as
                            # tabelas unificadas SQLite. Só materializa esse
                            # cenário nelas quando o usuário pediu o MIP;
                            # o caminho comum permanece exclusivamente
                            # Parquet e não faz milhões de INSERTs.
                            _marca = time.perf_counter()
                            resultado_mip = simulacao.carregar_cenario_parquet(conn, cenario["id"])
                            simulacao.persistir_cenario_calculado(
                                conn, resultado_mip, sufixo, commit=False,
                                cenario_id=cenario["id"],
                                proximo_id_populacao=proximo_id_populacao)
                            proximo_id_populacao = resultado_mip.get(
                                "_proximo_id_populacao_lote", proximo_id_populacao)
                            resultado_cenario["tempos_estagios"]["materializacao_sqlite_mip"] = (
                                time.perf_counter() - _marca)
                            _marca = time.perf_counter()
                            simulacao.calcular_mip_para_cenario(
                                conn, configuracao, resultado_cenario, sufixo, cenario_id=cenario["id"])
                            resultado_cenario["tempos_estagios"]["mip_continuo"] = time.perf_counter() - _marca
                        conn.execute(
                            "UPDATE simulacao_cenarios SET status = 'Gerado', "
                            "gerado_em = datetime('now', 'localtime') WHERE id = ?",
                            (cenario["id"],))
                        # Ainda DENTRO do try do cenário de propósito (mudou
                        # de lugar — antes ficava fora do try/except): se um
                        # lock persistente sobrar mesmo depois das tentativas
                        # de _commitar_com_retry, cai no except abaixo e é
                        # tratado como falha DESTE cenário — não uma falha
                        # inesperada do lote inteiro (ver `falhou`, docstring
                        # da classe _ThreadGerarLote).
                        _commitar_com_retry(conn)
                        for etapa, segundos in resultado_cenario["tempos_estagios"].items():
                            tempos_totais[etapa] = tempos_totais.get(etapa, 0.0) + segundos
                        # Linha de diagnóstico POR CENÁRIO (não só a soma do
                        # lote inteiro, ver tempos_totais acima) — hipótese
                        # em teste: "ipc_retorno" alto pode não ser
                        # serialização de verdade, e sim o result-handler
                        # interno do ProcessPoolExecutor (roda numa thread
                        # do PRÓPRIO processo principal) faminto de GIL
                        # enquanto ESTA thread (_ThreadGerarLote) está funda
                        # em código Python puro fazendo o executemany de
                        # "gravacao_final" de OUTRO cenário — nesse caso
                        # ipc_retorno de um cenário deveria correlacionar
                        # com gravacao_final do cenário anterior, não com o
                        # tamanho do payload dele mesmo. Reaproveita
                        # tempos_construtores (mesmo destino: o log, ver
                        # _gravar_log_construtores) em vez de abrir mais um
                        # campo novo pra passar por concluido.emit.
                        tempos_construtores.append(
                            f"[{cenario['nome']}] tempos: despacho="
                            f"{resultado_cenario['tempos_estagios'].get('fila_despacho', 0.0):.2f}s "
                            f"retorno_manifesto={resultado_cenario['tempos_estagios'].get('ipc_retorno', 0.0):.2f}s "
                            f"parquet_worker={resultado_cenario['tempos_estagios'].get('gravacao_parquet_worker', 0.0):.2f}s "
                            f"blob_projeto={resultado_cenario['tempos_estagios'].get('gravacao_blob_parquet', 0.0):.2f}s")
                        tempos_construtores.extend(
                            f"[{cenario['nome']}] {linha}"
                            for linha in resultado_cenario.get("resumo_construtores", {}).get("tempos", []))
                        gerados += 1
                    except Exception as e:
                        conn.rollback()
                        _marcar_erro_cenario_com_retry(conn, cenario["id"], e)
                        com_erro.append((cenario["nome"], str(e)))
                    self.progresso.emit(gerados + len(com_erro), total, cenario["nome"])
                    if delta_estimado_por_cenario is None:
                        tamanho_agora = Path(self._caminho_trabalho).stat().st_size
                        delta_estimado_por_cenario = max(0, tamanho_agora - tamanho_antes_lote)

        return gerados, com_erro, tempos_totais, tempos_construtores


class _ThreadExportarCenariosBanco(QThread):
    """Empilha `simulacao_lote_populacao` (todos os cenários "Gerado",
    filtrados por cenario_id, ver core/simulacao.py:persistir_cenario_no_
    lote) numa ÚNICA tabela (NOME_TABELA_EXPORTACAO_CENARIOS), gravada num
    arquivo .sqlite NOVO (`self._caminho_destino`) — mesma ideia de
    exportar_todos_cenarios (Excel: colunas de metadados "cenario"/idade/
    intensidade de manejo na frente de cada linha, ver COLUNAS_METADADOS_
    CENARIO), mas sem o limite de linhas por aba: pensada pro caso de
    dezenas de milhões de linhas juntando todos os cenários, onde nem faz
    sentido tentar caber num .xlsx paginado.

    Lê/grava em lotes de `_TAMANHO_LOTE_EXPORTACAO_BANCO` linhas por vez
    (nunca a tabela inteira em memória de uma vez) e commita no destino a
    cada cenário — resiliente a interrupção (o que já foi exportado fica
    salvo no arquivo de destino mesmo se um cenário no meio falhar ou o
    processo for encerrado).

    Esquema de colunas do destino: todo cenário do lote COMPARTILHA o
    mesmo schema em `simulacao_lote_populacao` (ver _garantir_tabelas_
    lote) — ao contrário das antigas tabelas sufixadas (uma por cenário,
    podendo divergir entre si), não precisa mais de uma 1ª passada
    varrendo cenário por cenário só pra descobrir a união de colunas: 1
    `PRAGMA table_info` já basta. Coluna de população cujo nome colide com
    uma coluna de metadados (ex: um "cenario" que por acaso também fosse
    nome de coluna da Base IFC), ou a própria "cenario_id", é descartada —
    metadados sempre vencem, mesmo critério de _inserir_metadados_cenario
    (exportar_todos_cenarios, Excel); "cenario_id" nunca deveria vazar pro
    arquivo exportado (mesmo cuidado de core/simulacao.py:ativar_cenario)."""

    progresso = Signal(int, int, str)
    concluido = Signal(dict)
    falhou = Signal(object)

    def __init__(self, caminho_trabalho, caminho_destino, cenarios, parent=None):
        super().__init__(parent)
        self._caminho_trabalho = caminho_trabalho
        self._caminho_destino = caminho_destino
        self._cenarios = cenarios

    def run(self):
        try:
            conn_origem = conectar_caminho(self._caminho_trabalho)
            conn_destino = conectar_caminho(self._caminho_destino)
            try:
                total = len(self._cenarios)
                cenarios_sem_dado = []
                nomes_metadados = {nome for nome, _tipo in simulacao.COLUNAS_METADADOS_CENARIO}

                existe_tabela_lote = conn_origem.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (simulacao.TABELA_LOTE_POPULACAO,)
                ).fetchone()
                colunas_populacao_uniao = [
                    (linha[1], linha[2] or "REAL")
                    for linha in (
                        conn_origem.execute(f'PRAGMA table_info("{simulacao.TABELA_LOTE_POPULACAO}")')
                        if existe_tabela_lote is not None else [])
                    if linha[1] not in nomes_metadados and linha[1] != "cenario_id"
                ]
                # Formato atual: o schema vem do primeiro Parquet gerado.
                # Mantém o ramo SQLite acima como fallback para projetos
                # antigos que ainda não foram regenerados.
                primeiro_parquet = (
                    conn_origem.execute(
                        f'SELECT cenario_id FROM "{simulacao.TABELA_CENARIOS_PARQUET}" '
                        "ORDER BY cenario_id LIMIT 1"
                    ).fetchone()
                    if simulacao._existe_tabela_cenarios_parquet(conn_origem) else None)
                if primeiro_parquet is not None:
                    df_schema = simulacao.carregar_populacao_cenario_parquet(
                        conn_origem, primeiro_parquet[0])
                    def _tipo_sql_serie(serie):
                        if pd.api.types.is_integer_dtype(serie.dtype):
                            return "INTEGER"
                        if pd.api.types.is_numeric_dtype(serie.dtype):
                            return "REAL"
                        return "TEXT"
                    colunas_populacao_uniao = [
                        (nome, _tipo_sql_serie(df_schema[nome]))
                        for nome in df_schema.columns
                        if nome not in nomes_metadados and nome != "cenario_id"
                    ]

                nome_tabela_destino = NOME_TABELA_EXPORTACAO_CENARIOS
                colunas_destino = [nome for nome, _tipo in simulacao.COLUNAS_METADADOS_CENARIO] + [
                    nome for nome, _tipo in colunas_populacao_uniao]
                colunas_destino_sql = ", ".join(
                    f'"{nome}" {tipo}' for nome, tipo in simulacao.COLUNAS_METADADOS_CENARIO
                ) + "".join(f', "{nome}" {tipo}' for nome, tipo in colunas_populacao_uniao)
                conn_destino.execute(f'DROP TABLE IF EXISTS "{nome_tabela_destino}"')
                conn_destino.execute(f'CREATE TABLE "{nome_tabela_destino}" ({colunas_destino_sql})')
                marcadores = ", ".join("?" for _ in colunas_destino)
                colunas_destino_insert_sql = ", ".join(f'"{c}"' for c in colunas_destino)
                sql_insert = (
                    f'INSERT INTO "{nome_tabela_destino}" ({colunas_destino_insert_sql}) '
                    f'VALUES ({marcadores})'
                )
                colunas_origem_sql = ", ".join(f'"{n}"' for n, _t in colunas_populacao_uniao)

                total_linhas = 0
                exportados = 0
                for numero, (cenario_id, nome, *valores_manejo) in enumerate(self._cenarios, start=1):
                    df_parquet = simulacao.carregar_populacao_cenario_parquet(
                        conn_origem, cenario_id)
                    if df_parquet is not None:
                        metadados = (nome,) + tuple(valores_manejo)
                        nomes_origem = [nome_col for nome_col, _tipo in colunas_populacao_uniao]
                        df_parquet = df_parquet.reindex(columns=nomes_origem)
                        for inicio in range(0, len(df_parquet), _TAMANHO_LOTE_EXPORTACAO_BANCO):
                            bloco = df_parquet.iloc[inicio:inicio + _TAMANHO_LOTE_EXPORTACAO_BANCO]
                            linhas_destino = [
                                metadados + tuple(
                                    None if pd.isna(valor) else valor.item()
                                    if hasattr(valor, "item") else valor
                                    for valor in linha)
                                for linha in bloco.itertuples(index=False, name=None)
                            ]
                            conn_destino.executemany(sql_insert, linhas_destino)
                            total_linhas += len(linhas_destino)
                        conn_destino.commit()
                        exportados += 1
                        self.progresso.emit(numero, total, nome)
                        continue
                    tem_dado = existe_tabela_lote is not None and conn_origem.execute(
                        f'SELECT 1 FROM "{simulacao.TABELA_LOTE_POPULACAO}" WHERE cenario_id = ? LIMIT 1',
                        (cenario_id,)
                    ).fetchone() is not None
                    if not tem_dado:
                        cenarios_sem_dado.append(nome)
                        self.progresso.emit(numero, total, nome)
                        continue
                    metadados = (nome,) + tuple(valores_manejo)
                    cursor = conn_origem.execute(
                        f'SELECT {colunas_origem_sql} FROM "{simulacao.TABELA_LOTE_POPULACAO}" '
                        "WHERE cenario_id = ?", (cenario_id,))
                    while True:
                        linhas = cursor.fetchmany(_TAMANHO_LOTE_EXPORTACAO_BANCO)
                        if not linhas:
                            break
                        linhas_destino = [metadados + linha for linha in linhas]
                        conn_destino.executemany(sql_insert, linhas_destino)
                        total_linhas += len(linhas)
                    conn_destino.commit()
                    exportados += 1
                    self.progresso.emit(numero, total, nome)

                self.concluido.emit({
                    "exportados": exportados, "total": total, "total_linhas": total_linhas,
                    "cenarios_sem_dado": cenarios_sem_dado, "caminho": self._caminho_destino,
                })
            finally:
                conn_origem.close()
                conn_destino.close()
        except Exception as e:
            self.falhou.emit(e)


def _gravar_log_construtores(tempos_construtores, perfil_caso=None, resumo_tempos=None):
    """Grava o detalhe por construtor (linhas de `tempos_construtores`,
    ver _ThreadGerarLote.run) num arquivo de log ao lado do projeto —
    diagnóstico de performance útil pra quem for investigar um lote
    lento, mas verboso demais pro diálogo de "Simulação" no final de
    "Gerar pendentes"/"Reiniciar" (que só mostra o resumo por etapa, ver
    TelaSimulacao._finalizar_geracao_lote). Acrescenta (não sobrescreve)
    um bloco com timestamp a cada lote, pra manter histórico entre
    rodadas. Sem projeto aberto (não deveria acontecer aqui, mas por
    segurança) cai pra %TEMP%. Devolve o Path gravado, ou None se a
    escrita falhar (erro de disco não deve travar o diálogo de
    conclusão).

    `perfil_caso`/`resumo_tempos` (opcionais): o mesmo perfil do caso e a
    mesma lista de linhas de tempo por etapa (já com a média por cenário)
    mostrados no diálogo de conclusão (ver _finalizar_geracao_lote) —
    escritos aqui também pra o log ser um artefato completo, colável
    inteiro pra diagnóstico, mesmo quando nenhum construtor individual
    passou de 1s num cenário (`tempos_construtores` vazio)."""
    caminho_proj = projeto.caminho_projeto()
    pasta = caminho_proj.parent if caminho_proj is not None else Path(tempfile.gettempdir())
    nome_base = caminho_proj.stem if caminho_proj is not None else "khaya_planner"
    caminho_log = pasta / f"{nome_base}_construtores.log"
    try:
        with open(caminho_log, "a", encoding="utf-8") as arquivo:
            arquivo.write(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
            if perfil_caso:
                arquivo.write(f"Perfil do caso: {perfil_caso}\n")
            if resumo_tempos:
                arquivo.write("Tempos por etapa (soma / média por cenário):\n")
                arquivo.write("\n".join(resumo_tempos))
                arquivo.write("\n")
            arquivo.write("\n".join(tempos_construtores))
            arquivo.write("\n\n")
        return caminho_log
    except OSError:
        return None


# Aviso do painel "Grade automática de cenários" (ver
# TelaSimulacao._gerar_grade_cenarios). Não há limite máximo: grades
# grandes continuam permitidas, mas exigem confirmação do usuário.
_LIMITE_AVISO_GRADE_CENARIOS = 200

# Ordem cronológica fixa dos manejos — usada por _preencher_idades_puladas
# (ver TelaSimulacao._gerar_grade_cenarios) pra garantir Raleio < 1º
# Desbaste < 2º Desbaste < Corte Raso em toda combinação da grade, mesmo
# quando um manejo foi "pulado" (idade em branco no painel, ver
# TelaSimulacao._ler_evento_grade_opcional).
_ORDEM_EVENTOS_MANEJO = ("raleio", "desbaste_1", "desbaste_2", "corte_raso")


def _preencher_idades_puladas(idades):
    """`idades`: dict evento -> idade (int) ou None (manejo pulado, ver
    _ler_evento_grade_opcional) — "corte_raso" nunca é None (sempre
    obrigatório no painel "Grade automática"). Devolve um dict novo com
    toda idade None preenchida por um valor sintético (só serve pra manter
    a ordem — a intensidade 0% do manejo pulado já garante que ele não
    remove nada de verdade) OU None se não tiver como manter Raleio < 1º
    Desbaste < 2º Desbaste < Corte Raso nessa combinação (ex: dois manejos
    definidos colados demais, sem espaço pra encaixar um pulado entre
    eles, ou idade sintética que precisaria ser < 1).

    Varre de trás pra frente (Corte Raso primeiro) guardando `teto` — a
    idade já resolvida do próximo manejo à direita: um manejo pulado vira
    `teto - 1` (encostado imediatamente abaixo do vizinho), um manejo com
    idade definida só precisa continuar menor que `teto`."""
    resultado = dict(idades)
    teto = None
    for evento in reversed(_ORDEM_EVENTOS_MANEJO):
        valor = resultado[evento]
        if valor is None:
            if teto is None or teto <= 1:
                return None
            valor = teto - 1
            resultado[evento] = valor
        if teto is not None and valor >= teto:
            return None
        teto = valor
    return resultado

# Guarda de espaço em disco do lote de "Múltiplos cenários"/"Grade
# automática" (ver _ThreadGerarLote.run) — cada cenário grava sua própria
# tabela larga no arquivo de trabalho (%TEMP%\khaya_planner_v2), sem
# limite nenhum; num lote grande (centenas/milhares) isso pode encher o
# disco de verdade, e sem essa guarda o SQLite abortava no meio de um
# cenário com "database or disk is full" (erro cru do driver, sem
# contexto, deixando o lote parado num estado confuso). Em vez de tentar
# prever o tamanho de cada cenário de antemão (varia por projeto — nº de
# talhões, construtores ativos, colunas por classe), mede o crescimento
# real do arquivo no 1º cenário do lote e usa isso como estimativa pros
# seguintes, com folga.
_MARGEM_SEGURANCA_DISCO_LOTE = 3
# Piso absoluto antes mesmo do 1º cenário — cobre já começar com o disco
# praticamente cheio, quando ainda não há nenhuma medição própria deste
# lote pra estimar o tamanho por cenário.
_ESPACO_MINIMO_ABSOLUTO_LOTE = 200 * 1024 * 1024

# Limite de linhas por aba de um .xlsx (contando o cabeçalho) — usado por
# TelaSimulacao.exportar_todos_cenarios pra paginar em mais de uma aba sem
# nunca dividir um cenário no meio (ver _particionar_paginas_excel).
LIMITE_LINHAS_EXCEL = 1_048_576

# engine="xlsxwriter" em vez do padrão do pandas pra .xlsx (openpyxl) —
# testado ~15-20% mais rápido em planilhas grandes (200 mil linhas x 15
# colunas: ~44s -> ~38s), sem mudar o arquivo gerado. NÃO usar a opção
# "constant_memory" do xlsxwriter (parecia dar 2x, mas CORROMPE dados —
# confirmado testando: célula de texto vira NaN silenciosamente às
# vezes; nada no traceback acusa o problema, só comparando o arquivo
# gerado com os dados originais).
_ENGINE_KWARGS_EXCEL = {"engine": "xlsxwriter"}

# Campos de idade/intensidade de manejo de um cenário (colunas de
# simulacao_cenarios, mesmos nomes de chave em `configuracao` — ver
# _ler_valores_evento/_ThreadGerarLote.run) — usados tanto por
# _gerar_uma_simulacao (monta `metadados_cenario` pra
# core/simulacao.py:calcular_volume_por_sortimento) quanto por
# exportar_todos_cenarios (injeta os mesmos valores direto de
# simulacao_cenarios na hora de exportar). Mesma ordem/nomes de
# core/simulacao.py:COLUNAS_METADADOS_CENARIO, exceto "cenario" (nome do
# cenário em si, tratado à parte em cada lugar que usa esta lista).
_CAMPOS_MANEJO_CENARIO = (
    "idade_raleio", "intensidade_raleio", "idade_desbaste_1", "intensidade_desbaste_1",
    "idade_desbaste_2", "intensidade_desbaste_2", "idade_corte_raso",
)

# Nome da tabela única gravada por exportar_todos_cenarios_banco (ver
# _ThreadExportarCenariosBanco) no arquivo .sqlite de destino — mesma
# ideia de exportar_todos_cenarios (Excel), mas sem o limite de linhas
# por aba: pensada pra quando o total de linhas empilhadas (todos os
# cenários "Gerado" juntos) passa dos milhões, onde um .xlsx paginado em
# dezenas de abas deixa de ser prático.
NOME_TABELA_EXPORTACAO_CENARIOS = "simulacao_todos_cenarios"

# Linhas por lote de INSERT em _ThreadExportarCenariosBanco — lê/grava em
# pedaços em vez de carregar a tabela de um cenário inteira em memória de
# uma vez (um cenário sozinho pode ter centenas de milhares de linhas).
_TAMANHO_LOTE_EXPORTACAO_BANCO = 5000


def _particionar_paginas_excel(blocos, limite_linhas=LIMITE_LINHAS_EXCEL):
    """`blocos`: lista de (nome_cenario, DataFrame) — agrupa em "páginas"
    (cada uma uma lista de DataFrame, futura aba do Excel) cabendo dentro
    de `limite_linhas` (cabeçalho + linhas de dados), NUNCA dividindo um
    cenário entre duas páginas: o próximo cenário só entra na página
    atual se ainda couber inteiro, senão abre página nova. Um cenário
    sozinho maior que `limite_linhas` não caberia em nenhuma aba sem ser
    dividido — como isso é proibido, fica de fora (nome devolvido em
    `excedidos`, pra quem chamar avisar o usuário) em vez de estourar o
    limite físico do .xlsx. Devolve (paginas, excedidos)."""
    paginas = []
    excedidos = []
    pagina_atual = []
    linhas_atual = 1  # cabeçalho
    for nome_cenario, df in blocos:
        n = len(df)
        if n + 1 > limite_linhas:
            excedidos.append(nome_cenario)
            continue
        if pagina_atual and linhas_atual + n > limite_linhas:
            paginas.append(pagina_atual)
            pagina_atual = []
            linhas_atual = 1
        pagina_atual.append(df)
        linhas_atual += n
    if pagina_atual:
        paginas.append(pagina_atual)
    return paginas, excedidos


def _escrever_paginas_excel(writer, paginas, nome_base):
    """Escreve cada página (lista de DataFrame, ver _particionar_paginas_excel)
    numa aba própria — nome da aba é só `nome_base` se houver 1 página só
    (comportamento de sempre), ou "{nome_base} {n}" (1-based) se precisou
    paginar; truncado em 31 caracteres (limite de nome de aba do .xlsx)."""
    for i, dfs in enumerate(paginas):
        nome_aba = nome_base if len(paginas) == 1 else f"{nome_base} {i + 1}"
        pd.concat(dfs, ignore_index=True).to_excel(writer, sheet_name=nome_aba[:31], index=False)


class TelaSimulacao(TelaBase):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._mapa_intensidade_raleio = {}
        self._mapa_intensidade_desbaste_1 = {}
        self._mapa_intensidade_desbaste_2 = {}
        self._opcoes_grafico_coluna = {"": (None, False)}
        self._opcoes_kpi_coluna = {}
        self._opcoes_tabela_pivo_coluna = {"": None}
        self._opcoes_grafico_classe_coluna = {"": None}
        self._gerando = False
        self._gerando_lote = False
        self._thread_gerar = None
        self._thread_gerar_lote = None
        self._thread_exportar_cenarios_banco = None
        # Aviso "cenários de uma versão anterior" (ver _carregar_cenarios)
        # — só uma vez por instância desta tela, não a cada refresh da
        # grade (recarregar_lista/_carregar_cenarios rodam com frequência:
        # depois de gerar, ativar, excluir etc.).
        self._aviso_cenarios_formato_antigo_mostrado = False

        self._montar_form()
        self.recarregar_lista()

    # ---------------- formulário ----------------

    def _montar_form(self):
        # QScrollArea em vez de só um QWidget — mesmo motivo de
        # app/screens/modelos.py: sem isso, uma janela menor que o
        # formulário inteiro corta os últimos campos sem nenhum jeito de
        # rolar até eles.
        layout_raiz = QVBoxLayout(self)
        layout_raiz.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # O formulário é montado para se adaptar à largura disponível;
        # mantenha somente a rolagem vertical. Isso também evita que a
        # largura reservada pela barra horizontal faça o conteúdo oscilar
        # ao redimensionar a janela.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout_raiz.addWidget(scroll)

        conteudo = QWidget()
        layout = QVBoxLayout(conteudo)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        scroll.setWidget(conteudo)

        cartao_classes = Cartao("Classes diamétricas e manejo")
        layout.addWidget(cartao_classes)
        self._montar_secao_classes_manejo(cartao_classes.corpo)

        # Uma única coluna mantém todos os cartões dentro da área útil da
        # tela, inclusive em janelas menores e com a barra lateral aberta.
        # Os formulários internos também usam no máximo dois pares por
        # linha (ver as seções abaixo), portanto não impõem largura mínima
        # maior que a viewport.
        self._grid = QGridLayout()
        self._grid.setColumnStretch(0, 1)
        layout.addLayout(self._grid)

        # Rowspan 2 (não só a linha 0) — sobra espaço embaixo de "Eventos
        # de manejo" (mais curto) na coluna ao lado, em vez de forçar a
        # linha 0 sozinha a esticar até a altura deste cartão (que cresceu:
        # agora também tem forma/escala da distribuição + a tabela de
        # agregação opcional). Guardado em self —
        # _ao_alternar_multiplos_cenarios encolhe o rowspan de volta pra 1
        # enquanto "Cenários" precisa da linha 1.
        self.cartao_colunas = Cartao("Parâmetros da Simulação")
        self._grid.addWidget(self.cartao_colunas, 0, 0)
        self._montar_secao_colunas_ifc(self.cartao_colunas.corpo)

        # Guardado em self (não só local) — _ao_alternar_multiplos_cenarios
        # move o cartão de Colunas entre rowspan 1/2, nunca este.
        self.cartao_eventos = Cartao("Eventos de manejo")
        self._grid.addWidget(self.cartao_eventos, 1, 0)
        self._montar_secao_eventos(self.cartao_eventos.corpo)

        # Montado aqui mas só colocado no grid (aparece embaixo de
        # "Colunas da Base IFC", ao lado de "Eventos de manejo") quando o
        # checkbox "Múltiplos cenários" é marcado — ver
        # _ao_alternar_multiplos_cenarios.
        self.cartao_cenarios = Cartao("Cenários (múltiplos)")
        self._montar_secao_cenarios(self.cartao_cenarios.corpo)
        self.cartao_cenarios.setVisible(False)

        # Resultados em janela própria: a tela principal fica dedicada à
        # configuração/execução. Abre pelo botão "Gráficos" ou por duplo
        # clique num cenário gerado.
        self.dialogo_graficos = QDialog(self)
        self.dialogo_graficos.setWindowTitle("Gráficos de resultados")
        self.dialogo_graficos.resize(1100, 720)
        self._montar_secao_grafico(self.dialogo_graficos)

        self._montar_secao_executar(layout, layout_raiz)

        layout.addStretch(1)
        self._layout_simulacao_lado_a_lado = None
        self._atualizar_layout_responsivo()

    def _montar_secao_classes_manejo(self, container):
        """Parâmetros globais usados diretamente na geração da simulação."""
        layout = QGridLayout(container)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(6)
        layout.setColumnStretch(1, 1)

        self.entry_primeira_classe = QLineEdit()
        self.entry_ultima_classe = QLineEdit()
        self.entry_idade_maxima_manejo = QLineEdit()
        self.entry_numero_minimo_arvores = QLineEdit()
        campos = (
            ("Primeira classe diamétrica", self.entry_primeira_classe),
            ("Última classe diamétrica", self.entry_ultima_classe),
            ("Idade máxima de manejo", self.entry_idade_maxima_manejo),
            ("Mínimo de árvores/ha", self.entry_numero_minimo_arvores),
        )
        for indice, (rotulo, campo) in enumerate(campos):
            layout.addWidget(QLabel(rotulo), indice, 0)
            layout.addWidget(campo, indice, 1)

        self.combo_normalizacao_weibull = QComboBox()
        self.combo_normalizacao_weibull.addItem("Aditiva (padrão)", "aditiva")
        self.combo_normalizacao_weibull.addItem("Proporcional", "proporcional")
        layout.addWidget(QLabel("Normalização da Weibull"), 4, 0)
        layout.addWidget(self.combo_normalizacao_weibull, 4, 1)

        self.combo_base_ajuste_logistico = QComboBox()
        self.combo_base_ajuste_logistico.addItem("1/IP (padrão)", "ip")
        self.combo_base_ajuste_logistico.addItem("1/IPM", "ipm")
        layout.addWidget(QLabel("Base do ajuste logístico"), 5, 0)
        layout.addWidget(self.combo_base_ajuste_logistico, 5, 1)

        self.combo_base_calculo_mip = QComboBox()
        self.combo_base_calculo_mip.addItem("Densidade — fdp (padrão)", "fdp")
        self.combo_base_calculo_mip.addItem("Probabilidade por classe", "classe")
        layout.addWidget(QLabel("Grandeza do MIP"), 6, 0)
        layout.addWidget(self.combo_base_calculo_mip, 6, 1)

        self.checkbox_ajuste_manejo = QCheckBox(
            "Ajustar Raleio/1º/2º Desbaste quando o ano-calendário já passou")
        layout.addWidget(self.checkbox_ajuste_manejo, 7, 0, 1, 2)

        barra_acoes = QHBoxLayout()
        barra_acoes.addStretch(1)
        self.botao_salvar_classes_manejo = QPushButton("Salvar parâmetros")
        qss.aplicar_variante(self.botao_salvar_classes_manejo, "salvar")
        icones.aplicar_icone(self.botao_salvar_classes_manejo, "salvar", cor="white")
        self.botao_salvar_classes_manejo.clicked.connect(self._salvar_classes_manejo)
        barra_acoes.addWidget(self.botao_salvar_classes_manejo)
        layout.addLayout(barra_acoes, 8, 0, 1, 2)

    def _montar_secao_colunas_ifc(self, container):
        # Grid de 2 pares (rótulo, combo) por linha — 12 campos (9 colunas
        # da Base IFC ByTalhao + forma/escala da distribuição), preenchido
        # na mesma ordem de sempre (esquerda-direita, cima-baixo). Linhas 6+:
        # checkbox + tabela de agregação (Volume por sortimento) — largura
        # cheia (6 colunas), dentro deste mesmo cartão ("Parâmetros da
        # Simulação"), não mais num cartão "Distribuição diamétrica" à
        # parte.
        layout = QGridLayout(container)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

        layout.addWidget(QLabel("Coluna de talhão"), 0, 0)
        self.combo_coluna_talhao = QComboBox()
        layout.addWidget(self.combo_coluna_talhao, 0, 1)
        self.combo_coluna_talhao.textActivated.connect(self._ao_mudar_selecao_coluna)

        layout.addWidget(QLabel("Coluna de fustes observados"), 0, 2)
        self.combo_coluna_fustes_observados = QComboBox()
        layout.addWidget(self.combo_coluna_fustes_observados, 0, 3)
        self.combo_coluna_fustes_observados.textActivated.connect(self._ao_mudar_selecao_coluna)

        # DAP médio/máximo/mínimo observados são opcionais (diferente de
        # talhão/fustes, obrigatórios) — sem eles, dap_*_atual continua
        # vazio antes do primeiro manejo, como sempre foi; combo aceita
        # "" (primeira opção) pra representar "não mapeado".
        self.combo_coluna_dap_med_observado = self._linha_coluna_opcional(
            layout, 1, "DAP médio observado")
        self.combo_coluna_dap_max_observado = self._linha_coluna_opcional(
            layout, 1, "DAP máximo observado", coluna=2)
        self.combo_coluna_dap_min_observado = self._linha_coluna_opcional(
            layout, 2, "DAP mínimo observado")

        # Ht médio observado: mesmo papel de baseline que DAP médio tem
        # acima (ht_atual troca por substituição direta a cada etapa, ver
        # core/simulacao.py:gerar_populacao).
        self.combo_coluna_ht_observado = self._linha_coluna_opcional(
            layout, 2, "Ht observado", coluna=2)

        # VTCC observado: baseline de volume/ha — diferente do Ht acima,
        # vtcc_atual SUBTRAI o volume removido a cada etapa (mesmo
        # tratamento acumulado de fustes_atual), não substitui direto.
        self.combo_coluna_vtcc_observado = self._linha_coluna_opcional(layout, 3, "VTCC observado")

        # Data de plantio: alimenta ano_simulado (ano do plantio +
        # idade_simulada de cada linha, ver gerar_populacao) — o
        # ano-calendário em que o talhão tinha aquela idade simulada.
        self.combo_coluna_data_plantio = self._linha_coluna_opcional(
            layout, 3, "Data de plantio", coluna=2)

        # Data de medição: cadastrada/lembrada junto da data de plantio
        # (par natural na Base IFC ByTalhao), mas ainda não entra em
        # nenhuma conta de gerar_populacao.
        self.combo_coluna_data_medicao = self._linha_coluna_opcional(
            layout, 4, "Data de medição")

        # CV do DAP observado: mesmo papel de baseline que Ht observado tem
        # acima (cv_dap_atual troca por substituição direta a cada etapa) —
        # diferente das outras colunas observadas, normalmente não vem
        # pronta na Base IFC ByTalhao, precisa ter sido calculada por fora
        # e importada como coluna própria antes de aparecer aqui.
        self.combo_coluna_cv_dap_observado = self._linha_coluna_opcional(
            layout, 4, "CV do DAP observado", coluna=2)

        # Forma/escala usadas na distribuição diamétrica: em branco usa
        # forma_atual/escala_atual (calculadas pelo pipeline Weibull, como
        # sempre foi); apontando pra outra coluna de simulacao_talhao_idade
        # (ex: uma gerada no Construtor de Variáveis), a distribuição passa a
        # usar essa em vez da calculada aqui — ver
        # core/simulacao.py:calcular_distribuicao_diametrica, chamada de novo
        # em self.gerar() depois de reaplicar os construtores salvos.
        self.combo_coluna_forma_distribuicao = self._linha_coluna_opcional(
            layout, 5, "Coluna de forma")
        self.combo_coluna_escala_distribuicao = self._linha_coluna_opcional(
            layout, 5, "Coluna de escala", coluna=2)

        # Tabela de agregação (Volume por sortimento) — escondida (e a
        # etapa correspondente pulada em self.gerar(), mesmo com linhas já
        # marcadas de uma sessão anterior) enquanto este checkbox estiver
        # desligado; ver _ao_alternar_tabela_agregacao/
        # _colunas_volume_classes_marcadas/_tipos_agregacao_volume_marcadas.
        # Estado persistido (obter/salvar_usar_tabela_agregacao_volume),
        # igual as colunas/agregação escolhidas dentro da tabela.
        self.checkbox_tabela_agregacao = QCheckBox("Tabela de agregação")
        self.checkbox_tabela_agregacao.setToolTip(
            "Liga a etapa de volume por sortimento (soma/média das colunas por classe "
            "diamétrica dentro de cada sortimento cadastrado em Configurações) — desligado, a "
            "tabela abaixo fica escondida e essa etapa é pulada em \"Gerar simulação\", mesmo "
            "que já tenha campo(s) marcado(s) nela.")
        self.checkbox_tabela_agregacao.toggled.connect(self._ao_alternar_tabela_agregacao)
        layout.addWidget(self.checkbox_tabela_agregacao, 6, 0, 1, 4)

        # Volume por sortimento: quais variáveis por classe diamétrica (ex:
        # "vtcc" pras colunas "vtcc_5"/"vtcc_7"/... geradas por um Modelo
        # ligado no nó Classe Diamétrica do Construtor de Variáveis)
        # representam volume, e como agregar as classes de cada sortimento
        # (cadastrados em Configurações) — soma ou média, escolhida por
        # LINHA (independente por variável, ex: soma pro volume total,
        # média pro volume de biomassa, ver
        # core/simulacao.py:calcular_volume_por_sortimento). Com 1 campo
        # marcado, a coluna de saída de cada sortimento sai com o nome cru
        # do sortimento (comportamento de sempre, ex: "0-10"); com 2+
        # marcados, cada uma vem prefixada com o campo pra não colidir na
        # mesma tabela (ex: "vtcc_0-10", "biomassa_0-10"). Nada marcado, a
        # etapa é pulada (nenhuma simulacao_volume_sortimento é gerada) —
        # chamada em self.gerar() depois de reaplicar os construtores
        # salvos (é quando essas colunas por classe existem).
        self.tabela_volume_classes = QTableWidget(0, 2)
        self.tabela_volume_classes.setHorizontalHeaderLabels(["Variável", "Agregação"])
        self.tabela_volume_classes.verticalHeader().setVisible(False)
        self.tabela_volume_classes.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.tabela_volume_classes.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela_volume_classes.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tabela_volume_classes.setMaximumHeight(90)
        self.tabela_volume_classes.setToolTip(
            "Marque o(s) campo(s) de volume por classe — cada um agregado separadamente por "
            "sortimento na mesma tabela (ex: volume total e volume de biomassa lado a lado), "
            "com a agregação (Soma/Média) escolhida por linha, independente entre campos. Com "
            "2+ marcados, cada coluna de sortimento vem prefixada com o nome do campo.")
        self.tabela_volume_classes.itemChanged.connect(self._ao_mudar_selecao_coluna)
        layout.addWidget(emoldurar_tabela(self.tabela_volume_classes), 7, 0, 1, 4)
        self.tabela_volume_classes.setVisible(False)

    def _ao_alternar_tabela_agregacao(self, marcado):
        self.tabela_volume_classes.setVisible(marcado)
        self._ao_mudar_selecao_coluna()

    def _montar_secao_eventos(self, container):
        layout = QGridLayout(container)
        layout.setColumnStretch(1, 1)

        # Sempre visíveis nos dois modos: geração única usa esses valores
        # direto (botão "Gerar simulação"); "Múltiplos cenários" marcado
        # usa os MESMOS campos como formulário de entrada pro grid que
        # aparece no Cartão "Cenários" — "Adicionar cenário" lê o que
        # estiver preenchido aqui (ver _ler_valores_evento/_adicionar_cenario).
        self.checkbox_multiplos_cenarios = QCheckBox(
            "Múltiplos cenários")
        self.checkbox_multiplos_cenarios.setToolTip(
            "Gere e compare várias combinações de idades e intensidades de manejo.")
        self.checkbox_multiplos_cenarios.toggled.connect(self._ao_alternar_multiplos_cenarios)
        layout.addWidget(self.checkbox_multiplos_cenarios, 0, 0, 1, 3)

        # Segundo modo de popular "Cenários (múltiplos)" — em vez de montar
        # cada linha à mão (checkbox acima), o usuário dá uma faixa de
        # idade (mín/máx/passo) e de intensidade (mín/máx, o passo já vem
        # do "Passo de intensidade" configurado — ver _montar_painel_grade_
        # cenarios) por manejo, e "Gerar grade de cenários" cria um cenário
        # pra cada combinação possível (produto cartesiano, ver
        # _gerar_grade_cenarios). Mutuamente exclusivo com "Múltiplos
        # cenários" (ver _ao_alternar_grade_automatica) — os dois só
        # preenchem a mesma tabela simulacao_cenarios por caminhos
        # diferentes; a partir daí (tabela/"Gerar todos os cenários"/
        # ranking) o fluxo é idêntico.
        self.checkbox_grade_automatica = QCheckBox(
            "Grade automática de cenários")
        self.checkbox_grade_automatica.setToolTip(
            "Cada cenário gerado respeita Raleio < 1º Desbaste < 2º Desbaste < Corte Raso — "
            "combinações fora dessa ordem são descartadas. Raleio/1º/2º Desbaste são opcionais: "
            "deixe os 3 campos de idade (mín/máx/passo) daquele manejo em branco pra tratá-lo "
            "como \"não feito\" em toda a grade — a intensidade fica sempre 0%, os combobox de "
            "intensidade dele são ignorados. Corte Raso continua obrigatório.")
        self.checkbox_grade_automatica.toggled.connect(self._ao_alternar_grade_automatica)
        layout.addWidget(self.checkbox_grade_automatica, 1, 0, 1, 3)

        layout.addWidget(QLabel("Idade (anos)"), 2, 1)
        layout.addWidget(QLabel("Intensidade"), 2, 2)

        self.entry_idade_raleio, self.combo_intensidade_raleio = self._linha_evento(layout, 3, "Raleio")
        self.entry_idade_desbaste_1, self.combo_intensidade_desbaste_1 = self._linha_evento(
            layout, 4, "1º Desbaste")
        self.entry_idade_desbaste_2, self.combo_intensidade_desbaste_2 = self._linha_evento(
            layout, 5, "2º Desbaste")
        # Corte Raso não tem intensidade própria (colheita total) — só a
        # idade, sem combobox de intensidade.
        self.entry_idade_corte_raso = self._linha_idade(layout, 6, "Corte Raso")

    def _ao_alternar_multiplos_cenarios(self, marcado):
        if marcado:
            self.checkbox_grade_automatica.blockSignals(True)
            self.checkbox_grade_automatica.setChecked(False)
            self.checkbox_grade_automatica.blockSignals(False)
            self.painel_grade_cenarios.setVisible(False)
        self._atualizar_visibilidade_cenarios()

    def _ao_alternar_grade_automatica(self, marcado):
        if marcado:
            self.checkbox_multiplos_cenarios.blockSignals(True)
            self.checkbox_multiplos_cenarios.setChecked(False)
            self.checkbox_multiplos_cenarios.blockSignals(False)
        self.painel_grade_cenarios.setVisible(marcado)
        self._atualizar_visibilidade_cenarios()

    def _atualizar_visibilidade_cenarios(self):
        # Os cartões permanecem em uma coluna. "Cenários" ocupa a linha
        # reservada entre Eventos e Gráfico quando qualquer um dos modos
        # de múltiplos cenários estiver ligado.
        marcado = self.checkbox_multiplos_cenarios.isChecked() or self.checkbox_grade_automatica.isChecked()
        self.cartao_cenarios.setVisible(marcado)
        self.botao_gerar.setVisible(not marcado)
        self._atualizar_layout_responsivo(forcar=True)

    def resizeEvent(self, evento):
        super().resizeEvent(evento)
        if hasattr(self, "_grid") and hasattr(self, "dialogo_graficos"):
            self._atualizar_layout_responsivo()

    def _atualizar_layout_responsivo(self, forcar=False):
        """Mantém Eventos ao lado de Parâmetros sem reintroduzir overflow.

        Abaixo do limite os cartões voltam a uma coluna; acima dele usam
        duas colunas. Cenários e gráfico continuam sempre na largura toda.
        """
        lado_a_lado = self.width() >= 1120
        if not forcar and lado_a_lado == self._layout_simulacao_lado_a_lado:
            return
        self._layout_simulacao_lado_a_lado = lado_a_lado
        for widget in (
                self.cartao_colunas, self.cartao_eventos, self.cartao_cenarios):
            self._grid.removeWidget(widget)

        if lado_a_lado:
            self._grid.setColumnStretch(0, 2)
            self._grid.setColumnStretch(1, 1)
            self._grid.addWidget(self.cartao_colunas, 0, 0)
            self._grid.addWidget(self.cartao_eventos, 0, 1)
            self._grid.addWidget(self.cartao_cenarios, 1, 0, 1, 2)
        else:
            self._grid.setColumnStretch(0, 1)
            self._grid.setColumnStretch(1, 0)
            self._grid.addWidget(self.cartao_colunas, 0, 0)
            self._grid.addWidget(self.cartao_eventos, 1, 0)
            self._grid.addWidget(self.cartao_cenarios, 2, 0)
        if hasattr(self, "_grid_rankings"):
            self._atualizar_layout_rankings_responsivo(lado_a_lado, forcar=forcar)

    def _atualizar_layout_rankings_responsivo(self, lado_a_lado, forcar=False):
        if not forcar and lado_a_lado == self._layout_rankings_lado_a_lado:
            return
        self._layout_rankings_lado_a_lado = lado_a_lado
        self._grid_rankings.removeWidget(self.painel_ranking_kpi)
        self._grid_rankings.removeWidget(self.painel_ranking_talhao)
        if lado_a_lado:
            self._grid_rankings.setColumnStretch(0, 1)
            self._grid_rankings.setColumnStretch(1, 1)
            self._grid_rankings.addWidget(self.painel_ranking_kpi, 0, 0)
            self._grid_rankings.addWidget(self.painel_ranking_talhao, 0, 1)
        else:
            self._grid_rankings.setColumnStretch(0, 1)
            self._grid_rankings.setColumnStretch(1, 0)
            self._grid_rankings.addWidget(self.painel_ranking_kpi, 0, 0)
            self._grid_rankings.addWidget(self.painel_ranking_talhao, 1, 0)

    def _montar_secao_executar(self, layout, layout_raiz):
        # Prontidão/progresso pertencem ao conteúdo rolável. As ações são
        # montadas num rodapé fixo fora do scroll, seguindo a tela Modelos.
        self.label_prontidao = QLabel("")
        self.label_prontidao.setWordWrap(True)
        layout.addWidget(self.label_prontidao)

        # core/simulacao.py:gerar_populacao não expõe progress_callback —
        # instrumentar isso pra progresso numérico real é mudança de
        # backend; indeterminate aqui é o MVP, só amarrado ao início/fim
        # da thread (mesma ressalva do original).
        self.progressbar = QProgressBar()
        self.progressbar.setRange(0, 0)
        self.progressbar.setVisible(False)
        layout.addWidget(self.progressbar)

        self.label_status = QLabel("")
        layout.addWidget(self.label_status)

        rodape = QWidget()
        linha_botoes = QHBoxLayout(rodape)
        linha_botoes.setContentsMargins(12, 8, 12, 12)
        linha_botoes.addStretch(1)
        self.botao_gerar = QPushButton("Gerar simulação")
        qss.aplicar_variante(self.botao_gerar, "salvar")
        icones.aplicar_icone(self.botao_gerar, "gerar", cor="white")
        self.botao_gerar.clicked.connect(self.gerar)
        linha_botoes.addWidget(self.botao_gerar)
        self.botao_graficos = QPushButton("Gráficos")
        icones.aplicar_icone(self.botao_graficos, "simulacao")
        self.botao_graficos.clicked.connect(self._abrir_janela_graficos)
        linha_botoes.addWidget(self.botao_graficos)
        self.botao_exportar = QPushButton("Exportar para Excel...")
        icones.aplicar_icone(self.botao_exportar, "exportar")
        self.botao_exportar.clicked.connect(self.exportar_excel)
        linha_botoes.addWidget(self.botao_exportar)
        layout_raiz.addWidget(rodape)

    def _abrir_janela_graficos(self, _checked=False):
        self._atualizar_grafico_resultado()
        self._atualizar_tabela_pivo()
        self._atualizar_grafico_classe()
        self.dialogo_graficos.show()
        self.dialogo_graficos.raise_()
        self.dialogo_graficos.activateWindow()

    def _montar_secao_grafico(self, container):
        """"Gráfico de resultados" em três abas (QTabWidget): "Gráfico"
        (curva por evento/sortimento, comportamento de sempre — ver
        _montar_aba_grafico), "Tabela por sortimento" (cruzamento evento x
        sortimento, ver _montar_aba_tabela_pivo) e "Gráfico por classe"
        (classe diamétrica no eixo x, uma curva por evento — ver
        _montar_aba_grafico_classe)."""
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        abas = QTabWidget()
        layout.addWidget(abas)

        aba_grafico = QWidget()
        abas.addTab(aba_grafico, "Gráfico")
        self._montar_aba_grafico(aba_grafico)

        aba_tabela_pivo = QWidget()
        abas.addTab(aba_tabela_pivo, "Tabela por sortimento")
        self._montar_aba_tabela_pivo(aba_tabela_pivo)

        aba_grafico_classe = QWidget()
        abas.addTab(aba_grafico_classe, "Gráfico por classe")
        self._montar_aba_grafico_classe(aba_grafico_classe)

    def _montar_aba_grafico(self, container):
        """Combobox "Coluna" (colunas comuns de simulacao_talhao_idade +
        famílias por classe diamétrica, ver
        simulacao.colunas_grafico_resultado_disponiveis) + "Agregação
        entre talhões" (Soma/Média), redesenhando o gráfico sozinho a cada
        seleção — sem botão "Atualizar" à parte, mesmo UX do resto do app.
        Ver simulacao.dados_grafico_resultado pro que cada modo (coluna
        comum -> 1 curva por evento; família por classe -> 1 curva por
        sortimento cadastrado) significa."""
        layout = QGridLayout(container)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(2, 1)

        layout.addWidget(QLabel("Coluna"), 0, 0)
        self.combo_grafico_coluna = QComboBox()
        layout.addWidget(self.combo_grafico_coluna, 0, 1)
        self.combo_grafico_coluna.textActivated.connect(self._ao_mudar_selecao_grafico)

        layout.addWidget(QLabel("Agregação entre talhões"), 1, 0)
        self.combo_grafico_agregacao = QComboBox()
        self.combo_grafico_agregacao.addItems(["Soma", "Média"])
        layout.addWidget(self.combo_grafico_agregacao, 1, 1)
        self.combo_grafico_agregacao.textActivated.connect(self._ao_mudar_selecao_grafico)

        self.grafico_resultado = GraficoResultadoSimulacao()
        layout.addWidget(self.grafico_resultado, 2, 0, 1, 2)

    def _montar_aba_tabela_pivo(self, container):
        """Tabela cruzada evento de manejo (linha) x sortimento cadastrado
        (coluna) — célula = uma família por classe escolhida pelo usuário
        (ver simulacao.tabela_evento_sortimento), agregada por sortimento
        (soma entre classes) e por evento (soma/média entre talhões, mesma
        "Agregação" da aba Gráfico, mas independente — trocar uma não
        mexe na outra). Só oferece famílias por classe no combobox — uma
        coluna comum não tem dimensão de classe, não dá pra cruzar com
        sortimento (que é definido por faixa de classe diamétrica)."""
        layout = QGridLayout(container)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(3, 1)

        layout.addWidget(QLabel("Coluna (por classe)"), 0, 0)
        self.combo_tabela_pivo_coluna = QComboBox()
        layout.addWidget(self.combo_tabela_pivo_coluna, 0, 1)
        self.combo_tabela_pivo_coluna.textActivated.connect(self._ao_mudar_selecao_tabela_pivo)

        layout.addWidget(QLabel("Agregação entre talhões"), 1, 0)
        self.combo_tabela_pivo_agregacao = QComboBox()
        self.combo_tabela_pivo_agregacao.addItems(["Soma", "Média"])
        layout.addWidget(self.combo_tabela_pivo_agregacao, 1, 1)
        self.combo_tabela_pivo_agregacao.textActivated.connect(self._ao_mudar_selecao_tabela_pivo)

        self.label_status_tabela_pivo = QLabel("Escolha uma coluna pra ver a tabela.")
        qss.aplicar_status(self.label_status_tabela_pivo, "neutro")
        layout.addWidget(self.label_status_tabela_pivo, 2, 0, 1, 2)

        self.tabela_pivo_sortimento = Tabela(colunas=())
        layout.addWidget(self.tabela_pivo_sortimento, 3, 0, 1, 2)

    def _ao_mudar_selecao_tabela_pivo(self, _texto=None):
        self._atualizar_tabela_pivo()

    def _atualizar_opcoes_tabela_pivo(self, conn):
        """Repopula o combobox "Coluna (por classe)" da aba "Tabela por
        sortimento" — só famílias por classe (ver
        simulacao.colunas_grafico_resultado_disponiveis), diferente do
        combobox "Coluna" da aba Gráfico, que também aceita colunas
        comuns. Chamado de dentro de _atualizar_opcoes_formulario, mesmo
        padrão de _atualizar_opcoes_grafico."""
        try:
            _colunas_simples, bases_por_classe = simulacao.colunas_grafico_resultado_disponiveis(conn)
        except Exception:
            bases_por_classe = []

        opcoes = {"": None}
        valores_combo = [""]
        for base in bases_por_classe:
            opcoes[base] = base
            valores_combo.append(base)

        self._opcoes_tabela_pivo_coluna = opcoes
        self._repovoar_combo_preservando(self.combo_tabela_pivo_coluna, valores_combo)

    def _atualizar_tabela_pivo(self):
        coluna = self._opcoes_tabela_pivo_coluna.get(self.combo_tabela_pivo_coluna.currentText())
        if not coluna:
            self.tabela_pivo_sortimento.redefinir_colunas([])
            self._definir_status(self.label_status_tabela_pivo, "Escolha uma coluna pra ver a tabela.", "neutro")
            return

        tipo_agregacao = self.combo_tabela_pivo_agregacao.currentText() or "Soma"
        try:
            conn = conectar()
        except RuntimeError:
            self.tabela_pivo_sortimento.redefinir_colunas([])
            self._definir_status(self.label_status_tabela_pivo, "Nenhum projeto aberto.", "neutro")
            return
        try:
            try:
                dados = simulacao.tabela_evento_sortimento(conn, coluna, tipo_agregacao)
            except ValueError as e:
                self.tabela_pivo_sortimento.redefinir_colunas([])
                self._definir_status(self.label_status_tabela_pivo, str(e), "aviso")
                return
        finally:
            conn.close()

        if dados.empty:
            self.tabela_pivo_sortimento.redefinir_colunas([])
            self._definir_status(
                self.label_status_tabela_pivo,
                "Nenhum dado pra mostrar com essa seleção (nenhuma idade com evento de manejo "
                "preenchido).", "aviso")
            return

        # Ordem das colunas = ordem de 1ª aparição no DataFrame, que já
        # vem por limite_inferior (ver simulacao.tabela_evento_sortimento/
        # dados_grafico_resultado) — não alfabética. "Total" fechando a
        # tabela = soma/média (mesma "Agregação entre talhões" escolhida
        # acima, reaproveitada aqui pra combinar os sortimentos da linha
        # em vez de aparecer um terceiro modo de agregação pro usuário
        # escolher) dos sortimentos daquele evento.
        sortimentos_ordem = list(dict.fromkeys(dados["sortimento"]))
        self.tabela_pivo_sortimento.redefinir_colunas(
            ["evento"] + sortimentos_ordem + ["Total"],
            tipos_iniciais={s: "Float" for s in sortimentos_ordem + ["Total"]})

        eventos_presentes = set(dados["evento"])
        ordem_eventos = [
            evento for evento in (
                simulacao.EVENTO_RALEIO, simulacao.EVENTO_DESBASTE_1, simulacao.EVENTO_DESBASTE_2,
                simulacao.EVENTO_CORTE_RASO)
            if evento in eventos_presentes
        ]

        linhas = []
        for evento in ordem_eventos:
            valores_evento = dados[dados["evento"] == evento].set_index("sortimento")["valor"]
            valores_linha = [
                None if s not in valores_evento.index or pd.isna(valores_evento[s])
                else float(valores_evento[s])
                for s in sortimentos_ordem
            ]
            preenchidos = [v for v in valores_linha if v is not None]
            if preenchidos:
                total = sum(preenchidos) / len(preenchidos) if tipo_agregacao == "Média" else sum(preenchidos)
            else:
                total = None
            linha = (evento,) + tuple(valores_linha) + (total,)
            linhas.append(linha)
        self.tabela_pivo_sortimento.definir_linhas(linhas)

        self._definir_status(
            self.label_status_tabela_pivo,
            f"{len(ordem_eventos)} evento(s) x {len(sortimentos_ordem)} sortimento(s).", "sucesso")

    # ---------------- aba "Gráfico por classe" ----------------

    def _montar_aba_grafico_classe(self, container):
        """Combobox "Coluna (por classe)" (mesma lista de famílias por
        classe da aba "Tabela por sortimento") + "Agregação entre
        talhões" (Soma/Média) — desenha uma curva por evento de manejo
        (Raleio/1º Desbaste/2º Desbaste/Corte Raso), classe diamétrica no
        eixo x, ver simulacao.dados_grafico_por_classe. Diferente da aba
        "Gráfico" (que soma entre classes pra virar uma curva por
        sortimento), aqui a classe é o próprio eixo x — útil pra ver como
        um valor (ex: VET, RT, VTCC SIMULADO) se distribui pelas classes
        em cada evento, sem colapsar nelas."""
        layout = QGridLayout(container)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(2, 1)

        layout.addWidget(QLabel("Coluna (por classe)"), 0, 0)
        self.combo_grafico_classe_coluna = QComboBox()
        layout.addWidget(self.combo_grafico_classe_coluna, 0, 1)
        self.combo_grafico_classe_coluna.textActivated.connect(self._ao_mudar_selecao_grafico_classe)

        layout.addWidget(QLabel("Agregação entre talhões"), 1, 0)
        self.combo_grafico_classe_agregacao = QComboBox()
        self.combo_grafico_classe_agregacao.addItems(["Soma", "Média"])
        layout.addWidget(self.combo_grafico_classe_agregacao, 1, 1)
        self.combo_grafico_classe_agregacao.textActivated.connect(self._ao_mudar_selecao_grafico_classe)

        self.grafico_classe = GraficoPorClasseSimulacao()
        layout.addWidget(self.grafico_classe, 2, 0, 1, 2)

    def _atualizar_opcoes_grafico_classe(self, conn):
        """Repopula o combobox "Coluna (por classe)" da aba "Gráfico por
        classe" — mesma lista de _atualizar_opcoes_tabela_pivo (só
        famílias por classe), listas separadas porque cada combobox
        lembra sua própria seleção independente."""
        try:
            _colunas_simples, bases_por_classe = simulacao.colunas_grafico_resultado_disponiveis(conn)
        except Exception:
            bases_por_classe = []

        opcoes = {"": None}
        valores_combo = [""]
        for base in bases_por_classe:
            opcoes[base] = base
            valores_combo.append(base)

        self._opcoes_grafico_classe_coluna = opcoes
        self._repovoar_combo_preservando(self.combo_grafico_classe_coluna, valores_combo)

    def _ao_mudar_selecao_grafico_classe(self, _texto=None):
        self._atualizar_grafico_classe()

    def _atualizar_grafico_classe(self):
        coluna = self._opcoes_grafico_classe_coluna.get(self.combo_grafico_classe_coluna.currentText())
        if not coluna:
            self.grafico_classe.mostrar_mensagem("Escolha uma coluna pra ver o gráfico.")
            return

        tipo_agregacao = self.combo_grafico_classe_agregacao.currentText() or "Soma"
        try:
            conn = conectar()
        except RuntimeError:
            self.grafico_classe.mostrar_mensagem("Nenhum projeto aberto.")
            return
        try:
            try:
                df = simulacao.dados_grafico_por_classe(conn, coluna, tipo_agregacao)
            except ValueError as e:
                self.grafico_classe.mostrar_mensagem(str(e))
                return
        finally:
            conn.close()

        if df.empty:
            self.grafico_classe.mostrar_mensagem(
                "Nenhum dado pra mostrar com essa seleção (nenhuma idade com evento de manejo "
                "preenchido).")
            return

        self.grafico_classe.desenhar(df)

    def _montar_secao_cenarios(self, container):
        """Grid de cenários do modo "Múltiplos cenários" (checkbox no
        Cartão "Eventos de manejo", ver _ao_alternar_multiplos_cenarios) —
        cada linha usa os MESMOS campos de "Eventos de manejo" como
        formulário: ajusta os valores lá em cima, dá um nome aqui embaixo
        e clica "Adicionar cenário"; repete pra próxima combinação.
        "Gerar pendentes"/"Reiniciar" rodam o pipeline completo
        (gerar_populacao + reaplicar construtores + distribuição/volume
        por sortimento) pra cada linha, cada uma na sua própria tabela
        (ver simulacao.gerar_populacao/ativar_cenario) — "Gerar pendentes"
        só nos cenários com status != "Gerado" (continua de onde "Parar"
        deixou), "Reiniciar" em TODOS de novo, mesmo os já "Gerado" (ver
        _gerar_todos_cenarios). "Parar" pede uma parada no limite entre
        dois cenários, sem abortar o que já está rodando (ver
        _ThreadGerarLote.solicitar_parada). "Ativar cenário selecionado"
        copia o resultado de um deles pras tabelas de sempre, pra usar em
        Construtor de Variáveis/Gráfico de Resultados/Exportar Excel
        exatamente como hoje."""
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self._montar_painel_grade_cenarios(layout)

        # Desligado por padrão: o MIP contínuo (aba Ingressos) exige um
        # ajuste scipy.optimize.curve_fit POR TALHÃO (CPU-bound, não dá pra
        # vetorizar) — em lotes de centenas/milhares de cenários isso
        # facilmente domina o tempo total. "Gerar todos os cenários" já
        # calcula população + construtores + distribuição diamétrica +
        # volume por sortimento pra cada um; religue aqui só se precisar do
        # MIP de cada cenário do lote (ver _gerar_todos_cenarios/
        # _ThreadGerarLote, core/simulacao.py:calcular_mip_continuo).
        self.checkbox_calcular_mip_lote = QCheckBox("Calcular MIP contínuo em cada cenário do lote")
        self.checkbox_calcular_mip_lote.setToolTip(
            "Desligado por padrão — o MIP contínuo ajusta uma curva (scipy curve_fit) por "
            "talhão, uma etapa lenta que a maioria dos lotes não precisa. Religue só se for "
            "usar o MIP de cada cenário gerado (aba Ingressos)."
        )
        layout.addWidget(self.checkbox_calcular_mip_lote)

        self.tabela_cenarios = Tabela(
            colunas=(
                "nome", "idade_raleio", "intensidade_raleio", "idade_desbaste_1",
                "intensidade_desbaste_1", "idade_desbaste_2", "intensidade_desbaste_2",
                "idade_corte_raso", "status"),
            larguras={
                "nome": 110, "idade_raleio": 65, "intensidade_raleio": 80, "idade_desbaste_1": 80,
                "intensidade_desbaste_1": 95, "idade_desbaste_2": 80, "intensidade_desbaste_2": 95,
                "idade_corte_raso": 85, "status": 90},
        )
        self.tabela_cenarios.setMaximumHeight(170)
        self.tabela_cenarios.view.doubleClicked.connect(self._abrir_graficos_cenario_duplo_clique)
        layout.addWidget(self.tabela_cenarios)

        linha_nome = QHBoxLayout()
        linha_nome.addWidget(QLabel("Nome do cenário"))
        self.entry_nome_cenario = QLineEdit()
        self.entry_nome_cenario.setToolTip(
            "\"Adicionar cenário\" usa os valores de idade/intensidade preenchidos ali em cima, "
            "em \"Eventos de manejo\" — ajuste os campos, dê um nome, adicione; troque os "
            "valores e adicione de novo pra próxima combinação.")
        linha_nome.addWidget(self.entry_nome_cenario)
        layout.addLayout(linha_nome)

        # Em grade, os comandos quebram em várias linhas e não alargam o
        # cartão além da viewport (eram oito botões numa única linha).
        linha_botoes = QGridLayout()
        botoes_acao = []
        botao_adicionar = QPushButton("Adicionar cenário")
        icones.aplicar_icone(botao_adicionar, "adicionar")
        botao_adicionar.clicked.connect(self._adicionar_cenario)
        botoes_acao.append(botao_adicionar)
        botao_excluir = QPushButton("Excluir selecionado(s)")
        qss.aplicar_variante(botao_excluir, "perigo")
        icones.aplicar_icone(botao_excluir, "excluir")
        botao_excluir.clicked.connect(self._excluir_cenarios_selecionados)
        botoes_acao.append(botao_excluir)
        botao_ativar = QPushButton("Ativar cenário selecionado")
        icones.aplicar_icone(botao_ativar, "ativar_desativar")
        botao_ativar.clicked.connect(self._ativar_cenario_selecionado)
        botoes_acao.append(botao_ativar)
        self.botao_gerar_lote = QPushButton("Gerar pendentes")
        qss.aplicar_variante(self.botao_gerar_lote, "salvar")
        icones.aplicar_icone(self.botao_gerar_lote, "gerar", cor="white")
        self.botao_gerar_lote.setToolTip(
            "Roda o pipeline completo só nos cenários com status diferente de \"Gerado\" "
            "(\"Pendente\" ou \"Erro: ...\") — pula quem já foi gerado com sucesso. Continua de "
            "onde \"Parar\" deixou, sem refazer o que já está pronto. Pra regerar TUDO de novo "
            "(inclusive os já \"Gerado\"), use \"Reiniciar\".")
        self.botao_gerar_lote.clicked.connect(lambda: self._gerar_todos_cenarios(somente_pendentes=True))
        botoes_acao.append(self.botao_gerar_lote)
        self.botao_reiniciar_lote = QPushButton("Reiniciar")
        icones.aplicar_icone(self.botao_reiniciar_lote, "redefinir")
        self.botao_reiniciar_lote.setToolTip(
            "Roda o pipeline completo em TODOS os cenários cadastrados de novo, inclusive os que "
            "já têm status \"Gerado\" — substitui o resultado anterior de cada um. Pra rodar só o "
            "que falta, use \"Gerar pendentes\".")
        self.botao_reiniciar_lote.clicked.connect(lambda: self._gerar_todos_cenarios(somente_pendentes=False))
        botoes_acao.append(self.botao_reiniciar_lote)
        self.botao_parar_lote = QPushButton("Parar")
        qss.aplicar_variante(self.botao_parar_lote, "perigo")
        icones.aplicar_icone(self.botao_parar_lote, "parar")
        self.botao_parar_lote.setEnabled(False)
        self.botao_parar_lote.setToolTip(
            "Pede pra geração em lote parar depois do cenário que estiver rodando agora — nunca "
            "no meio de um cenário. O que já foi gerado fica salvo; o resto continua \"Pendente\" "
            "(ou \"Erro\"), pra retomar depois com \"Gerar pendentes\".")
        self.botao_parar_lote.clicked.connect(self._parar_geracao_lote)
        botoes_acao.append(self.botao_parar_lote)
        botao_exportar_cenarios = QPushButton("Exportar todos os cenários...")
        icones.aplicar_icone(botao_exportar_cenarios, "exportar")
        botao_exportar_cenarios.setToolTip(
            "Um Excel só com todos os cenários já \"Gerado\" (status na tabela acima) empilhados "
            "— colunas \"cenario\", idade/intensidade de Raleio/1º/2º Desbaste e idade do Corte "
            "Raso no início de cada linha identificam de qual cenário (e com que parâmetros) ela "
            "veio. Se o total estourar o limite de linhas de uma aba do Excel, pagina em mais de "
            "uma aba (\"Simulação 2\", \"Simulação 3\", ...), mas nunca divide um cenário entre "
            "duas abas — um cenário sozinho maior que o limite fica de fora (avisado no final).")
        botao_exportar_cenarios.clicked.connect(self.exportar_todos_cenarios)
        botoes_acao.append(botao_exportar_cenarios)
        self.botao_exportar_cenarios_banco = QPushButton("Exportar todos os cenários (banco de dados)...")
        icones.aplicar_icone(self.botao_exportar_cenarios_banco, "exportar")
        self.botao_exportar_cenarios_banco.setToolTip(
            "Mesma ideia do \"Exportar todos os cenários\" acima, mas grava num arquivo .sqlite "
            "novo, numa tabela só (colunas \"cenario\"/idade/intensidade de manejo na frente de "
            "cada linha) — sem limite de linhas por aba do Excel, pra quando o total empilhado de "
            "todos os cenários passa dos milhões de linhas.")
        self.botao_exportar_cenarios_banco.clicked.connect(self.exportar_todos_cenarios_banco)
        botoes_acao.append(self.botao_exportar_cenarios_banco)
        for indice, botao in enumerate(botoes_acao):
            linha_botoes.addWidget(botao, indice // 2, indice % 2)
        for coluna in range(2):
            linha_botoes.setColumnStretch(coluna, 1)
        layout.addLayout(linha_botoes)

        self.progressbar_cenarios = QProgressBar()
        self.progressbar_cenarios.setVisible(False)
        layout.addWidget(self.progressbar_cenarios)

        self.label_status_cenarios = QLabel("")
        # Sem quebra de linha, um status comprido ("Gerando cenário X/Y:
        # NomeDoCenárioComprido...") só era cortado no fim, invisível —
        # word wrap deixa a mensagem inteira legível, ocupando mais de uma
        # linha quando precisar, em vez de sumir com o texto.
        self.label_status_cenarios.setWordWrap(True)
        layout.addWidget(self.label_status_cenarios)

        self._montar_secao_ranking_cenarios(layout)

    def _montar_painel_grade_cenarios(self, layout):
        """Painel do modo "Grade automática" (checkbox_grade_automatica,
        ver _ao_alternar_grade_automatica) — fica escondido por padrão
        (só o modo "Múltiplos cenários", manual, aparece). Por manejo:
        idade mínima/máxima/passo (QLineEdit — mesma validação de
        _ler_idade) e intensidade mínima/máxima (QComboBox com as mesmas
        intensidades já disponíveis pra "Eventos de manejo", ver
        _atualizar_opcoes_formulario — o "passo" da intensidade é o
        "Passo de intensidade" já configurado em Configurações, que gerou
        essa lista; aqui só se escolhe o mín/máx dentro dela). Raleio/1º/2º
        Desbaste são opcionais — os 3 campos de idade em branco tratam
        aquele manejo como "não feito" em toda a grade (ver
        _ler_evento_grade_opcional); Corte Raso continua obrigatório.
        "Gerar grade de cenários" (_gerar_grade_cenarios) troca TODOS os
        cenários cadastrados pelo produto cartesiano dessas faixas — só as
        combinações com Raleio < 1º Desbaste < 2º Desbaste < Corte Raso
        sobrevivem (ver _preencher_idades_puladas) —, nomeados "Cenário
        1".."Cenário N" — dali em diante é a mesma tabela/"Gerar todos os
        cenários"/ranking de sempre."""
        self.painel_grade_cenarios = QWidget()
        self.painel_grade_cenarios.setVisible(False)
        layout.addWidget(self.painel_grade_cenarios)

        layout_grade = QVBoxLayout(self.painel_grade_cenarios)
        layout_grade.setContentsMargins(0, 0, 0, 8)

        grade = QGridLayout()
        grade.addWidget(QLabel("Idade mín."), 0, 1)
        grade.addWidget(QLabel("Idade máx."), 0, 2)
        grade.addWidget(QLabel("Idade passo"), 0, 3)
        grade.addWidget(QLabel("Intens. mín."), 0, 4)
        grade.addWidget(QLabel("Intens. máx."), 0, 5)

        (self.entry_idade_min_raleio, self.entry_idade_max_raleio, self.entry_idade_passo_raleio,
         self.combo_intensidade_min_raleio, self.combo_intensidade_max_raleio) = self._linha_grade_evento(
            grade, 1, "Raleio")
        (self.entry_idade_min_desbaste_1, self.entry_idade_max_desbaste_1,
         self.entry_idade_passo_desbaste_1, self.combo_intensidade_min_desbaste_1,
         self.combo_intensidade_max_desbaste_1) = self._linha_grade_evento(grade, 2, "1º Desbaste")
        (self.entry_idade_min_desbaste_2, self.entry_idade_max_desbaste_2,
         self.entry_idade_passo_desbaste_2, self.combo_intensidade_min_desbaste_2,
         self.combo_intensidade_max_desbaste_2) = self._linha_grade_evento(grade, 3, "2º Desbaste")
        # Corte Raso não tem intensidade própria — só a faixa de idade.
        self.entry_idade_min_corte_raso, self.entry_idade_max_corte_raso, \
            self.entry_idade_passo_corte_raso = self._linha_grade_idade(grade, 4, "Corte Raso")
        layout_grade.addLayout(grade)

        linha_botao = QHBoxLayout()
        botao_gerar_grade = QPushButton("Gerar grade de cenários")
        qss.aplicar_variante(botao_gerar_grade, "salvar")
        icones.aplicar_icone(botao_gerar_grade, "gerar", cor="white")
        botao_gerar_grade.clicked.connect(self._gerar_grade_cenarios)
        linha_botao.addWidget(botao_gerar_grade)
        linha_botao.addStretch(1)
        layout_grade.addLayout(linha_botao)

        separador = QFrame()
        separador.setFrameShape(QFrame.Shape.HLine)
        layout_grade.addWidget(separador)

    @staticmethod
    def _linha_grade_evento(grade, linha, rotulo):
        grade.addWidget(QLabel(rotulo), linha, 0)
        entry_min = QLineEdit()
        entry_min.setFixedWidth(55)
        grade.addWidget(entry_min, linha, 1)
        entry_max = QLineEdit()
        entry_max.setFixedWidth(55)
        grade.addWidget(entry_max, linha, 2)
        entry_passo = QLineEdit()
        entry_passo.setFixedWidth(55)
        grade.addWidget(entry_passo, linha, 3)
        combo_min = QComboBox()
        combo_min.setFixedWidth(90)
        grade.addWidget(combo_min, linha, 4)
        combo_max = QComboBox()
        combo_max.setFixedWidth(90)
        grade.addWidget(combo_max, linha, 5)
        return entry_min, entry_max, entry_passo, combo_min, combo_max

    @staticmethod
    def _linha_grade_idade(grade, linha, rotulo):
        grade.addWidget(QLabel(rotulo), linha, 0)
        entry_min = QLineEdit()
        entry_min.setFixedWidth(55)
        grade.addWidget(entry_min, linha, 1)
        entry_max = QLineEdit()
        entry_max.setFixedWidth(55)
        grade.addWidget(entry_max, linha, 2)
        entry_passo = QLineEdit()
        entry_passo.setFixedWidth(55)
        grade.addWidget(entry_passo, linha, 3)
        return entry_min, entry_max, entry_passo

    def _montar_secao_ranking_cenarios(self, layout):
        """Ranking dos cenários já gerados por um KPI = soma de 1+ colunas
        escolhidas (ex: um nó VET configurado só pra "Corte Raso" + outro
        só pra "Desbaste" — escolhendo os dois, o KPI é o VET total da
        simulação) — ver simulacao.ranquear_cenarios. Lista parecida com a
        do combobox "Coluna" do Gráfico de Resultados, mas NÃO a mesma
        (self._opcoes_kpi_coluna, montada em _atualizar_opcoes_grafico a
        partir de simulacao.colunas_kpi_cenarios_disponiveis — união das
        colunas de TODOS os cenários já gerados, não só a tabela canônica,
        senão coluna gerada pelo Construtor de Variáveis num cenário do
        lote ainda não ativado nunca apareceria aqui). Aqui em QListWidget
        de múltipla seleção — o KPI é sempre a SOMA de tudo que for
        selecionado, sem outro operador (único caso que precisava agora,
        não uma ferramenta de fórmula genérica)."""
        separador = QFrame()
        separador.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separador)

        self._container_rankings = QWidget()
        self._grid_rankings = QGridLayout(self._container_rankings)
        self._grid_rankings.setContentsMargins(0, 0, 0, 0)
        self._grid_rankings.setHorizontalSpacing(12)
        layout.addWidget(self._container_rankings)

        self.painel_ranking_kpi = QWidget()
        layout = QVBoxLayout(self.painel_ranking_kpi)
        layout.setContentsMargins(0, 0, 0, 0)

        rotulo = QLabel("Ranquear cenários por KPI")
        qss.aplicar_variante(rotulo, "titulo")
        layout.addWidget(rotulo)

        self.lista_kpi_colunas = QListWidget()
        self.lista_kpi_colunas.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lista_kpi_colunas.setMaximumHeight(110)
        self.lista_kpi_colunas.setToolTip(
            "Ctrl+clique (ou Shift+clique) pra escolher mais de uma — o KPI ranqueado é a SOMA "
            "de todas as colunas selecionadas (ex: marcar \"vet_corte\" e \"vet_desbaste\" dá o "
            "VET total da simulação).")
        layout.addWidget(self.lista_kpi_colunas)

        linha_direcao = QHBoxLayout()
        linha_direcao.addWidget(QLabel("Direção"))
        self.combo_kpi_direcao = QComboBox()
        self.combo_kpi_direcao.addItems(["Maior é melhor", "Menor é melhor"])
        linha_direcao.addWidget(self.combo_kpi_direcao)
        botao_ranquear = QPushButton("Ranquear cenários")
        icones.aplicar_icone(botao_ranquear, "buscar")
        botao_ranquear.clicked.connect(self._ranquear_cenarios)
        linha_direcao.addWidget(botao_ranquear)
        linha_direcao.addStretch(1)
        layout.addLayout(linha_direcao)

        self.tabela_ranking_cenarios = Tabela(
            colunas=("posicao", "cenario", "valor"),
            larguras={"posicao": 60, "cenario": 160, "valor": 120},
            tipos_iniciais={"posicao": "Inteiro", "valor": "Float"},
        )
        self.tabela_ranking_cenarios.setMaximumHeight(170)
        layout.addWidget(self.tabela_ranking_cenarios)

        self.painel_ranking_talhao = QWidget()
        layout_talhao = QVBoxLayout(self.painel_ranking_talhao)
        layout_talhao.setContentsMargins(0, 0, 0, 0)
        self._montar_secao_ranking_por_talhao(layout_talhao)
        self._layout_rankings_lado_a_lado = None
        self._atualizar_layout_rankings_responsivo(True, forcar=True)

    def _montar_secao_ranking_por_talhao(self, layout):
        """Ranking por talhão — mesmo KPI/direção escolhidos acima
        (lista_kpi_colunas/combo_kpi_direcao), mas em vez de somar todos
        os talhões num número só por cenário (_ranquear_cenarios), soma
        SEPARADO por talhão (ver simulacao.ranquear_cenarios_por_chave) —
        cada talhão tem seu próprio ranking de cenários, já que a condição
        de sítio/crescimento não é igual em todo talhão (o melhor cenário
        pra um pode não ser o melhor pra outro). Chave sempre a coluna de
        talhão configurada em "Parâmetros da Simulação" (obter_coluna_
        talhao) — não outro campo qualquer.

        "Mostrar/exportar só os N melhores por talhão": desmarcado, a
        tabela abaixo mostra TODOS os cenários de cada talhão; marcado,
        só os N melhores de cada um (spinbox ao lado) — e exportar_
        ranking_por_talhao exporta exatamente o que está mostrado ali
        (mesmo recorte), nunca mais."""
        rotulo = QLabel("Ranquear por talhão (mesmo KPI/direção acima)")
        qss.aplicar_variante(rotulo, "titulo")
        layout.addWidget(rotulo)

        linha_top_n = QHBoxLayout()
        self.checkbox_top_n_talhao = QCheckBox("Mostrar/exportar só os")
        self.checkbox_top_n_talhao.toggled.connect(self._ao_alternar_top_n_talhao)
        linha_top_n.addWidget(self.checkbox_top_n_talhao)
        self.spin_top_n_talhao = QSpinBox()
        self.spin_top_n_talhao.setRange(1, 999)
        self.spin_top_n_talhao.setValue(5)
        self.spin_top_n_talhao.setEnabled(False)
        linha_top_n.addWidget(self.spin_top_n_talhao)
        linha_top_n.addWidget(QLabel("melhores de cada talhão"))
        linha_top_n.addStretch(1)
        layout.addLayout(linha_top_n)

        linha_botoes_talhao = QHBoxLayout()
        botao_ranquear_talhao = QPushButton("Ranquear por talhão")
        icones.aplicar_icone(botao_ranquear_talhao, "buscar")
        botao_ranquear_talhao.clicked.connect(self._ranquear_cenarios_por_talhao)
        linha_botoes_talhao.addWidget(botao_ranquear_talhao)
        botao_exportar_ranking_talhao = QPushButton("Exportar ranking por talhão para Excel...")
        icones.aplicar_icone(botao_exportar_ranking_talhao, "exportar")
        botao_exportar_ranking_talhao.clicked.connect(self.exportar_ranking_por_talhao)
        botao_exportar_ranking_talhao.setToolTip(
            "Exporta os dados completos (mesmas abas \"Simulação\"/\"Volume por Sortimento\" de "
            "\"Exportar Todos os Cenários\") só dos cenários no ranking mostrado abaixo — cada "
            "talhão só traz linhas dos cenários que aparecem NO SEU PRÓPRIO ranking (talhões "
            "diferentes podem trazer cenários diferentes).")
        linha_botoes_talhao.addWidget(botao_exportar_ranking_talhao)
        linha_botoes_talhao.addStretch(1)
        layout.addLayout(linha_botoes_talhao)

        self.tabela_ranking_por_talhao = Tabela(
            colunas=("talhao", "posicao", "cenario", "valor"),
            larguras={"talhao": 100, "posicao": 60, "cenario": 160, "valor": 120},
            tipos_iniciais={"posicao": "Inteiro", "valor": "Float"},
        )
        self.tabela_ranking_por_talhao.setMaximumHeight(220)
        layout.addWidget(self.tabela_ranking_por_talhao)

    def _linha_coluna_opcional(self, layout, linha, rotulo, coluna=0):
        layout.addWidget(QLabel(rotulo), linha, coluna)
        combo = QComboBox()
        layout.addWidget(combo, linha, coluna + 1)
        combo.textActivated.connect(self._ao_mudar_selecao_coluna)
        return combo

    @staticmethod
    def _linha_evento(layout, linha, rotulo):
        layout.addWidget(QLabel(rotulo), linha, 0)
        entry_idade = QLineEdit()
        entry_idade.setFixedWidth(60)
        layout.addWidget(entry_idade, linha, 1)
        combo_intensidade = QComboBox()
        combo_intensidade.setFixedWidth(100)
        layout.addWidget(combo_intensidade, linha, 2)
        return entry_idade, combo_intensidade

    @staticmethod
    def _linha_idade(layout, linha, rotulo):
        # Corte Raso não tem intensidade própria (colheita total) — só a
        # idade, sem combobox de intensidade.
        layout.addWidget(QLabel(rotulo), linha, 0)
        entry_idade = QLineEdit()
        entry_idade.setFixedWidth(60)
        layout.addWidget(entry_idade, linha, 1)
        return entry_idade

    # ---------------- utilitários de combobox/status ----------------

    @staticmethod
    def _repovoar_combo(combo, itens, valor_desejado):
        """Repopula um combobox tentando, nesta ordem: `valor_desejado`
        (ex: o valor persistido em simulacao_metadados), a seleção atual
        (se ainda existir entre `itens`), ou fica sem seleção (currentText
        vazio) — mesma tolerância do StringVar do Tkinter original, que
        aceitava um valor fora da lista sem travar."""
        atual = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(itens)
        if valor_desejado in itens:
            combo.setCurrentText(valor_desejado)
        elif atual in itens:
            combo.setCurrentText(atual)
        else:
            combo.setCurrentIndex(-1)
        combo.blockSignals(False)

    @staticmethod
    def _repovoar_tabela_volume_classes(tabela, itens, valores_desejados, agregacoes_desejadas):
        """Mesma tolerância de _repovoar_combo, adaptada pra uma QTableWidget
        de 2 colunas (nome da variável, com checkbox pra marcar/desmarcar +
        combo Soma/Média por linha): marca os itens de `valores_desejados`
        (ex: o que foi persistido em simulacao_metadados) que ainda
        existirem entre `itens`; se `valores_desejados` vier vazio,
        preserva o que já estava marcado na tela em vez de desmarcar tudo
        (mesma prioridade "valor persistido, senão seleção atual" do
        combobox singular). Agregação por linha segue a mesma lógica:
        `agregacoes_desejadas` (persistido) tem prioridade, senão o que já
        estava escolhido na tela, senão "Soma"."""
        atual_marcados = {
            tabela.item(linha, 0).text() for linha in range(tabela.rowCount())
            if tabela.item(linha, 0).checkState() == Qt.CheckState.Checked
        }
        atual_agregacao = {
            tabela.item(linha, 0).text(): tabela.cellWidget(linha, 1).currentText()
            for linha in range(tabela.rowCount())
        }
        quer_marcar = set(valores_desejados) if valores_desejados else atual_marcados
        agregacao_por_item = {**atual_agregacao, **(agregacoes_desejadas or {})}

        tabela.blockSignals(True)
        tabela.setRowCount(0)
        for texto in itens:
            linha = tabela.rowCount()
            tabela.insertRow(linha)
            item = QTableWidgetItem(texto)
            item.setFlags((item.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEditable)
            item.setCheckState(
                Qt.CheckState.Checked if texto in quer_marcar else Qt.CheckState.Unchecked)
            tabela.setItem(linha, 0, item)
            combo = QComboBox()
            combo.addItems(["Soma", "Média"])
            combo.setCurrentText(agregacao_por_item.get(texto, "Soma"))
            tabela.setCellWidget(linha, 1, combo)
        tabela.blockSignals(False)

    @staticmethod
    def _repovoar_combo_preservando(combo, itens):
        """Repopula um combobox preservando a seleção atual se ainda
        existir entre os novos `itens` (que sempre trazem "" como 1ª
        opção nesse uso), senão volta pra "" — usado pelos combobox de
        coluna do Gráfico de Resultados, que não têm valor persistido."""
        atual = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(itens)
        combo.setCurrentText(atual if atual in itens else (itens[0] if itens else ""))
        combo.blockSignals(False)

    @staticmethod
    def _definir_status(rotulo, texto, chave_cor):
        rotulo.setText(texto)
        qss.aplicar_status(rotulo, chave_cor)

    # ---------------- lógica ----------------

    def novo_registro(self):
        # esta tela não tem formulário de edição manual pro resultado —
        # existe só pra manter a mesma interface das outras telas
        # (chamada ao trocar de projeto, antes de recarregar_lista)
        pass

    @staticmethod
    def _texto_numero_configuracao(valor):
        if valor is None:
            return ""
        return f"{float(valor):g}".replace(".", ",")

    def _carregar_classes_manejo(self, conn):
        row = conn.execute(
            "SELECT primeira_classe_diametrica, ultima_classe_diametrica, "
            "idade_maxima_manejo, numero_minimo_arvores_ha, "
            "tipo_normalizacao_weibull, ajuste_manejo_padrao, "
            "base_ajuste_logistico, base_calculo_mip "
            "FROM configuracoes WHERE id = 1"
        ).fetchone()
        if row is None:
            row = (None, None, None, None, "aditiva", 0, "ip", "fdp")

        primeira, ultima, idade_maxima, minimo_arvores, normalizacao, ajuste, base_log, base_mip = row
        self.entry_primeira_classe.setText(self._texto_numero_configuracao(primeira))
        self.entry_ultima_classe.setText(self._texto_numero_configuracao(ultima))
        self.entry_idade_maxima_manejo.setText(self._texto_numero_configuracao(idade_maxima))
        self.entry_numero_minimo_arvores.setText(self._texto_numero_configuracao(minimo_arvores))

        indice = self.combo_normalizacao_weibull.findData(
            normalizacao if normalizacao in simulacao.TIPOS_NORMALIZACAO_WEIBULL else "aditiva")
        self.combo_normalizacao_weibull.setCurrentIndex(max(indice, 0))
        self.checkbox_ajuste_manejo.setChecked(bool(ajuste))
        indice = self.combo_base_ajuste_logistico.findData(
            base_log if base_log in simulacao.BASES_AJUSTE_LOGISTICO else "ip")
        self.combo_base_ajuste_logistico.setCurrentIndex(max(indice, 0))
        indice = self.combo_base_calculo_mip.findData(
            base_mip if base_mip in simulacao.BASES_CALCULO_MIP else "fdp")
        self.combo_base_calculo_mip.setCurrentIndex(max(indice, 0))

    def _salvar_classes_manejo(self):
        valores = []
        for rotulo, campo in (
            ("Primeira classe diamétrica", self.entry_primeira_classe),
            ("Última classe diamétrica", self.entry_ultima_classe),
            ("Idade máxima de manejo", self.entry_idade_maxima_manejo),
            ("Mínimo de árvores/ha", self.entry_numero_minimo_arvores),
        ):
            texto = campo.text().strip()
            if not texto:
                valores.append(None)
                continue
            try:
                valores.append(float(texto.replace(",", ".")))
            except ValueError:
                QMessageBox.warning(self, "Simulação", f"Valor inválido em '{rotulo}': '{texto}'.")
                campo.setFocus()
                return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return
        try:
            conn.execute(
                "INSERT INTO configuracoes "
                "(id, primeira_classe_diametrica, ultima_classe_diametrica, intervalo_classe, "
                "idade_maxima_manejo, numero_minimo_arvores_ha, tipo_normalizacao_weibull, "
                "ajuste_manejo_padrao, base_ajuste_logistico, base_calculo_mip) "
                "VALUES (1, ?, ?, 1, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "primeira_classe_diametrica=excluded.primeira_classe_diametrica, "
                "ultima_classe_diametrica=excluded.ultima_classe_diametrica, "
                "idade_maxima_manejo=excluded.idade_maxima_manejo, "
                "numero_minimo_arvores_ha=excluded.numero_minimo_arvores_ha, "
                "tipo_normalizacao_weibull=excluded.tipo_normalizacao_weibull, "
                "ajuste_manejo_padrao=excluded.ajuste_manejo_padrao, "
                "base_ajuste_logistico=excluded.base_ajuste_logistico, "
                "base_calculo_mip=excluded.base_calculo_mip",
                (*valores, self.combo_normalizacao_weibull.currentData(),
                 int(self.checkbox_ajuste_manejo.isChecked()),
                 self.combo_base_ajuste_logistico.currentData(),
                 self.combo_base_calculo_mip.currentData()),
            )
            conn.commit()
        finally:
            conn.close()

        projeto.sincronizar()
        QMessageBox.information(self, "Simulação", "Parâmetros salvos.")

    def recarregar_lista(self):
        try:
            conn = conectar()
        except RuntimeError:
            for campo in (
                self.entry_primeira_classe, self.entry_ultima_classe,
                self.entry_idade_maxima_manejo, self.entry_numero_minimo_arvores,
            ):
                campo.clear()
            self.combo_normalizacao_weibull.setCurrentIndex(0)
            self.checkbox_ajuste_manejo.setChecked(False)
            self.combo_base_ajuste_logistico.setCurrentIndex(0)
            self.combo_base_calculo_mip.setCurrentIndex(0)
            self.botao_salvar_classes_manejo.setEnabled(False)
            self._definir_status(self.label_prontidao, "Nenhum projeto aberto.", "neutro")
            self._definir_status(self.label_status, "", "neutro")
            for combo in (
                self.combo_coluna_talhao, self.combo_coluna_fustes_observados,
                self.combo_coluna_dap_med_observado, self.combo_coluna_dap_max_observado,
                self.combo_coluna_dap_min_observado, self.combo_coluna_ht_observado,
                self.combo_coluna_vtcc_observado, self.combo_coluna_cv_dap_observado,
                self.combo_coluna_data_plantio,
                self.combo_coluna_data_medicao, self.combo_coluna_forma_distribuicao,
                self.combo_coluna_escala_distribuicao,
                self.combo_grafico_coluna, self.combo_tabela_pivo_coluna,
                self.combo_grafico_classe_coluna,
            ):
                combo.blockSignals(True)
                combo.clear()
                combo.blockSignals(False)
            self.tabela_volume_classes.blockSignals(True)
            self.tabela_volume_classes.setRowCount(0)
            self.tabela_volume_classes.blockSignals(False)
            self.checkbox_tabela_agregacao.blockSignals(True)
            self.checkbox_tabela_agregacao.setChecked(False)
            self.checkbox_tabela_agregacao.blockSignals(False)
            self.tabela_volume_classes.setVisible(False)
            self._opcoes_grafico_coluna = {"": (None, False)}
            self.grafico_resultado.mostrar_mensagem("Nenhum projeto aberto.")
            self._opcoes_tabela_pivo_coluna = {"": None}
            self.tabela_pivo_sortimento.redefinir_colunas([])
            self._definir_status(self.label_status_tabela_pivo, "Nenhum projeto aberto.", "neutro")
            self._opcoes_grafico_classe_coluna = {"": None}
            self.grafico_classe.mostrar_mensagem("Nenhum projeto aberto.")
            self.tabela_cenarios.definir_linhas([])
            self._opcoes_kpi_coluna = {}
            self.lista_kpi_colunas.clear()
            self.tabela_ranking_cenarios.definir_linhas([])
            self.botao_gerar.setEnabled(False)
            return

        self.botao_salvar_classes_manejo.setEnabled(True)
        try:
            self._carregar_classes_manejo(conn)
            self._atualizar_opcoes_formulario(conn)
            self._atualizar_prontidao(conn)
        finally:
            conn.close()

        self._atualizar_status_resultado()
        self._atualizar_grafico_resultado()
        self._atualizar_tabela_pivo()
        self._atualizar_grafico_classe()
        self._carregar_cenarios()

    def _atualizar_opcoes_formulario(self, conn):
        try:
            colunas = simulacao.colunas_base_ifc_talhao(conn)
        except Exception:
            colunas = []

        coluna_salva = simulacao.obter_coluna_talhao(conn)
        self._repovoar_combo(self.combo_coluna_talhao, colunas, coluna_salva or "")

        coluna_fustes_salva = simulacao.obter_coluna_fustes_observados(conn)
        self._repovoar_combo(self.combo_coluna_fustes_observados, colunas, coluna_fustes_salva or "")

        colunas_opcionais = [""] + colunas
        for combo, obter_salva in (
            (self.combo_coluna_dap_med_observado, simulacao.obter_coluna_dap_med_observado),
            (self.combo_coluna_dap_max_observado, simulacao.obter_coluna_dap_max_observado),
            (self.combo_coluna_dap_min_observado, simulacao.obter_coluna_dap_min_observado),
            (self.combo_coluna_ht_observado, simulacao.obter_coluna_ht_observado),
            (self.combo_coluna_vtcc_observado, simulacao.obter_coluna_vtcc_observado),
            (self.combo_coluna_cv_dap_observado, simulacao.obter_coluna_cv_dap_observado),
            (self.combo_coluna_data_plantio, simulacao.obter_coluna_data_plantio),
            (self.combo_coluna_data_medicao, simulacao.obter_coluna_data_medicao),
        ):
            salva = obter_salva(conn)
            self._repovoar_combo(combo, colunas_opcionais, salva or "")

        # Forma/escala da distribuição vêm de simulacao_talhao_idade (não de
        # base_ifc_talhao) — só existem depois da 1ª "Gerar simulação"; até
        # lá, fica só a opção em branco (usa forma_atual/escala_atual).
        try:
            colunas_populacao = [
                d[0] for d in conn.execute(
                    f'SELECT * FROM "{simulacao.TABELA_POPULACAO}" LIMIT 0').description
                if d[0] != "id"
            ]
        except Exception:
            colunas_populacao = []
        colunas_populacao_opcionais = [""] + colunas_populacao
        for combo, obter_salva in (
            (self.combo_coluna_forma_distribuicao, simulacao.obter_coluna_forma_distribuicao),
            (self.combo_coluna_escala_distribuicao, simulacao.obter_coluna_escala_distribuicao),
        ):
            salva = obter_salva(conn)
            self._repovoar_combo(combo, colunas_populacao_opcionais, salva or "")

        # Volume por sortimento: a tabela mostra só nomes-base detectados
        # (grupos de colunas "base_<classe>", ver
        # colunas_volume_por_classe_disponiveis) — nada até a 1ª "Gerar
        # simulação" com um construtor ligado em Classe Diamétrica.
        try:
            bases_volume = simulacao.colunas_volume_por_classe_disponiveis(conn)
        except Exception:
            bases_volume = []
        salvas_volume = simulacao.obter_colunas_base_volume_classes(conn)
        agregacoes_salvas = simulacao.obter_tipo_agregacao_volume(conn)
        self._repovoar_tabela_volume_classes(
            self.tabela_volume_classes, bases_volume, salvas_volume, agregacoes_salvas)
        # setChecked dispara _ao_alternar_tabela_agregacao (mostra/esconde
        # a tabela acima) só se o estado realmente mudar do padrão
        # (desligado) — senão a tabela já nasce escondida certa.
        self.checkbox_tabela_agregacao.setChecked(simulacao.obter_usar_tabela_agregacao_volume(conn))

        self._atualizar_opcoes_grafico(conn)
        self._atualizar_opcoes_tabela_pivo(conn)
        self._atualizar_opcoes_grafico_classe(conn)

        try:
            disponiveis = simulacao.obter_intensidades_disponiveis(conn)
        except Exception:
            disponiveis = {"int_raleio": [], "int_desbaste_1": [], "int_desbaste_2": []}

        self._mapa_intensidade_raleio = self._definir_opcoes_intensidade(
            self.combo_intensidade_raleio, disponiveis["int_raleio"])
        self._mapa_intensidade_desbaste_1 = self._definir_opcoes_intensidade(
            self.combo_intensidade_desbaste_1, disponiveis["int_desbaste_1"])
        self._mapa_intensidade_desbaste_2 = self._definir_opcoes_intensidade(
            self.combo_intensidade_desbaste_2, disponiveis["int_desbaste_2"])

        # Combobox mín/máx do painel "Grade automática" (ver
        # _montar_painel_grade_cenarios) — mesma lista de intensidades de
        # cada manejo acima, só que em par (mín/máx) pra definir uma faixa
        # em vez de um valor único; máx já parte da última opção (faixa
        # cheia por padrão, o usuário estreita se quiser).
        self._definir_opcoes_intensidade(self.combo_intensidade_min_raleio, disponiveis["int_raleio"])
        self._definir_opcoes_intensidade(
            self.combo_intensidade_max_raleio, disponiveis["int_raleio"], ultimo_por_padrao=True)
        self._definir_opcoes_intensidade(
            self.combo_intensidade_min_desbaste_1, disponiveis["int_desbaste_1"])
        self._definir_opcoes_intensidade(
            self.combo_intensidade_max_desbaste_1, disponiveis["int_desbaste_1"], ultimo_por_padrao=True)
        self._definir_opcoes_intensidade(
            self.combo_intensidade_min_desbaste_2, disponiveis["int_desbaste_2"])
        self._definir_opcoes_intensidade(
            self.combo_intensidade_max_desbaste_2, disponiveis["int_desbaste_2"], ultimo_por_padrao=True)

    @staticmethod
    def _definir_opcoes_intensidade(combo, valores, ultimo_por_padrao=False):
        opcoes = [_formatar_intensidade(v) for v in valores]
        mapa = dict(zip(opcoes, valores))
        atual = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(opcoes)
        padrao = (opcoes[-1] if ultimo_por_padrao else opcoes[0]) if opcoes else ""
        combo.setCurrentText(atual if atual in opcoes else padrao)
        combo.blockSignals(False)
        return mapa

    def _atualizar_prontidao(self, conn):
        # A coluna de talhão/fustes observados só é gravada no banco
        # quando "Gerar simulação" roda de verdade — passa aqui o que
        # está selecionado nos comboboxes agora, senão o botão nunca
        # destravaria (o valor ainda não salvo faria a checagem achar
        # que falta escolher).
        status = simulacao.verificar_prontidao(
            conn,
            coluna_talhao_ifc=self.combo_coluna_talhao.currentText() or None,
            coluna_fustes_observados=self.combo_coluna_fustes_observados.currentText() or None,
        )
        if status["pronta"]:
            self._definir_status(self.label_prontidao, "Pronto pra gerar a simulação.", "sucesso")
            self.botao_gerar.setEnabled(True)
        else:
            texto = "Faltando: " + "; ".join(status["pendencias"])
            self._definir_status(self.label_prontidao, texto, "aviso")
            self.botao_gerar.setEnabled(False)

    def _ao_mudar_selecao_coluna(self, _texto=None):
        try:
            conn = conectar()
        except RuntimeError:
            return
        try:
            self._atualizar_prontidao(conn)
        finally:
            conn.close()

    def _atualizar_status_resultado(self):
        """Só confirma que a simulação existe e quantas linhas tem — sem
        materializar o resultado (talhão × idade × classe diamétrica) na
        UI. Quem quiser ver o resultado de verdade usa "Exportar para
        Excel", que lê e grava tudo direto, sem passar pela UI (a Tabela
        Qt já é virtualizada, mas mesmo assim não faz sentido carregar uma
        simulação inteira só pra conferência visual)."""
        try:
            conn = conectar()
        except RuntimeError:
            self._definir_status(self.label_status, "Nenhum projeto aberto.", "neutro")
            return

        try:
            existe = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (simulacao.TABELA_POPULACAO,)
            ).fetchone()
            if existe is None:
                self._definir_status(self.label_status, "Nenhuma simulação gerada ainda.", "neutro")
                return

            total = conn.execute(f'SELECT COUNT(*) FROM "{simulacao.TABELA_POPULACAO}"').fetchone()[0]
            self._definir_status(
                self.label_status,
                f"{total:,} linha(s) geradas — use \"Exportar para Excel\" pra conferir o resultado.",
                "sucesso")
        finally:
            conn.close()

    # ---------------- gráfico de resultados ----------------

    def _atualizar_opcoes_grafico(self, conn):
        """Repopula o combobox "Coluna" do gráfico (ver
        simulacao.colunas_grafico_resultado_disponiveis) — chamado de
        dentro de _atualizar_opcoes_formulario, com a MESMA conexão já
        aberta ali (só popula os valores; quem de fato consulta os dados e
        redesenha é _atualizar_grafico_resultado, chamado à parte em
        recarregar_lista, com conexão própria — mesmo padrão de
        _atualizar_status_resultado)."""
        try:
            colunas_simples, bases_por_classe = simulacao.colunas_grafico_resultado_disponiveis(conn)
        except Exception:
            colunas_simples, bases_por_classe = [], []

        opcoes = {"": (None, False)}
        valores_combo = [""]
        for nome in colunas_simples:
            opcoes[nome] = (nome, False)
            valores_combo.append(nome)
        for base in bases_por_classe:
            texto = f"{base} (por classe → sortimento)"
            opcoes[texto] = (base, True)
            valores_combo.append(texto)

        self._opcoes_grafico_coluna = opcoes
        self._repovoar_combo_preservando(self.combo_grafico_coluna, valores_combo)

        # Ranking de cenários por KPI (ver _montar_secao_ranking_cenarios)
        # — lista À PARTE, não a mesma de self._opcoes_grafico_coluna: essa
        # vem só da tabela canônica (o cenário ativado por último, ou
        # nenhuma se nunca ativou nenhum), enquanto o KPI precisa enxergar
        # colunas geradas pelo Construtor de Variáveis em QUALQUER cenário
        # já gerado do lote, mesmo sem ter sido ativado ainda (ver
        # simulacao.colunas_kpi_cenarios_disponiveis) — sem essa distinção
        # essas colunas nunca apareciam pra ranquear. Sem a opção vazia
        # (não faz sentido somar "nada"). Preserva a seleção anterior que
        # ainda existir, senão escolher as colunas de novo a cada "Gerar
        # todos os cenários" seria chato.
        try:
            colunas_kpi_simples, bases_kpi_por_classe = simulacao.colunas_kpi_cenarios_disponiveis(conn)
        except Exception:
            colunas_kpi_simples, bases_kpi_por_classe = [], []

        opcoes_kpi = {}
        valores_kpi = []
        for nome in colunas_kpi_simples:
            opcoes_kpi[nome] = (nome, False)
            valores_kpi.append(nome)
        for base in bases_kpi_por_classe:
            texto = f"{base} (por classe → sortimento)"
            opcoes_kpi[texto] = (base, True)
            valores_kpi.append(texto)
        self._opcoes_kpi_coluna = opcoes_kpi

        selecionados_antes = {item.text() for item in self.lista_kpi_colunas.selectedItems()}
        self.lista_kpi_colunas.clear()
        for texto in valores_kpi:
            item = QListWidgetItem(texto)
            self.lista_kpi_colunas.addItem(item)
            if texto in selecionados_antes:
                item.setSelected(True)

    def _ao_mudar_selecao_grafico(self, _texto=None):
        self._atualizar_grafico_resultado()

    def _atualizar_grafico_resultado(self):
        """Consulta (conexão própria) e redesenha o gráfico conforme a
        seleção atual dos combobox "Coluna"/"Agregação entre talhões" —
        chamado a cada seleção nova e depois de recarregar_lista (inclusive
        depois de "Gerar simulação"), sempre recalculando do zero em vez de
        cachear o desenho — a consulta é barata e evita qualquer
        divergência entre o que está na tela e o banco atual. A reação à
        troca de tema fica a cargo do próprio widget (ver
        widgets/grafico_simulacao.py), que reaplica o último desenho."""
        nome_real, por_classe = self._opcoes_grafico_coluna.get(
            self.combo_grafico_coluna.currentText(), (None, False))
        if not nome_real:
            self.grafico_resultado.mostrar_mensagem("Escolha uma coluna pra ver o gráfico.")
            return

        tipo_agregacao = self.combo_grafico_agregacao.currentText() or "Soma"

        try:
            conn = conectar()
        except RuntimeError:
            self.grafico_resultado.mostrar_mensagem("Nenhum projeto aberto.")
            return
        try:
            try:
                df = simulacao.dados_grafico_resultado(conn, nome_real, por_classe, tipo_agregacao)
            except ValueError as e:
                self.grafico_resultado.mostrar_mensagem(str(e))
                return
        finally:
            conn.close()

        if df.empty:
            self.grafico_resultado.mostrar_mensagem("Nenhum dado pra mostrar com essa seleção.")
            return

        self.grafico_resultado.desenhar(df, por_classe)

    def exportar_excel(self):
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return
        try:
            df_populacao, df_sortimento = self._carregar_dados_geracao(conn)
        finally:
            conn.close()

        if df_populacao is None:
            QMessageBox.warning(self, "Simulação", "Nenhuma simulação gerada ainda.")
            return

        caminho, _ = QFileDialog.getSaveFileName(
            self, "Exportar Simulação", "", "Planilha Excel (*.xlsx)")
        if not caminho:
            return
        if not caminho.endswith(".xlsx"):
            caminho += ".xlsx"

        try:
            with pd.ExcelWriter(caminho, **_ENGINE_KWARGS_EXCEL) as writer:
                df_populacao.to_excel(writer, sheet_name="Simulação", index=False)
                if df_sortimento is not None:
                    df_sortimento.to_excel(writer, sheet_name="Volume por Sortimento", index=False)
        except Exception as e:
            QMessageBox.critical(self, "Simulação", f"Não foi possível exportar:\n{e}")
            return

        texto_sortimento = (
            f" e {len(df_sortimento):,} linha(s) de volume por sortimento"
            if df_sortimento is not None else ""
        )
        QMessageBox.information(
            self, "Simulação",
            f"Exportado: {len(df_populacao):,} linha(s){texto_sortimento} em\n{caminho}")

    def exportar_todos_cenarios(self):
        """Um .xlsx só com todos os cenários já "Gerado" empilhados —
        cada linha (nas 2 abas, "Simulação" e "Volume por Sortimento")
        marcada com "cenario" + idade/intensidade de Raleio/1º Desbaste/
        2º Desbaste/idade do Corte Raso no início, lidos direto de
        simulacao_cenarios (sobrescreve qualquer uma dessas colunas que já
        viesse gravada em simulacao_volume_sortimento__cenarioN, ver
        core/simulacao.py:calcular_volume_por_sortimento — aqui são
        sempre recalculadas na hora, cobre também cenários gerados antes
        dessas colunas existirem). Se o total de linhas estourar o limite
        de uma aba do .xlsx, pagina em mais de uma aba ("Simulação 2",
        "Simulação 3", ...) SEM NUNCA dividir um cenário entre duas abas
        — ver _particionar_paginas_excel. Um cenário sozinho maior que o
        limite de uma aba não caberia sem violar essa regra, então fica
        de fora (avisado no resumo final, não trava o resto da
        exportação)."""
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return

        try:
            cenarios = conn.execute(
                "SELECT id, nome, idade_raleio, intensidade_raleio, idade_desbaste_1, "
                "intensidade_desbaste_1, idade_desbaste_2, intensidade_desbaste_2, idade_corte_raso "
                "FROM simulacao_cenarios WHERE status = 'Gerado' ORDER BY id"
            ).fetchall()
            if not cenarios:
                QMessageBox.warning(
                    self, "Simulação",
                    "Nenhum cenário com status \"Gerado\" — use \"Gerar pendentes\" antes "
                    "de exportar.")
                return

            blocos_populacao = []
            blocos_sortimento = []
            cenarios_sem_dado = []
            for cenario_id, nome, *valores_manejo in cenarios:
                df_populacao, df_sortimento = self._carregar_dados_geracao(
                    conn, cenario_id=cenario_id)
                if df_populacao is None:
                    cenarios_sem_dado.append(nome)
                    continue
                campos_metadados = [("cenario", nome)] + list(zip(_CAMPOS_MANEJO_CENARIO, valores_manejo))
                blocos_populacao.append(
                    (nome, self._inserir_metadados_cenario(df_populacao, campos_metadados)))
                if df_sortimento is not None:
                    blocos_sortimento.append(
                        (nome, self._inserir_metadados_cenario(df_sortimento, campos_metadados)))
        finally:
            conn.close()

        if not blocos_populacao:
            QMessageBox.warning(
                self, "Simulação",
                "Nenhum cenário \"Gerado\" tem dado pra exportar (tabelas ausentes — gere de "
                "novo).")
            return

        paginas_populacao, excedidos_populacao = _particionar_paginas_excel(blocos_populacao)
        paginas_sortimento, excedidos_sortimento = _particionar_paginas_excel(blocos_sortimento)

        caminho, _ = QFileDialog.getSaveFileName(
            self, "Exportar Todos os Cenários", "", "Planilha Excel (*.xlsx)")
        if not caminho:
            return
        if not caminho.endswith(".xlsx"):
            caminho += ".xlsx"

        try:
            with pd.ExcelWriter(caminho, **_ENGINE_KWARGS_EXCEL) as writer:
                _escrever_paginas_excel(writer, paginas_populacao, "Simulação")
                _escrever_paginas_excel(writer, paginas_sortimento, "Volume por Sortimento")
        except Exception as e:
            QMessageBox.critical(self, "Simulação", f"Não foi possível exportar:\n{e}")
            return

        n_cenarios_exportados = sum(len(p) for p in paginas_populacao)
        total_linhas_populacao = sum(len(df) for dfs in paginas_populacao for df in dfs)
        partes = [
            f"{n_cenarios_exportados} cenário(s), {total_linhas_populacao:,} linha(s) de "
            "simulação" + (f" em {len(paginas_populacao)} aba(s)" if len(paginas_populacao) > 1 else "")
        ]
        if blocos_sortimento:
            total_linhas_sortimento = sum(len(df) for dfs in paginas_sortimento for df in dfs)
            partes.append(
                f"{total_linhas_sortimento:,} linha(s) de volume por sortimento"
                + (f" em {len(paginas_sortimento)} aba(s)" if len(paginas_sortimento) > 1 else ""))
        texto = "Exportado: " + "; ".join(partes) + f"\nem\n{caminho}"

        avisos = []
        if cenarios_sem_dado:
            avisos.append(
                "Sem dado pra exportar (tabelas do cenário não existem, apesar do status "
                "\"Gerado\"): " + ", ".join(cenarios_sem_dado))
        if excedidos_populacao:
            avisos.append(
                "Cenário(s) com mais linhas de simulação do que cabe numa aba do Excel "
                f"({LIMITE_LINHAS_EXCEL:,}) — como um cenário nunca é dividido entre duas abas, "
                "ficaram de fora: " + ", ".join(excedidos_populacao))
        if excedidos_sortimento:
            avisos.append(
                "Cenário(s) com mais linhas de volume por sortimento do que cabe numa aba — "
                "ficaram de fora: " + ", ".join(excedidos_sortimento))

        if avisos:
            QMessageBox.warning(self, "Simulação", texto + "\n\n" + "\n\n".join(avisos))
        else:
            QMessageBox.information(self, "Simulação", texto)

    def exportar_todos_cenarios_banco(self):
        """Igual exportar_todos_cenarios (Excel), mas o destino é um
        arquivo .sqlite novo com todos os cenários "Gerado" empilhados
        numa tabela só (NOME_TABELA_EXPORTACAO_CENARIOS) — pensado pra
        quando o total de linhas passa dos milhões (o limite de uma aba
        do Excel é ~1 milhão). A leitura/gravação em si roda em
        background (_ThreadExportarCenariosBanco) — só a escolha do
        cenários/arquivo de destino acontece aqui, na thread da GUI."""
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return
        try:
            cenarios = conn.execute(
                "SELECT id, nome, idade_raleio, intensidade_raleio, idade_desbaste_1, "
                "intensidade_desbaste_1, idade_desbaste_2, intensidade_desbaste_2, idade_corte_raso "
                "FROM simulacao_cenarios WHERE status = 'Gerado' ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        if not cenarios:
            QMessageBox.warning(
                self, "Simulação",
                "Nenhum cenário com status \"Gerado\" — use \"Gerar pendentes\" antes de exportar.")
            return

        caminho, _ = QFileDialog.getSaveFileName(
            self, "Exportar Todos os Cenários (Banco de Dados)", "", "Banco de dados SQLite (*.sqlite)")
        if not caminho:
            return
        if not caminho.endswith(".sqlite"):
            caminho += ".sqlite"
        # Sempre um arquivo NOVO/limpo — sqlite3.connect num caminho já
        # existente abriria o banco existente por cima (schema/dados de
        # uma exportação anterior pro MESMO caminho ficariam misturados
        # em vez de substituídos).
        try:
            Path(caminho).unlink(missing_ok=True)
        except OSError as e:
            QMessageBox.critical(
                self, "Simulação", f"Não foi possível preparar o arquivo de destino:\n{e}")
            return

        caminho_trabalho = projeto.caminho_trabalho()
        self.botao_exportar_cenarios_banco.setEnabled(False)
        self._definir_status(self.label_status_cenarios, f"Exportando cenário 0/{len(cenarios)}...", "neutro")
        self.progressbar_cenarios.setRange(0, len(cenarios))
        self.progressbar_cenarios.setValue(0)
        self.progressbar_cenarios.setVisible(True)

        thread = _ThreadExportarCenariosBanco(caminho_trabalho, caminho, cenarios, parent=self)
        self._thread_exportar_cenarios_banco = thread
        thread.progresso.connect(self._ao_progredir_exportacao_cenarios_banco)
        thread.concluido.connect(
            lambda resumo: self._finalizar_exportacao_cenarios_banco(resumo=resumo))
        thread.falhou.connect(
            lambda erro: self._finalizar_exportacao_cenarios_banco(erro=erro))
        thread.start()

    def _ao_progredir_exportacao_cenarios_banco(self, numero, total, nome):
        self._definir_status(
            self.label_status_cenarios, f"Exportando cenário {numero}/{total}: {nome}...", "neutro")
        self.progressbar_cenarios.setRange(0, total)
        self.progressbar_cenarios.setValue(numero)

    def _finalizar_exportacao_cenarios_banco(self, resumo=None, erro=None):
        self._thread_exportar_cenarios_banco = None
        self.progressbar_cenarios.setVisible(False)
        self.botao_exportar_cenarios_banco.setEnabled(True)

        if erro is not None:
            self._definir_status(self.label_status_cenarios, "", "neutro")
            QMessageBox.critical(self, "Simulação", f"Não foi possível exportar:\n{erro}")
            return

        self._definir_status(self.label_status_cenarios, "", "neutro")
        texto = (
            f"Exportado: {resumo['exportados']} cenário(s), {resumo['total_linhas']:,} linha(s)\n"
            f"em\n{resumo['caminho']}\n(tabela \"{NOME_TABELA_EXPORTACAO_CENARIOS}\")")
        if resumo["cenarios_sem_dado"]:
            texto += (
                "\n\nSem dado pra exportar (tabelas do cenário não existem, apesar do status "
                "\"Gerado\"): " + ", ".join(resumo["cenarios_sem_dado"]))
            QMessageBox.warning(self, "Simulação", texto)
        else:
            QMessageBox.information(self, "Simulação", texto)

    @staticmethod
    def _inserir_metadados_cenario(df, campos_metadados):
        """Devolve uma cópia de `df` com as colunas de `campos_metadados`
        (lista de (nome, valor) NA ORDEM desejada, ex: [("cenario", "A"),
        ("idade_raleio", 5), ...]) inseridas no início, sobrescrevendo
        qualquer coluna de mesmo nome que já existisse em `df` (ex: um
        "cenario" gravado em simulacao_volume_sortimento por uma geração
        anterior a essas colunas de metadados existirem) em vez de
        duplicar."""
        df = df.drop(columns=[nome for nome, _valor in campos_metadados], errors="ignore").copy()
        for i, (nome, valor) in enumerate(campos_metadados):
            df.insert(i, nome, valor)
        return df

    def _carregar_dados_geracao(self, conn, sufixo_tabela="", cenario_id=None):
        """Lê população (+ distribuição diamétrica pivotada em colunas
        "classe_*"/"fdp_*") e volume por sortimento (se a etapa tiver
        rodado) de UMA geração — canônica (`sufixo_tabela=""`,
        `cenario_id=None`, comportamento de sempre, usado por
        exportar_excel) ou de um cenário específico do lote
        (`cenario_id=N`, filtrando as tabelas unificadas `simulacao_lote_
        *` — usado por exportar_todos_cenarios). Devolve (df_populacao,
        df_sortimento) — df_populacao None se a tabela nem existir ou
        estiver vazia/sem linha desse cenário; df_sortimento None se a
        etapa de volume por sortimento não tiver rodado (mesmo tratamento
        opcional de sempre — "Volume" não mapeado ou sem sortimento
        cadastrado)."""
        if cenario_id is not None:
            resultado_parquet = simulacao.carregar_cenario_parquet(conn, cenario_id)
            if resultado_parquet is not None:
                df_populacao = resultado_parquet["_df_populacao"].copy()
                linhas = resultado_parquet.get("_linhas_distribuicao", ())
                if linhas:
                    df_dist = pd.DataFrame({
                        "populacao_id": linhas[0], "classe": linhas[1],
                        "probabilidade": linhas[2], "densidade": linhas[3]})
                    prob = df_dist.pivot(
                        index="populacao_id", columns="classe", values="probabilidade")
                    prob.columns = [f"classe_{c:g}" for c in prob.columns]
                    fdp = df_dist.pivot(index="populacao_id", columns="classe", values="densidade")
                    fdp.columns = [f"fdp_{c:g}" for c in fdp.columns]
                    dist_larga = prob.join(fdp)
                    df_populacao = df_populacao.join(dist_larga, on="id")
                df_populacao = df_populacao.drop(columns=["id"], errors="ignore")

                df_sortimento = None
                persistir_volume = resultado_parquet.get(
                    "resultado_volume_sortimento", {}).get("_persistir_volume")
                if persistir_volume:
                    _sql, colunas_volume, _marcadores, linhas_volume = persistir_volume
                    if linhas_volume:
                        df_sortimento = pd.DataFrame.from_records(
                            linhas_volume, columns=colunas_volume).drop(columns=["id"], errors="ignore")
                return df_populacao, df_sortimento

            tabela_populacao = simulacao.TABELA_LOTE_POPULACAO
            filtro_sql, parametros = " WHERE cenario_id = ?", (cenario_id,)
        else:
            tabela_populacao = simulacao.TABELA_POPULACAO + sufixo_tabela
            filtro_sql, parametros = "", ()
        existe = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (tabela_populacao,)
        ).fetchone()
        if existe is None:
            return None, None

        if cenario_id is not None:
            # Nunca SELECT * numa tabela do lote: vazaria "cenario_id" pra
            # dentro do DataFrame exportado (ver core/simulacao.py:
            # ativar_cenario, mesmo cuidado).
            colunas = [
                linha[1] for linha in conn.execute(f'PRAGMA table_info("{tabela_populacao}")')
                if linha[1] != "cenario_id"
            ]
            colunas_sql = ", ".join(f'"{c}"' for c in colunas)
            df_populacao = pd.read_sql_query(
                f'SELECT {colunas_sql} FROM "{tabela_populacao}"{filtro_sql}', conn, params=parametros)
        else:
            df_populacao = pd.read_sql_query(f'SELECT * FROM "{tabela_populacao}"{filtro_sql}', conn)
        if df_populacao.empty:
            return None, None

        colunas_distribuicao, valores_por_id = self._carregar_distribuicao_pivotada(
            conn, df_populacao["id"].tolist(), sufixo_tabela=sufixo_tabela, cenario_id=cenario_id)

        # Volume por sortimento: etapa opcional — pulada quando "Volume"
        # não está mapeado ou não há sortimento cadastrado, ver
        # simulacao.calcular_volume_por_sortimento.
        if cenario_id is not None:
            tabela_sortimento = simulacao.TABELA_LOTE_VOLUME_SORTIMENTO
        else:
            tabela_sortimento = simulacao.TABELA_VOLUME_SORTIMENTO + sufixo_tabela
        existe_sortimento = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (tabela_sortimento,)
        ).fetchone()
        df_sortimento = None
        if existe_sortimento is not None:
            if cenario_id is not None:
                colunas_sortimento = [
                    linha[1] for linha in conn.execute(f'PRAGMA table_info("{tabela_sortimento}")')
                    if linha[1] not in ("id", "cenario_id")
                ]
                colunas_sortimento_sql = ", ".join(f'"{c}"' for c in colunas_sortimento)
                df_sortimento = pd.read_sql_query(
                    f'SELECT {colunas_sortimento_sql} FROM "{tabela_sortimento}" WHERE cenario_id = ?',
                    conn, params=(cenario_id,))
            else:
                df_sortimento = pd.read_sql_query(
                    f'SELECT * FROM "{tabela_sortimento}"', conn
                ).drop(columns=["id"])

        # Monta as colunas de classe (probabilidade "classe_x" + densidade
        # "fdp_x") todas de uma vez (uma linha por id, já com tudo) e junta
        # com um só pd.concat, em vez de inserir coluna por coluna num loop
        # — isso fragmentava o DataFrame (um realloc por coluna) o
        # suficiente pra disparar PerformanceWarning do pandas em
        # simulações grandes.
        vazio_distribuicao = (None,) * len(colunas_distribuicao)
        linhas_distribuicao = [
            valores_por_id.get(id_populacao, vazio_distribuicao) for id_populacao in df_populacao["id"]
        ]
        df_distribuicao = pd.DataFrame(
            linhas_distribuicao, columns=colunas_distribuicao, index=df_populacao.index)
        df_populacao = pd.concat([df_populacao.drop(columns=["id"]), df_distribuicao], axis=1)

        return df_populacao, df_sortimento

    @staticmethod
    def _carregar_distribuicao_pivotada(conn, ids, sufixo_tabela="", cenario_id=None):
        """Lê a distribuição diamétrica só pro intervalo de populacao_id
        visível (BETWEEN em vez de IN — evita o limite de parâmetros do
        sqlite3 com milhares de ids) e pivota em DUAS famílias de coluna
        por classe — "classe_<valor>" (probabilidade, massa dentro da
        classe, S(classe-0,5)-S(classe+0,5)) e "fdp_<valor>" (densidade, a
        altura da PDF da Weibull exatamente naquele valor, ver
        simulacao.densidade_weibull) — devolvendo {populacao_id:
        (probabilidade_classe_1, ..., probabilidade_classe_n,
        fdp_classe_1, ..., fdp_classe_n)}, junto da lista ordenada de
        nomes de coluna (classe_* primeiro, fdp_* depois, na mesma ordem
        de classe). `sufixo_tabela`: mesmo papel que em gerar_populacao —
        lê a tabela sufixada de uma geração única em vez da canônica.
        `cenario_id` (opcional): lê `simulacao_lote_distribuicao_
        diametrica` filtrando por esse cenário em vez de uma tabela
        sufixada — ao contrário da sufixada antiga, essa tabela é
        COMPARTILHADA por todos os cenários do lote, então o filtro por
        `cenario_id` é obrigatório aqui (não só o BETWEEN por
        populacao_id, que sozinho não distingue cenários diferentes cujos
        ids se intercalam)."""
        if not ids:
            return [], {}

        if cenario_id is not None:
            tabela_distribuicao = simulacao.TABELA_LOTE_DISTRIBUICAO
        else:
            tabela_distribuicao = simulacao.TABELA_DISTRIBUICAO + sufixo_tabela
        existe = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (tabela_distribuicao,)
        ).fetchone()
        if existe is None:
            return [], {}

        if cenario_id is not None:
            linhas = conn.execute(
                "SELECT populacao_id, classe_diametrica, probabilidade, densidade "
                f'FROM "{tabela_distribuicao}" '
                "WHERE cenario_id = ? AND populacao_id BETWEEN ? AND ?",
                (cenario_id, min(ids), max(ids)),
            ).fetchall()
        else:
            linhas = conn.execute(
                "SELECT populacao_id, classe_diametrica, probabilidade, densidade "
                f'FROM "{tabela_distribuicao}" '
                "WHERE populacao_id BETWEEN ? AND ?",
                (min(ids), max(ids)),
            ).fetchall()

        classes = sorted({classe for _, classe, _, _ in linhas})
        n_classes = len(classes)
        colunas = [f"classe_{classe:g}" for classe in classes] + [f"fdp_{classe:g}" for classe in classes]
        indice_da_classe = {classe: i for i, classe in enumerate(classes)}

        valores_por_id = {}
        for populacao_id, classe, probabilidade, densidade in linhas:
            if populacao_id not in valores_por_id:
                valores_por_id[populacao_id] = [None] * (2 * n_classes)
            indice = indice_da_classe[classe]
            valores_por_id[populacao_id][indice] = probabilidade
            valores_por_id[populacao_id][n_classes + indice] = densidade

        valores_por_id = {k: tuple(v) for k, v in valores_por_id.items()}
        return colunas, valores_por_id

    def _ler_valores_evento(self):
        """Lê+valida os 7 campos de "Eventos de manejo" (idade/intensidade
        de Raleio, 1º/2º Desbaste, idade de Corte Raso) — usado tanto por
        gerar() (geração única) quanto por _adicionar_cenario() (grid de
        múltiplos cenários). Devolve um dict com os 7 valores, ou None (já
        com o QMessageBox de erro mostrado) se algo for inválido."""
        try:
            idade_raleio = _ler_idade(self.entry_idade_raleio.text(), "Idade do Raleio")
            idade_desbaste_1 = _ler_idade(self.entry_idade_desbaste_1.text(), "Idade do 1º Desbaste")
            idade_desbaste_2 = _ler_idade(self.entry_idade_desbaste_2.text(), "Idade do 2º Desbaste")
            idade_corte_raso = _ler_idade(self.entry_idade_corte_raso.text(), "Idade do Corte Raso")
        except ValueError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return None

        try:
            intensidade_raleio = self._mapa_intensidade_raleio[self.combo_intensidade_raleio.currentText()]
            intensidade_desbaste_1 = self._mapa_intensidade_desbaste_1[
                self.combo_intensidade_desbaste_1.currentText()]
            intensidade_desbaste_2 = self._mapa_intensidade_desbaste_2[
                self.combo_intensidade_desbaste_2.currentText()]
        except KeyError:
            QMessageBox.warning(self, "Simulação", "Selecione a intensidade de cada intervenção.")
            return None

        return {
            "idade_raleio": idade_raleio, "intensidade_raleio": intensidade_raleio,
            "idade_desbaste_1": idade_desbaste_1, "intensidade_desbaste_1": intensidade_desbaste_1,
            "idade_desbaste_2": idade_desbaste_2, "intensidade_desbaste_2": intensidade_desbaste_2,
            "idade_corte_raso": idade_corte_raso,
        }

    def _ler_configuracao_comum(self):
        """Campos compartilhados por qualquer geração (única ou em lote) —
        mapeamento de colunas da Base IFC + config de distribuição/volume
        por sortimento. Não inclui idade/intensidade (isso é por cenário,
        ver _ler_valores_evento). Devolve o dict, ou None (já com
        QMessageBox de erro mostrado) se faltar coluna obrigatória."""
        coluna_talhao_ifc = self.combo_coluna_talhao.currentText().strip()
        if not coluna_talhao_ifc:
            QMessageBox.warning(self, "Simulação", "Selecione a coluna de talhão da Base IFC ByTalhao.")
            return None

        coluna_fustes_observados = self.combo_coluna_fustes_observados.currentText().strip()
        if not coluna_fustes_observados:
            QMessageBox.warning(
                self, "Simulação", "Selecione a coluna de fustes observados da Base IFC ByTalhao.")
            return None

        return {
            "coluna_talhao_ifc": coluna_talhao_ifc,
            "coluna_fustes_observados": coluna_fustes_observados,
            "coluna_dap_med_observado": self.combo_coluna_dap_med_observado.currentText().strip() or None,
            "coluna_dap_max_observado": self.combo_coluna_dap_max_observado.currentText().strip() or None,
            "coluna_dap_min_observado": self.combo_coluna_dap_min_observado.currentText().strip() or None,
            "coluna_ht_observado": self.combo_coluna_ht_observado.currentText().strip() or None,
            "coluna_vtcc_observado": self.combo_coluna_vtcc_observado.currentText().strip() or None,
            "coluna_cv_dap_observado": self.combo_coluna_cv_dap_observado.currentText().strip() or None,
            "coluna_data_plantio": self.combo_coluna_data_plantio.currentText().strip() or None,
            "coluna_data_medicao": self.combo_coluna_data_medicao.currentText().strip() or None,
            "coluna_forma_distribuicao": self.combo_coluna_forma_distribuicao.currentText().strip() or None,
            "coluna_escala_distribuicao": self.combo_coluna_escala_distribuicao.currentText().strip() or None,
            "usar_tabela_agregacao_volume": self.checkbox_tabela_agregacao.isChecked(),
            "colunas_volume_classes": self._colunas_volume_classes_marcadas(),
            "tipo_agregacao_volume": self._tipos_agregacao_volume_marcadas(),
        }

    def _colunas_volume_classes_marcadas(self):
        # Lê a tabela como está, independente do checkbox "Tabela de
        # agregação" — precisa continuar refletindo a seleção de verdade
        # pra persistir corretamente (ver gerar(), que salva isso sempre,
        # mas só CHAMA calcular_volume_por_sortimento se o checkbox
        # estiver ligado — é lá que "desligado = etapa pulada" acontece,
        # não aqui, senão desligar o checkbox uma vez apagaria a seleção
        # salva na próxima "Gerar simulação").
        return [
            self.tabela_volume_classes.item(linha, 0).text()
            for linha in range(self.tabela_volume_classes.rowCount())
            if self.tabela_volume_classes.item(linha, 0).checkState() == Qt.CheckState.Checked
        ]

    def _tipos_agregacao_volume_marcadas(self):
        return {
            self.tabela_volume_classes.item(linha, 0).text():
                self.tabela_volume_classes.cellWidget(linha, 1).currentText()
            for linha in range(self.tabela_volume_classes.rowCount())
            if self.tabela_volume_classes.item(linha, 0).checkState() == Qt.CheckState.Checked
        }

    def gerar(self):
        configuracao_comum = self._ler_configuracao_comum()
        if configuracao_comum is None:
            return

        valores_evento = self._ler_valores_evento()
        if valores_evento is None:
            return

        try:
            caminho_trabalho = projeto.caminho_trabalho()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return

        if QMessageBox.question(
            self, "Simulação", "Isso substitui a simulação anterior, se houver. Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        configuracao = {**configuracao_comum, **valores_evento}
        self._iniciar_geracao(caminho_trabalho, configuracao)

    # ---------------- cenários (múltiplos) ----------------

    def _carregar_cenarios(self):
        try:
            conn = conectar()
        except RuntimeError:
            self.tabela_cenarios.definir_linhas([])
            return
        try:
            linhas = conn.execute(
                "SELECT id, nome, idade_raleio, intensidade_raleio, idade_desbaste_1, "
                "intensidade_desbaste_1, idade_desbaste_2, intensidade_desbaste_2, "
                "idade_corte_raso, status FROM simulacao_cenarios ORDER BY id"
            ).fetchall()
            if not self._aviso_cenarios_formato_antigo_mostrado:
                self._aviso_cenarios_formato_antigo_mostrado = True
                # Cenários "Gerado" por uma versão anterior do app (tabelas
                # "__cenarioN" antigas, ver core/simulacao.py:ativar_cenario)
                # ficam inertes de propósito — sem migração automática (ver
                # decisão registrada no plano desta mudança). Só avisa, não
                # bloqueia: a linha continua aparecendo na grade, só não
                # funciona em Ativar/Exportar/Ranquear até "Reiniciar".
                tem_formato_antigo = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND "
                    "name LIKE 'simulacao_talhao_idade\\_\\_cenario%' ESCAPE '\\'"
                ).fetchone()[0] > 0
                if tem_formato_antigo:
                    QMessageBox.information(
                        self, "Simulação",
                        "Alguns cenários foram gerados numa versão anterior do app e ainda estão "
                        "no formato antigo — não aparecem em \"Ativar\"/\"Exportar\"/\"Ranquear\" "
                        "até serem regenerados. Rode \"Reiniciar\" pra trazê-los pro formato atual.")
        finally:
            conn.close()
        ids = [str(r[0]) for r in linhas]
        valores = [
            (
                r[1], r[2], _formatar_intensidade(r[3]) if r[3] is not None else "",
                r[4], _formatar_intensidade(r[5]) if r[5] is not None else "",
                r[6], _formatar_intensidade(r[7]) if r[7] is not None else "", r[8], r[9],
            )
            for r in linhas
        ]
        self.tabela_cenarios.definir_linhas(valores, ids=ids)

    def _adicionar_cenario(self):
        nome = self.entry_nome_cenario.text().strip()
        if not nome:
            QMessageBox.warning(self, "Simulação", "Informe um nome pro cenário.")
            return
        valores = self._ler_valores_evento()
        if valores is None:
            return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return
        try:
            conn.execute(
                "INSERT INTO simulacao_cenarios "
                "(nome, idade_raleio, intensidade_raleio, idade_desbaste_1, intensidade_desbaste_1, "
                "idade_desbaste_2, intensidade_desbaste_2, idade_corte_raso) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    nome, valores["idade_raleio"], valores["intensidade_raleio"],
                    valores["idade_desbaste_1"], valores["intensidade_desbaste_1"],
                    valores["idade_desbaste_2"], valores["intensidade_desbaste_2"],
                    valores["idade_corte_raso"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        self.entry_nome_cenario.setText("")
        self._carregar_cenarios()

    def _excluir_cenarios_selecionados(self):
        selecionados = [s for s in self.tabela_cenarios.selecionados() if s != IID_RESUMO]
        if not selecionados:
            return
        pergunta = (
            "Excluir este cenário (e as tabelas geradas por ele)?" if len(selecionados) == 1
            else f"Excluir os {len(selecionados)} cenários selecionados (e as tabelas geradas "
                 "por eles)?")
        if QMessageBox.question(
            self, "Simulação", pergunta,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return
        try:
            ids = [int(s) for s in selecionados]
            self._excluir_cenarios_por_id(conn, ids)
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        self._carregar_cenarios()

    @staticmethod
    def _excluir_cenarios_por_id(conn, ids):
        """Apaga as linhas desse(s) `cenario_id` nas tabelas UNIFICADAS do
        lote (`simulacao_lote_*`, ver core/simulacao.py:persistir_cenario_
        no_lote) + a(s) linha(s) de simulacao_cenarios pros ids dados —
        mesmo DELETE usado por "Excluir selecionado(s)"
        (_excluir_cenarios_selecionados) e por "Gerar grade de cenários"
        (_gerar_grade_cenarios, que troca TODOS os cenários cadastrados
        pelos da grade nova). Não commita — quem chama decide o commit
        (junto com o INSERT seguinte, no caso da grade)."""
        if not ids:
            return
        marcadores = ", ".join("?" for _ in ids)
        # Ordem defensiva (não há FK de verdade entre as tabelas do lote,
        # ao contrário das antigas sufixadas — ver core/simulacao.py:
        # _garantir_tabelas_lote): mesmo assim apaga a distribuição antes
        # da população, mesma convenção de simulacao.ativar_cenario.
        for tabela in (
                simulacao.TABELA_LOTE_DISTRIBUICAO, simulacao.TABELA_LOTE_POPULACAO,
                simulacao.TABELA_LOTE_VOLUME_SORTIMENTO, simulacao.TABELA_LOTE_MIP_CONTINUO,
                simulacao.TABELA_LOTE_MIP_AJUSTE_LOGISTICO):
            existe = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabela,)
            ).fetchone()
            if existe is not None:
                conn.execute(f'DELETE FROM "{tabela}" WHERE cenario_id IN ({marcadores})', ids)
        if simulacao._existe_tabela_cenarios_parquet(conn):
            conn.execute(
                f'DELETE FROM "{simulacao.TABELA_CENARIOS_PARQUET}" '
                f'WHERE cenario_id IN ({marcadores})', ids)
        conn.execute(f"DELETE FROM simulacao_cenarios WHERE id IN ({marcadores})", ids)

    def _ler_faixa_idade(self, entry_min, entry_max, entry_passo, rotulo):
        """Lê+valida os 3 campos de faixa de idade (mín/máx/passo) do
        painel "Grade automática" (_montar_painel_grade_cenarios) pra um
        manejo — mesma validação de _ler_idade (inteiro >= 1) nos 3, mais
        máx >= mín. Devolve a lista de idades da faixa (inclusiva dos dois
        lados). Levanta ValueError (mensagem já pronta pra mostrar) em vez
        de devolver None — _gerar_grade_cenarios lê os 4 manejos em
        sequência dentro de um único try/except."""
        minimo = _ler_idade(entry_min.text(), f"Idade mínima do {rotulo}")
        maximo = _ler_idade(entry_max.text(), f"Idade máxima do {rotulo}")
        passo = _ler_idade(entry_passo.text(), f"Passo de idade do {rotulo}")
        if maximo < minimo:
            raise ValueError(f"Idade máxima do {rotulo} precisa ser maior ou igual à mínima.")
        return list(range(minimo, maximo + 1, passo))

    @staticmethod
    def _ler_faixa_intensidade(combo_min, combo_max, mapa, rotulo):
        """Mesma ideia de _ler_faixa_idade, mas pra intensidade — em vez de
        min/máx/passo digitados, lê o par de combobox (mín/máx, ver
        _montar_painel_grade_cenarios) e devolve a fatia de `mapa` (a MESMA
        lista de intensidades já disponível pro manejo em "Eventos de
        manejo", ver _atualizar_opcoes_formulario) entre eles — o "passo"
        já está embutido nessa lista (vem do "Passo de intensidade"
        configurado em Configurações, não se escolhe de novo aqui)."""
        try:
            minimo = mapa[combo_min.currentText()]
            maximo = mapa[combo_max.currentText()]
        except KeyError:
            raise ValueError(f"Selecione a intensidade mínima e máxima do {rotulo}.")
        if maximo < minimo:
            raise ValueError(f"Intensidade máxima do {rotulo} precisa ser maior ou igual à mínima.")
        return sorted({v for v in mapa.values() if minimo - 1e-9 <= v <= maximo + 1e-9})

    def _ler_evento_grade_opcional(self, entry_min, entry_max, entry_passo, combo_min, combo_max, mapa, rotulo):
        """Faixa de idade+intensidade de um manejo OPCIONAL (Raleio/1º/2º
        Desbaste) no painel "Grade automática" — se os 3 campos de idade
        ficarem em branco, o manejo é tratado como "não feito" em toda a
        grade: devolve `([None], [0.0])` (idade "None" — preenchida
        automaticamente depois pra cada combinação, ver
        _preencher_idades_puladas — e intensidade sempre 0%, ignorando os
        combobox de intensidade mín/máx desse manejo). Do contrário
        (qualquer um dos 3 campos preenchido), funciona igual a antes:
        _ler_faixa_idade + _ler_faixa_intensidade, com os 3 campos de
        idade obrigatórios."""
        if not entry_min.text().strip() and not entry_max.text().strip() and not entry_passo.text().strip():
            return [None], [0.0]
        idades = self._ler_faixa_idade(entry_min, entry_max, entry_passo, rotulo)
        intensidades = self._ler_faixa_intensidade(combo_min, combo_max, mapa, rotulo)
        return idades, intensidades

    def _gerar_grade_cenarios(self):
        """Lê as faixas de idade/intensidade de cada manejo no painel
        "Grade automática" e substitui TODOS os cenários cadastrados pelo
        produto cartesiano delas (itertools.product) — um cenário por
        combinação, nomeado "Cenário 1".."Cenário N". Raleio/1º/2º Desbaste
        são opcionais (deixar a idade em branco = manejo não feito, ver
        _ler_evento_grade_opcional); Corte Raso continua obrigatório. Cada
        combinação passa por _preencher_idades_puladas, que preenche a
        idade dos manejos pulados com um valor sintético (não usado pra
        nada além de manter a ordem — a intensidade 0% já garante que não
        remove nada) e garante Raleio < 1º Desbaste < 2º Desbaste < Corte
        Raso; combinações sem jeito de manter essa ordem são descartadas.
        Só POPULA simulacao_cenarios; "Gerar todos os cenários" (já
        existente, botao_gerar_lote/_gerar_todos_cenarios) continua sendo
        quem roda o pipeline completo de cada linha, e o ranking
        (_montar_secao_ranking_cenarios/_ranquear_cenarios) já funciona
        sobre qualquer cenário "Gerado" independente de como a linha foi
        criada."""
        try:
            idades_raleio, intensidades_raleio = self._ler_evento_grade_opcional(
                self.entry_idade_min_raleio, self.entry_idade_max_raleio, self.entry_idade_passo_raleio,
                self.combo_intensidade_min_raleio, self.combo_intensidade_max_raleio,
                self._mapa_intensidade_raleio, "Raleio")
            idades_desbaste_1, intensidades_desbaste_1 = self._ler_evento_grade_opcional(
                self.entry_idade_min_desbaste_1, self.entry_idade_max_desbaste_1,
                self.entry_idade_passo_desbaste_1, self.combo_intensidade_min_desbaste_1,
                self.combo_intensidade_max_desbaste_1, self._mapa_intensidade_desbaste_1, "1º Desbaste")
            idades_desbaste_2, intensidades_desbaste_2 = self._ler_evento_grade_opcional(
                self.entry_idade_min_desbaste_2, self.entry_idade_max_desbaste_2,
                self.entry_idade_passo_desbaste_2, self.combo_intensidade_min_desbaste_2,
                self.combo_intensidade_max_desbaste_2, self._mapa_intensidade_desbaste_2, "2º Desbaste")
            idades_corte_raso = self._ler_faixa_idade(
                self.entry_idade_min_corte_raso, self.entry_idade_max_corte_raso,
                self.entry_idade_passo_corte_raso, "Corte Raso")
        except ValueError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return

        # Preenche a idade dos manejos pulados (None) e só mantém
        # combinações com a ordem cronológica correta — Raleio < 1º
        # Desbaste < 2º Desbaste < Corte Raso (ver _preencher_idades_
        # puladas) — cada faixa é lida independente, então o produto
        # cartesiano puro geraria também combinações sem sentido
        # silvicultural (ex: Corte Raso mais novo que o 2º Desbaste).
        combinacoes = []
        for idade_raleio, int_raleio, idade_d1, int_d1, idade_d2, int_d2, idade_corte in itertools.product(
                idades_raleio, intensidades_raleio, idades_desbaste_1, intensidades_desbaste_1,
                idades_desbaste_2, intensidades_desbaste_2, idades_corte_raso):
            preenchidas = _preencher_idades_puladas({
                "raleio": idade_raleio, "desbaste_1": idade_d1, "desbaste_2": idade_d2,
                "corte_raso": idade_corte,
            })
            if preenchidas is None:
                continue
            combinacoes.append((
                preenchidas["raleio"], int_raleio, preenchidas["desbaste_1"], int_d1,
                preenchidas["desbaste_2"], int_d2, preenchidas["corte_raso"],
            ))
        total = len(combinacoes)
        if total == 0:
            QMessageBox.warning(
                self, "Simulação",
                "Nenhuma combinação encontrada com essas faixas — lembre que a idade precisa "
                "crescer na ordem Raleio < 1º Desbaste < 2º Desbaste < Corte Raso, então as "
                "faixas de cada manejo precisam deixar espaço pra isso.")
            return
        aviso_extra = ""
        if total > _LIMITE_AVISO_GRADE_CENARIOS:
            aviso_extra = (
                "\n\nSão bastante cenários — \"Gerar pendentes\" (etapa seguinte) roda o "
                "pipeline completo pra cada um, um por um, e pode demorar.")
        if QMessageBox.question(
            self, "Simulação",
            f"Isso substitui os cenários cadastrados atualmente por {total:,} novo(s) cenário(s) "
            f"(\"Cenário 1\" a \"Cenário {total}\"), um pra cada combinação de idade/intensidade "
            f"dentro das faixas informadas.{aviso_extra}\n\nContinuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return
        try:
            ids_atuais = [r[0] for r in conn.execute("SELECT id FROM simulacao_cenarios").fetchall()]
            self._excluir_cenarios_por_id(conn, ids_atuais)
            for numero, combinacao in enumerate(combinacoes, start=1):
                (idade_raleio, intensidade_raleio, idade_desbaste_1, intensidade_desbaste_1,
                 idade_desbaste_2, intensidade_desbaste_2, idade_corte_raso) = combinacao
                conn.execute(
                    "INSERT INTO simulacao_cenarios "
                    "(nome, idade_raleio, intensidade_raleio, idade_desbaste_1, "
                    "intensidade_desbaste_1, idade_desbaste_2, intensidade_desbaste_2, "
                    "idade_corte_raso) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"Cenário {numero}", idade_raleio, intensidade_raleio, idade_desbaste_1,
                        intensidade_desbaste_1, idade_desbaste_2, intensidade_desbaste_2,
                        idade_corte_raso,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        self._carregar_cenarios()
        self._definir_status(
            self.label_status_cenarios,
            f"{total:,} cenário(s) gerado(s) na grade — use \"Gerar pendentes\" pra rodar "
            "a simulação de cada um.", "sucesso")

    def _abrir_graficos_cenario_duplo_clique(self, indice):
        if not indice.isValid():
            return
        linhas = self.tabela_cenarios.linhas_visiveis()
        if indice.row() >= len(linhas):
            return
        cenario_id, _valores, eh_resumo = linhas[indice.row()]
        if eh_resumo or cenario_id == IID_RESUMO:
            return
        self.tabela_cenarios.limpar_selecao()
        self.tabela_cenarios.selecionar_id(cenario_id)
        self._ativar_cenario_selecionado(abrir_graficos=True)

    def _ativar_cenario_selecionado(self, _checked=False, abrir_graficos=False):
        selecionados = [s for s in self.tabela_cenarios.selecionados() if s != IID_RESUMO]
        if len(selecionados) != 1:
            QMessageBox.warning(self, "Simulação", "Selecione exatamente 1 cenário pra ativar.")
            return
        cenario_id = int(selecionados[0])

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return
        try:
            linha = conn.execute(
                "SELECT nome, status FROM simulacao_cenarios WHERE id = ?", (cenario_id,)
            ).fetchone()
            if linha is None:
                QMessageBox.warning(self, "Simulação", "Cenário não encontrado.")
                return
            nome, status = linha
            if status != "Gerado":
                QMessageBox.warning(
                    self, "Simulação",
                    f"O cenário \"{nome}\" ainda não foi gerado (status: {status}) — rode "
                    "\"Gerar pendentes\" antes de ativar.")
                return
            try:
                simulacao.ativar_cenario(conn, cenario_id)
            except ValueError as e:
                QMessageBox.warning(self, "Simulação", str(e))
                return
            # As tabelas do lote guardam um retrato de quando esse cenário
            # foi gerado — se construtores foram criados/alterados DEPOIS
            # disso (ex: um nó financeiro novo), ativar um cenário mais
            # antigo deixaria as colunas novas de fora até uma nova "Gerar
            # simulação"/"Gerar todos os cenários". Reaplicar aqui (mesma
            # chamada que _gerar_uma_simulacao já faz na geração) garante
            # que o cenário recém-ativado sempre reflita os construtores
            # salvos atuais, não o que existia quando ele foi gerado.
            resumo_construtores = construtores.aplicar_construtores_salvos(
                conn, simulacao.TABELA_POPULACAO)
        finally:
            conn.close()
        projeto.sincronizar()
        self.recarregar_lista()

        texto_construtores = ""
        if resumo_construtores["executados"] or resumo_construtores["falhas"]:
            texto_construtores = (
                f"\n\nConstrutores de Variáveis reaplicados: {resumo_construtores['executados']:,}")
            if resumo_construtores["falhas"]:
                texto_construtores += "\nPendências:\n" + "\n".join(resumo_construtores["falhas"])
        if abrir_graficos:
            self._abrir_janela_graficos()
        else:
            QMessageBox.information(self, "Simulação", f"Cenário \"{nome}\" ativado.{texto_construtores}")

    def _ranquear_cenarios(self):
        """Ranking dos cenários já gerados pela soma das colunas
        selecionadas no QListWidget (ver _montar_secao_ranking_cenarios/
        simulacao.ranquear_cenarios) — não precisa ativar nenhum cenário
        antes, lê a tabela sufixada de cada um direto."""
        textos_selecionados = [item.text() for item in self.lista_kpi_colunas.selectedItems()]
        if not textos_selecionados:
            QMessageBox.warning(self, "Simulação", "Selecione ao menos uma coluna pro KPI.")
            return
        colunas = [self._opcoes_kpi_coluna[texto][0] for texto in textos_selecionados]
        decrescente = self.combo_kpi_direcao.currentText() != "Menor é melhor"

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return
        try:
            ranking = simulacao.ranquear_cenarios(conn, colunas, decrescente)
        finally:
            conn.close()

        if not ranking:
            self.tabela_ranking_cenarios.definir_linhas([])
            QMessageBox.information(
                self, "Simulação",
                "Nenhum cenário com status \"Gerado\" ainda — rode \"Gerar pendentes\" "
                "antes de ranquear.")
            return

        ids = [str(item["id"]) for item in ranking]
        linhas = [
            (item["posicao"], item["nome"], "—" if item["valor"] is None else item["valor"])
            for item in ranking
        ]
        self.tabela_ranking_cenarios.definir_linhas(linhas, ids=ids)

    def _ao_alternar_top_n_talhao(self, marcado):
        self.spin_top_n_talhao.setEnabled(marcado)

    def _ler_configuracao_ranking_por_talhao(self):
        """(coluna_chave, ranking_por_chave) a partir do KPI/direção
        escolhidos (mesmos widgets do ranking geral, ver
        _montar_secao_ranking_cenarios) e do checkbox/spinbox "Mostrar/
        exportar só os N melhores" — None se já avisou o usuário (falta
        selecionar KPI, projeto fechado, ou coluna de talhão não
        configurada). Compartilhado por _ranquear_cenarios_por_talhao
        (mostra na tela) e exportar_ranking_por_talhao (exporta o MESMO
        recorte, nunca precisa re-clicar "Ranquear por talhão" antes)."""
        textos_selecionados = [item.text() for item in self.lista_kpi_colunas.selectedItems()]
        if not textos_selecionados:
            QMessageBox.warning(self, "Simulação", "Selecione ao menos uma coluna pro KPI.")
            return None
        colunas = [self._opcoes_kpi_coluna[texto][0] for texto in textos_selecionados]
        decrescente = self.combo_kpi_direcao.currentText() != "Menor é melhor"
        top_n = self.spin_top_n_talhao.value() if self.checkbox_top_n_talhao.isChecked() else None

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return None
        try:
            coluna_chave = simulacao.obter_coluna_talhao(conn)
            if not coluna_chave:
                QMessageBox.warning(self, "Simulação", "Coluna de talhão não configurada.")
                return None
            ranking_por_chave = simulacao.ranquear_cenarios_por_chave(
                conn, coluna_chave, colunas, decrescente, top_n)
        finally:
            conn.close()
        return coluna_chave, ranking_por_chave

    def _ranquear_cenarios_por_talhao(self):
        """Ranking por talhão — um ranking independente de cenários POR
        TALHÃO (ver simulacao.ranquear_cenarios_por_chave), não um KPI só
        somado entre todos (isso é _ranquear_cenarios, a tabela acima)."""
        resultado = self._ler_configuracao_ranking_por_talhao()
        if resultado is None:
            return
        _coluna_chave, ranking_por_chave = resultado

        if not ranking_por_chave:
            self.tabela_ranking_por_talhao.definir_linhas([])
            QMessageBox.information(
                self, "Simulação",
                "Nenhum cenário com status \"Gerado\" tem a(s) coluna(s) do KPI ainda — rode "
                "\"Gerar pendentes\" antes de ranquear.")
            return

        ids = []
        linhas = []
        for chave in sorted(ranking_por_chave.keys(), key=str):
            for item in ranking_por_chave[chave]:
                ids.append(f"{chave}::{item['id']}")
                linhas.append((chave, item["posicao"], item["nome"], item["valor"]))
        self.tabela_ranking_por_talhao.definir_linhas(linhas, ids=ids)

    def exportar_ranking_por_talhao(self):
        """Exporta os dados completos (mesmas 2 abas de exportar_todos_
        cenarios: "Simulação" e "Volume por Sortimento", mesmas colunas de
        metadados de cenário) só dos cenários que aparecem no ranking por
        talhão (mesmo recorte de tabela_ranking_por_talhao — reusa
        _ler_configuracao_ranking_por_talhao, não precisa "Ranquear por
        talhão" ter sido clicado antes) — cada talhão só traz as linhas
        dos cenários no SEU PRÓPRIO ranking; um cenário que apareça no
        ranking de 2+ talhões entra uma vez pra cada um, só com as linhas
        daquele talhão (não as dos outros). Acrescenta uma coluna
        "posicao_ranking_talhao" (a posição daquele talhão naquele
        cenário) nas duas abas, pra rastrear de onde cada linha veio."""
        resultado = self._ler_configuracao_ranking_por_talhao()
        if resultado is None:
            return
        coluna_chave, ranking_por_chave = resultado
        if not ranking_por_chave:
            QMessageBox.warning(
                self, "Simulação",
                "Nenhum cenário com status \"Gerado\" tem a(s) coluna(s) do KPI ainda — rode "
                "\"Gerar pendentes\" antes de exportar.")
            return

        # Inverte {talhao: [itens]} pra {cenario_id: {talhao: posicao}} —
        # é por cenário que os dados são lidos (uma tabela sufixada por
        # cenário), então é por cenário que precisamos saber quais
        # talhões filtrar dali.
        talhoes_por_cenario = {}
        for chave, itens in ranking_por_chave.items():
            for item in itens:
                talhoes_por_cenario.setdefault(item["id"], {})[chave] = item["posicao"]

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return

        try:
            marcadores = ", ".join("?" for _ in talhoes_por_cenario)
            cenarios = conn.execute(
                "SELECT id, nome, idade_raleio, intensidade_raleio, idade_desbaste_1, "
                "intensidade_desbaste_1, idade_desbaste_2, intensidade_desbaste_2, idade_corte_raso "
                f"FROM simulacao_cenarios WHERE id IN ({marcadores}) ORDER BY id",
                list(talhoes_por_cenario.keys()),
            ).fetchall()

            blocos_populacao = []
            blocos_sortimento = []
            cenarios_sem_dado = []
            for cenario_id, nome, *valores_manejo in cenarios:
                df_populacao, df_sortimento = self._carregar_dados_geracao(
                    conn, cenario_id=cenario_id)
                if df_populacao is None:
                    cenarios_sem_dado.append(nome)
                    continue

                posicoes_por_talhao = talhoes_por_cenario[cenario_id]
                campos_metadados = [("cenario", nome)] + list(zip(_CAMPOS_MANEJO_CENARIO, valores_manejo))

                df_populacao_filtrado = df_populacao[
                    df_populacao[coluna_chave].isin(posicoes_por_talhao)].copy()
                df_populacao_filtrado.insert(
                    0, "posicao_ranking_talhao",
                    df_populacao_filtrado[coluna_chave].map(posicoes_por_talhao))
                blocos_populacao.append(
                    (nome, self._inserir_metadados_cenario(df_populacao_filtrado, campos_metadados)))

                if df_sortimento is not None and coluna_chave in df_sortimento.columns:
                    df_sortimento_filtrado = df_sortimento[
                        df_sortimento[coluna_chave].isin(posicoes_por_talhao)].copy()
                    df_sortimento_filtrado.insert(
                        0, "posicao_ranking_talhao",
                        df_sortimento_filtrado[coluna_chave].map(posicoes_por_talhao))
                    blocos_sortimento.append(
                        (nome, self._inserir_metadados_cenario(df_sortimento_filtrado, campos_metadados)))
        finally:
            conn.close()

        if not blocos_populacao:
            QMessageBox.warning(
                self, "Simulação",
                "Nenhum cenário do ranking tem dado pra exportar (tabelas ausentes — gere de "
                "novo).")
            return

        paginas_populacao, excedidos_populacao = _particionar_paginas_excel(blocos_populacao)
        paginas_sortimento, excedidos_sortimento = _particionar_paginas_excel(blocos_sortimento)

        caminho, _ = QFileDialog.getSaveFileName(
            self, "Exportar Ranking por Talhão", "", "Planilha Excel (*.xlsx)")
        if not caminho:
            return
        if not caminho.endswith(".xlsx"):
            caminho += ".xlsx"

        try:
            with pd.ExcelWriter(caminho, **_ENGINE_KWARGS_EXCEL) as writer:
                _escrever_paginas_excel(writer, paginas_populacao, "Simulação")
                _escrever_paginas_excel(writer, paginas_sortimento, "Volume por Sortimento")
        except Exception as e:
            QMessageBox.critical(self, "Simulação", f"Não foi possível exportar:\n{e}")
            return

        total_linhas_populacao = sum(len(df) for dfs in paginas_populacao for df in dfs)
        partes = [
            f"{len(talhoes_por_cenario):,} cenário(s) no ranking, {total_linhas_populacao:,} "
            "linha(s) de simulação" + (
                f" em {len(paginas_populacao)} aba(s)" if len(paginas_populacao) > 1 else "")
        ]
        if blocos_sortimento:
            total_linhas_sortimento = sum(len(df) for dfs in paginas_sortimento for df in dfs)
            partes.append(
                f"{total_linhas_sortimento:,} linha(s) de volume por sortimento"
                + (f" em {len(paginas_sortimento)} aba(s)" if len(paginas_sortimento) > 1 else ""))
        texto = "Exportado: " + "; ".join(partes) + f"\nem\n{caminho}"

        avisos = []
        if cenarios_sem_dado:
            avisos.append(
                "Sem dado pra exportar (tabelas do cenário não existem, apesar do status "
                "\"Gerado\"): " + ", ".join(cenarios_sem_dado))
        if excedidos_populacao:
            avisos.append(
                "Cenário(s) com mais linhas de simulação do que cabe numa aba do Excel "
                f"({LIMITE_LINHAS_EXCEL:,}) — como um cenário nunca é dividido entre duas abas, "
                "ficaram de fora: " + ", ".join(excedidos_populacao))
        if excedidos_sortimento:
            avisos.append(
                "Cenário(s) com mais linhas de volume por sortimento do que cabe numa aba — "
                "ficaram de fora: " + ", ".join(excedidos_sortimento))

        if avisos:
            QMessageBox.warning(self, "Simulação", texto + "\n\n" + "\n\n".join(avisos))
        else:
            QMessageBox.information(self, "Simulação", texto)

    def _gerar_todos_cenarios(self, somente_pendentes=True):
        """Botão "Gerar pendentes" (`somente_pendentes=True`, o normal —
        roda só quem tem status diferente de "Gerado", continuando de
        onde um "Parar" anterior deixou) ou "Reiniciar" (`False` — roda
        TODOS de novo, inclusive os já "Gerado", mesmo comportamento que
        este método tinha antes de existir "Parar"/"Gerar pendentes")."""
        configuracao_comum = self._ler_configuracao_comum()
        if configuracao_comum is None:
            return

        try:
            caminho_trabalho = projeto.caminho_trabalho()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação", str(e))
            return
        try:
            sql = (
                "SELECT id, nome, idade_raleio, intensidade_raleio, idade_desbaste_1, "
                "intensidade_desbaste_1, idade_desbaste_2, intensidade_desbaste_2, "
                "idade_corte_raso FROM simulacao_cenarios"
            )
            if somente_pendentes:
                sql += " WHERE status != 'Gerado'"
            linhas = conn.execute(sql + " ORDER BY id").fetchall()
        finally:
            conn.close()

        if not linhas:
            if somente_pendentes:
                QMessageBox.information(
                    self, "Simulação",
                    "Nenhum cenário pendente — todos já foram gerados. Use \"Reiniciar\" pra "
                    "gerar tudo de novo.")
            else:
                QMessageBox.warning(self, "Simulação", "Nenhum cenário cadastrado — adicione ao menos um.")
            return

        cenarios = [
            {
                "id": r[0], "nome": r[1], "idade_raleio": r[2], "intensidade_raleio": r[3],
                "idade_desbaste_1": r[4], "intensidade_desbaste_1": r[5],
                "idade_desbaste_2": r[6], "intensidade_desbaste_2": r[7], "idade_corte_raso": r[8],
            }
            for r in linhas
        ]

        pergunta = (
            f"Gerar os {len(cenarios)} cenário(s) pendente(s)?"
            if somente_pendentes else
            f"Gerar {len(cenarios)} cenário(s)? Isso substitui o resultado anterior de cada um, "
            "se houver."
        )
        if QMessageBox.question(
            self, "Simulação", pergunta,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        # "Reiniciar" (somente_pendentes=False): volta o status de TODO
        # cenário incluído nesta rodada pra "Pendente" ANTES de começar —
        # sem isso, um cenário já "Gerado" de uma rodada anterior
        # continuava mostrando "Gerado" na tabela até chegar a vez DELE
        # no laço (_ThreadGerarLote.run só regrava o status de cada um
        # quando o processa); se o usuário "Parar" antes disso, os que
        # ainda não foram alcançados ficavam com um "Gerado" enganoso —
        # na verdade não regenerado nesta rodada, resultado de uma rodada
        # anterior que pode nem bater mais com a configuração atual.
        # "Gerar pendentes" não precisa disso: por definição, todo
        # cenário na lista já tem status != "Gerado".
        if not somente_pendentes:
            try:
                conn = conectar()
            except RuntimeError as e:
                QMessageBox.warning(self, "Simulação", str(e))
                return
            try:
                conn.executemany(
                    "UPDATE simulacao_cenarios SET status = 'Pendente' WHERE id = ?",
                    [(cenario["id"],) for cenario in cenarios],
                )
                conn.commit()
            finally:
                conn.close()
            self._carregar_cenarios()

        self._iniciar_geracao_lote(caminho_trabalho, configuracao_comum, cenarios)

    def _iniciar_geracao_lote(self, caminho_trabalho, configuracao_comum, cenarios):
        # Resolve qualquer sincronização pendente ANTES de começar — o lote
        # pode levar minutos escrevendo pesado no banco de trabalho; deixar
        # uma sincronização agendada disparar no meio (mesma
        # Connection.backup(), mesmo arquivo) arrisca "database is locked"
        # se o backup demorar mais que o busy_timeout (mesmo motivo de
        # weibull.py:_ao_clicar_ajustar_simulacao).
        projeto.finalizar_sincronizacao_pendente()

        self._gerando_lote = True
        self.botao_gerar_lote.setEnabled(False)
        self.botao_reiniciar_lote.setEnabled(False)
        self.botao_parar_lote.setEnabled(True)

        janela = self.window()
        if hasattr(janela, "travar_navegacao"):
            janela.travar_navegacao(True)

        total = len(cenarios)
        self._definir_status(self.label_status_cenarios, f"Gerando cenário 0/{total}...", "neutro")
        self.progressbar_cenarios.setRange(0, total)
        self.progressbar_cenarios.setValue(0)
        self.progressbar_cenarios.setVisible(True)

        thread = _ThreadGerarLote(
            caminho_trabalho, configuracao_comum, cenarios,
            calcular_mip=self.checkbox_calcular_mip_lote.isChecked(), parent=self)
        self._thread_gerar_lote = thread
        thread.progresso.connect(self._ao_progredir_geracao_lote)
        thread.concluido.connect(lambda resumo: self._finalizar_geracao_lote(resumo=resumo))
        thread.falhou.connect(lambda erro: self._finalizar_geracao_lote(erro=erro))
        thread.start()

    def _ao_progredir_geracao_lote(self, numero, total, nome):
        self._definir_status(
            self.label_status_cenarios, f"Gerando cenário {numero}/{total}: {nome}...", "neutro")
        self.progressbar_cenarios.setRange(0, total)
        self.progressbar_cenarios.setValue(numero)

    def _parar_geracao_lote(self):
        """Botão "Parar" — só pede a parada (ver _ThreadGerarLote.
        solicitar_parada); quem efetivamente encerra a thread e libera os
        botões é _finalizar_geracao_lote, quando o sinal `concluido`
        chegar (só depois do cenário em andamento terminar de commitar).
        Desabilita a si mesmo na hora pra evitar clique duplo enquanto
        espera."""
        if self._thread_gerar_lote is not None:
            self._thread_gerar_lote.solicitar_parada()
        self.botao_parar_lote.setEnabled(False)
        self._definir_status(
            self.label_status_cenarios, "Parando após o cenário atual...", "neutro")

    def _finalizar_geracao_lote(self, resumo=None, erro=None):
        self._gerando_lote = False
        self._thread_gerar_lote = None
        self.progressbar_cenarios.setVisible(False)
        self.botao_gerar_lote.setEnabled(True)
        self.botao_reiniciar_lote.setEnabled(True)
        self.botao_parar_lote.setEnabled(False)

        janela = self.window()
        if hasattr(janela, "travar_navegacao"):
            janela.travar_navegacao(False)

        if erro is not None:
            self._definir_status(self.label_status_cenarios, "", "neutro")
            QMessageBox.critical(self, "Simulação", f"Falha ao gerar os cenários:\n{erro}")
            self.recarregar_lista()
            return

        projeto.sincronizar()
        self.recarregar_lista()

        interrompido = resumo.get("interrompido", False)
        parou_por_disco = resumo.get("parou_por_disco", False)
        pendentes = resumo["total"] - resumo["gerados"] - len(resumo["com_erro"])
        if parou_por_disco:
            texto = (
                f"Parado por falta de espaço em disco: {resumo['gerados']} de {resumo['total']} "
                f"cenário(s) gerado(s). {pendentes} ainda pendente(s) — libere espaço no disco e "
                "use \"Gerar pendentes\" pra continuar.")
        elif interrompido:
            texto = (
                f"Parado: {resumo['gerados']} de {resumo['total']} cenário(s) gerado(s). "
                f"{pendentes} ainda pendente(s) — use \"Gerar pendentes\" pra continuar.")
        else:
            texto = f"{resumo['gerados']} de {resumo['total']} cenário(s) gerado(s) com sucesso."
        if resumo["com_erro"]:
            detalhes = "\n".join(f"- {nome}: {msg}" for nome, msg in resumo["com_erro"])
            texto += f"\n\n{len(resumo['com_erro'])} com erro:\n{detalhes}"
            self._definir_status(self.label_status_cenarios, "Concluído com erros.", "aviso")
        elif parou_por_disco:
            self._definir_status(self.label_status_cenarios, "Parado (disco cheio).", "aviso")
        elif interrompido:
            self._definir_status(self.label_status_cenarios, "Parado.", "aviso")
        else:
            self._definir_status(self.label_status_cenarios, "Concluído.", "sucesso")

        # Perfil do caso (nº de talhões/idade máxima/classes diamétricas/
        # construtores ativos/cenários/modo — ver _ThreadGerarLote.run) —
        # contexto pra interpretar os tempos abaixo (o mesmo tempo total
        # significa coisas bem diferentes num caso pequeno vs. num caso
        # grande). Ausente (None) só deveria acontecer num erro anterior à
        # montagem do perfil — não trava o resto do diálogo.
        perfil = resumo.get("perfil_caso") or {}
        if perfil:
            idade_txt = (
                f"{perfil['idade_maxima_manejo']:.0f}"
                if perfil.get("idade_maxima_manejo") is not None else "—")
            texto += (
                f"\n\nPerfil do caso: {perfil.get('n_talhoes', '—')} talhão(ões), "
                f"idade máxima {idade_txt}, {perfil.get('n_classes_diametricas', '—')} "
                f"classe(s) diamétrica(s), {perfil.get('n_construtores_ativos', '—')} "
                f"construtor(es) ativo(s), {perfil.get('n_cenarios', '—')} cenário(s), "
                f"modo {perfil.get('modo', '—')}"
                + (f" ({perfil['n_workers']} workers)" if perfil.get("modo") == "paralelo" else "")
                + ".")

        # Diagnóstico de onde o lote gastou tempo — soma de todos os
        # cenários gerados com sucesso (ver _ThreadGerarLote.run), maior
        # etapa primeiro, com a média por cenário ao lado (a soma sozinha
        # é difícil de interpretar num lote de milhares de cenários).
        # Ajuda a decidir o que otimizar (ex: se "Construtores de
        # Variáveis" domina o total, o gargalo tá lá, não na geração da
        # população em si; "Fila/IPC (workers)" domina só quando o
        # round-trip entre processos pesa mais que o cálculo em si — ver
        # _executar_lote_paralelo).
        tempos_totais = resumo.get("tempos_totais") or {}
        gerados = resumo.get("gerados") or 0
        partes_tempo = []
        if sum(tempos_totais.values()) >= 1.0:
            rotulos_etapa = {
                "populacao": "Geração da população", "construtores": "Construtores de Variáveis",
                "distribuicao": "Distribuição diamétrica", "volume_sortimento": "Volume por sortimento",
                "mip_continuo": "MIP contínuo", "gravacao_final": "Gravação final",
                "fila_despacho": "Fila até o worker começar",
                "preaquecimento_afilamento": "Pré-aquecimento do afilamento (uma vez no lote)",
                "gravacao_parquet_worker": "Gravação Parquet nos workers",
                "ipc_retorno": "Retorno do manifesto (worker→principal)",
                "gravacao_blob_parquet": "Persistência do Parquet no projeto",
                "materializacao_sqlite_mip": "Materialização SQLite para MIP",
            }
            partes_tempo = [
                f"{rotulos_etapa.get(etapa, etapa)}: {segundos:.1f}s"
                + (f" (méd. {segundos / gerados:.2f}s/cenário)"
                   if gerados and etapa != "preaquecimento_afilamento" else "")
                for etapa, segundos in sorted(tempos_totais.items(), key=lambda item: item[1], reverse=True)
                if segundos >= 0.1
            ]
            texto += (
                f"\n\nTempo total do lote: {sum(tempos_totais.values()):.1f}s — por etapa:\n"
                + "\n".join(partes_tempo))

        # Detalhe por construtor (só existe entrada aqui se ELE sozinho
        # passou de 1s num cenário — ver _ThreadGerarLote.run) — verboso
        # demais pro diálogo (um lote grande facilmente passa de centenas
        # de linhas), então vai pro arquivo de log em vez de aparecer
        # aqui; só uma linha apontando onde procurar quando existir algo.
        # O log é gravado sempre que há perfil/tempos pra registrar, não só
        # quando algum construtor individual foi lento — é o artefato
        # colável inteiro pra diagnóstico (ver _gravar_log_construtores).
        tempos_construtores = resumo.get("tempos_construtores") or []
        if tempos_construtores or perfil:
            caminho_log = _gravar_log_construtores(
                tempos_construtores, perfil_caso=perfil, resumo_tempos=partes_tempo)
            if caminho_log is not None and tempos_construtores:
                texto += f"\n\nDetalhe por construtor (mais lentos) em:\n{caminho_log}"

        # Texto selecionável/copiável (Ctrl+C) — QMessageBox.information()
        # cria o texto como label comum, sem seleção por mouse; usuário
        # precisa colar esse resumo (talhões/tempos por etapa) em outro
        # lugar pra diagnóstico, então monta a caixa manualmente pra poder
        # ligar setTextInteractionFlags antes de exibir.
        caixa = QMessageBox(self)
        caixa.setIcon(QMessageBox.Icon.Information)
        caixa.setWindowTitle("Simulação")
        caixa.setText(texto)
        caixa.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        caixa.exec()

    def _iniciar_geracao(self, caminho_trabalho, configuracao):
        # Mesmo motivo de _iniciar_geracao_lote acima — uma geração única
        # também pode escrever pesado (ver "gravação no banco" nos tempos
        # por construtor) e correr com uma sincronização pendente arrisca
        # "database is locked".
        projeto.finalizar_sincronizacao_pendente()

        self._gerando = True
        self.botao_gerar.setEnabled(False)

        janela = self.window()
        if hasattr(janela, "travar_navegacao"):
            janela.travar_navegacao(True)

        self._definir_status(self.label_status, "Gerando simulação...", "neutro")
        self.progressbar.setRange(0, 0)
        self.progressbar.setVisible(True)

        thread = _ThreadGerarSimulacao(caminho_trabalho, configuracao, parent=self)
        self._thread_gerar = thread
        thread.concluido.connect(lambda resultado: self._finalizar_geracao(resultado=resultado))
        thread.falhou.connect(self._ao_falhar_geracao)
        thread.start()

    def _ao_falhar_geracao(self, erro):
        # Distingue ValueError (erro de validação — mensagem já pronta pra
        # mostrar direto, ver core/simulacao.py:gerar_populacao) de
        # qualquer outra exceção (erro inesperado, mostrado com contexto
        # extra) — mesma distinção que o "validacao"/"erro" da fila do
        # original fazia.
        self._finalizar_geracao(erro=erro, erro_validacao=isinstance(erro, ValueError))

    def _finalizar_geracao(self, resultado=None, erro=None, erro_validacao=False):
        self._gerando = False
        self._thread_gerar = None
        self.progressbar.setVisible(False)

        janela = self.window()
        if hasattr(janela, "travar_navegacao"):
            janela.travar_navegacao(False)

        if erro is not None:
            self.label_status.setText("")
            if erro_validacao:
                QMessageBox.warning(self, "Simulação", str(erro))
            else:
                QMessageBox.critical(self, "Simulação", f"Falha ao gerar a simulação:\n{erro}")
            self.recarregar_lista()
            return

        projeto.sincronizar()
        self.recarregar_lista()

        if resultado["aviso_distribuicao"]:
            QMessageBox.warning(self, "Simulação", resultado["aviso_distribuicao"])

        if resultado["aviso_volume_sortimento"]:
            QMessageBox.warning(self, "Simulação", resultado["aviso_volume_sortimento"])

        if resultado["aviso_mip_continuo"]:
            QMessageBox.warning(self, "Simulação", resultado["aviso_mip_continuo"])

        resultado_volume_sortimento = resultado["resultado_volume_sortimento"]
        texto_volume_sortimento = ""
        if resultado_volume_sortimento["executado"]:
            n_campos = resultado_volume_sortimento.get("n_campos", 1)
            sufixo_campos = f" × {n_campos} campo(s)" if n_campos > 1 else ""
            texto_volume_sortimento = (
                f"\n\nVolume por sortimento: {resultado_volume_sortimento['linhas']:,} linha(s), "
                f"{resultado_volume_sortimento.get('n_sortimentos', len(resultado_volume_sortimento['colunas_sortimento'])):,} "
                f"sortimento(s){sufixo_campos}.")

        resumo_construtores = resultado["resumo_construtores"]
        texto_construtores = ""
        if resumo_construtores["executados"] or resumo_construtores["falhas"]:
            texto_construtores = (
                f"\n\nConstrutores de Variáveis reaplicados: {resumo_construtores['executados']:,}")
            if resumo_construtores["falhas"]:
                texto_construtores += "\nPendências:\n" + "\n".join(resumo_construtores["falhas"])
            # Só aparece se algum construtor passou de 1s (ver
            # core/construtores.py:_LIMIAR_TEMPO_RELATADO) — diagnóstico de
            # performance pra "Gerar simulação" lenta, mesma ideia do
            # Construtor de Variáveis (Prévia/Salvar construtor).
            if resumo_construtores.get("tempos"):
                texto_construtores += "\nTempos:\n" + "\n".join(resumo_construtores["tempos"])

        QMessageBox.information(
            self, "Simulação",
            "Simulação gerada.\n\n"
            f"Talhões: {resultado['talhoes']:,}\n"
            f"Talhões com ajuste Weibull \"Por Talhão\": {resultado['talhoes_com_weibull']:,}\n"
            f"Talhões sem ajuste Weibull \"Por Talhão\": {resultado['talhoes_sem_weibull']:,}\n"
            f"Talhões sem fuste observado (coluna não numérica/vazia): "
            f"{resultado['talhoes_sem_fustes_observado']:,}\n"
            f"Talhões com ajuste Weibull \"Por Simulação\" após raleio: "
            f"{resultado['talhoes_com_weibull_apos_raleio']:,}\n"
            f"...após 1º desbaste: {resultado['talhoes_com_weibull_apos_desbaste_1']:,}\n"
            f"...após 2º desbaste: {resultado['talhoes_com_weibull_apos_desbaste_2']:,}\n\n"
            f"Manejo pulado pela guarda de fustes/ha mínimo — "
            f"Raleio: {resultado['talhoes_manejo_pulado_apos_raleio']:,}, "
            f"1º Desbaste: {resultado['talhoes_manejo_pulado_apos_desbaste_1']:,}, "
            f"2º Desbaste: {resultado['talhoes_manejo_pulado_apos_desbaste_2']:,}\n\n"
            f"Idades simuladas: {resultado['idades']:,} (1 a {resultado['idade_maxima_manejo']:g})\n"
            f"Linhas geradas: {resultado['linhas_geradas']:,}\n\n"
            f"Classes diamétricas: {resultado['classes_diametricas']:,}\n"
            f"Linhas de distribuição geradas: {resultado['linhas_distribuicao_geradas']:,}\n"
            f"Combinações talhão/idade sem distribuição (sem forma/escala em vigor): "
            f"{resultado['combinacoes_sem_distribuicao']:,}"
            f"{texto_volume_sortimento}"
            f"{texto_construtores}")


def _gerar_uma_simulacao(
    conn, configuracao, sufixo_tabela="", contexto_lote=None, calcular_mip=True,
    cenario_id=None, proximo_id_populacao=None,
):
    """Sequência completa de UMA geração — gerar_populacao + reaplicar
    construtores salvos + distribuição/volume por sortimento opcionais —
    reaproveitada tanto pela geração única (_ThreadGerarSimulacao) quanto
    pelo lote de múltiplos cenários (_ThreadGerarLote), um `sufixo_tabela`
    por cenário (ver core/simulacao.py:gerar_populacao/ativar_cenario).
    Sem sufixo (modo de cenário único, comportamento de sempre), grava
    direto nas tabelas canônicas. Devolve o dict `resultado` (mesmo
    formato usado pelo QMessageBox de resumo da geração única) — levanta
    ValueError/Exception na primeira falha, igual gerar_populacao já
    fazia; quem chama em lote (_ThreadGerarLote) captura por cenário.

    `contexto_lote` (opcional): resultado de
    `core.simulacao.preparar_contexto_lote`, repassado direto pra
    `gerar_populacao`/`calcular_distribuicao_diametrica` — usado só pelo
    lote (`_ThreadGerarLote`, que monta o contexto uma vez pro lote
    inteiro em vez de a cada cenário). None na geração única, mesmo
    comportamento de sempre.

    `cenario_id`/`proximo_id_populacao` (opcionais, só o lote paralelo/
    sequencial passa): quando informados, a persistência (ver
    core/simulacao.py:persistir_cenario_calculado) grava nas tabelas
    UNIFICADAS do lote (`simulacao_lote_*`, uma linha por cenário) em vez
    de DROP+CREATE numa tabela "{sufixo_tabela}" própria — é essa troca
    que elimina o custo crescente de DDL que causava "database is locked"
    em lotes grandes. `resultado["_proximo_id_populacao_lote"]` sai
    preenchido nesse caso — quem chama usa como `proximo_id_populacao` do
    próximo cenário (ver _executar_lote_sequencial/_executar_lote_
    paralelo).

    `calcular_mip` (padrão True, geração única): se False, pula a etapa de
    MIP contínuo inteira — usada pelo lote de "Múltiplos cenários", que
    normalmente não precisa desse resultado pra cada cenário testado (é
    CPU-bound: um ajuste `scipy.optimize.curve_fit` por talhão, ver
    core/simulacao.py:calcular_mip_continuo) e tem o checkbox "Calcular
    MIP contínuo" (desmarcado por padrão) pra religar quando precisar.

    `resultado["tempos_estagios"]` (dict, sempre presente) guarda quantos
    segundos cada etapa levou (populacao/construtores/distribuicao/
    volume_sortimento/gravacao_final/mip_continuo, esta última ausente
    quando `calcular_mip=False`) — diagnóstico pra achar qual etapa
    domina o tempo total, principalmente no lote de "Múltiplos cenários"
    (ver _ThreadGerarLote, que soma isso entre todos os cenários e mostra
    no resumo final).

    População, construtores, distribuição e volume por sortimento rodam
    inteiramente EM MEMÓRIA (ver core/simulacao.py:calcular_cenario_em_
    memoria) — só tocam o banco UMA VEZ, no final ("gravacao_final", ver
    core/simulacao.py:persistir_cenario_calculado), em vez de cada etapa
    ler/gravar a tabela de população separadamente (era o gargalo medido
    em simulações grandes: idas e voltas ao banco a cada etapa,
    multiplicadas por milhares de cenários no lote). Exceção: se algum
    construtor ativo tiver um nó "Custo de Formação"
    (`calcular_cenario_em_memoria` devolve None nesse caso, porque esse nó
    depende de linhas sintéticas inseridas DIRETO no banco antes de
    calcular, via sincronizar_linhas_formacao) — cai pro caminho antigo
    inteiro (via banco, tudo com `persistir=True`), sem o ganho de
    performance, mas sem risco: caso raro, não vale a complexidade extra
    de replicar aquela sincronização em memória agora. Mesma checagem
    decide, ANTES de o lote começar, se `_ThreadGerarLote` roda em
    paralelo (ProcessPoolExecutor, um cenário por núcleo, chamando
    calcular_cenario_em_memoria num processo worker) ou cai pro laço
    sequencial de sempre (ver `_ThreadGerarLote.run`)."""
    tabela_populacao = simulacao.TABELA_POPULACAO + sufixo_tabela

    calculo = simulacao.calcular_cenario_em_memoria(
        conn, configuracao, sufixo_tabela=sufixo_tabela, contexto_lote=contexto_lote)

    tempos_estagios = {}
    _marca = time.perf_counter()

    def _medir(nome_estagio):
        nonlocal _marca
        agora = time.perf_counter()
        tempos_estagios[nome_estagio] = agora - _marca
        _marca = agora

    if calculo is not None:
        resultado = calculo
        tempos_estagios.update(resultado.pop("tempos_estagios"))
        _marca = time.perf_counter()  # reseta pra medir só a persistência daqui em diante
        simulacao.persistir_cenario_calculado(
            conn, resultado, sufixo_tabela, commit=False,
            cenario_id=cenario_id, proximo_id_populacao=proximo_id_populacao)
        conn.commit()
        _medir("gravacao_final")
    else:
        # Fallback: algum construtor ativo tem nó "custo_formacao" (ver
        # calcular_cenario_em_memoria) — grava a população já calculada
        # (ainda sem colunas de construtor) e segue o caminho antigo,
        # inteiramente via banco, igual antes da otimização em memória
        # existir.
        resultado = simulacao.gerar_populacao(
            conn, configuracao["coluna_talhao_ifc"], configuracao["coluna_fustes_observados"],
            configuracao["idade_raleio"], configuracao["intensidade_raleio"],
            configuracao["idade_desbaste_1"], configuracao["intensidade_desbaste_1"],
            configuracao["idade_desbaste_2"], configuracao["intensidade_desbaste_2"],
            configuracao["idade_corte_raso"],
            coluna_dap_med_observado=configuracao["coluna_dap_med_observado"],
            coluna_dap_max_observado=configuracao["coluna_dap_max_observado"],
            coluna_dap_min_observado=configuracao["coluna_dap_min_observado"],
            coluna_ht_observado=configuracao["coluna_ht_observado"],
            coluna_vtcc_observado=configuracao["coluna_vtcc_observado"],
            coluna_cv_dap_observado=configuracao["coluna_cv_dap_observado"],
            coluna_data_plantio=configuracao["coluna_data_plantio"],
            sufixo_tabela=sufixo_tabela,
            contexto_lote=contexto_lote,
            persistir=False,
        )
        _medir("populacao")
        simulacao.salvar_coluna_data_medicao(conn, configuracao["coluna_data_medicao"])
        df_populacao = resultado["_df_populacao"]

        simulacao._persistir_populacao(
            conn, resultado["_tabela_populacao"], resultado["_tabela_distribuicao"],
            resultado["_create_table_sql"], df_populacao, resultado["_colunas_insert"], commit=False)
        simulacao._persistir_distribuicao(
            conn, resultado["_tabela_distribuicao"], resultado["_tabela_populacao"],
            resultado["_linhas_distribuicao"], commit=False)
        conn.commit()
        for chave in ("_df_populacao", "_tabela_populacao", "_create_table_sql", "_colunas_insert",
                      "_tabela_distribuicao", "_linhas_distribuicao"):
            resultado.pop(chave, None)
        _medir("gravacao_final")

        resultado["resumo_construtores"] = construtores.aplicar_construtores_salvos(
            conn, tabela_populacao, tabela_origem=simulacao.TABELA_POPULACAO,
            idade_corte_raso=configuracao["idade_corte_raso"])
        _medir("construtores")

        coluna_forma_distribuicao = configuracao["coluna_forma_distribuicao"]
        coluna_escala_distribuicao = configuracao["coluna_escala_distribuicao"]
        simulacao.salvar_coluna_forma_distribuicao(conn, coluna_forma_distribuicao)
        simulacao.salvar_coluna_escala_distribuicao(conn, coluna_escala_distribuicao)

        resultado["aviso_distribuicao"] = None
        if coluna_forma_distribuicao or coluna_escala_distribuicao:
            coluna_forma_efetiva = coluna_forma_distribuicao or resultado["coluna_forma_atual"]
            coluna_escala_efetiva = coluna_escala_distribuicao or resultado["coluna_escala_atual"]
            try:
                resultado_distribuicao = simulacao.calcular_distribuicao_diametrica(
                    conn, coluna_forma_efetiva, coluna_escala_efetiva, sufixo_tabela=sufixo_tabela,
                    contexto_lote=contexto_lote)
                resultado.update(resultado_distribuicao)
            except ValueError as e:
                resultado["aviso_distribuicao"] = (
                    f"Simulação gerada, mas não foi possível recalcular a distribuição com "
                    f"a coluna de forma/escala apontada:\n{e}\n\n"
                    "A distribuição ficou com forma_atual/escala_atual (padrão).")
        _medir("distribuicao")

        simulacao.salvar_colunas_base_volume_classes(conn, configuracao["colunas_volume_classes"])
        simulacao.salvar_tipo_agregacao_volume(conn, configuracao["tipo_agregacao_volume"])
        simulacao.salvar_usar_tabela_agregacao_volume(conn, configuracao["usar_tabela_agregacao_volume"])
        resultado["aviso_volume_sortimento"] = None
        metadados_cenario = None
        if configuracao.get("nome_cenario"):
            metadados_cenario = {"cenario": configuracao["nome_cenario"]}
            metadados_cenario.update(
                {campo: configuracao.get(campo) for campo in _CAMPOS_MANEJO_CENARIO})
        colunas_volume_efetivas = (
            configuracao["colunas_volume_classes"]
            if configuracao["usar_tabela_agregacao_volume"] else [])
        try:
            resultado["resultado_volume_sortimento"] = simulacao.calcular_volume_por_sortimento(
                conn, colunas_volume_efetivas, configuracao["tipo_agregacao_volume"],
                sufixo_tabela=sufixo_tabela, metadados_cenario=metadados_cenario)
        except ValueError as e:
            resultado["resultado_volume_sortimento"] = {"executado": False}
            resultado["aviso_volume_sortimento"] = (
                f"Simulação gerada, mas não foi possível calcular o volume por sortimento:\n{e}")
        _medir("volume_sortimento")

    # MIP contínuo — ver core/simulacao.py:calcular_mip_para_cenario (lê
    # `configuracao["coluna_forma_distribuicao"/"coluna_escala_
    # distribuicao"]` sozinha, sem precisar reler nada daqui).
    if calcular_mip:
        # cenario_id só faz sentido quando a persistência acima passou
        # pelo caminho em memória/tabela unificada (`calculo is not
        # None`, ver persistir_cenario_calculado) — no fallback raro
        # (construtor com nó "Custo de Formação" sem coluna de talhão),
        # a população foi gravada na tabela SUFIXADA antiga, e é lá que
        # o MIP precisa ler (sufixo_tabela, sem cenario_id).
        cenario_id_mip = cenario_id if calculo is not None else None
        simulacao.calcular_mip_para_cenario(
            conn, configuracao, resultado, sufixo_tabela, cenario_id=cenario_id_mip)
        _medir("mip_continuo")
    else:
        resultado["aviso_mip_continuo"] = None
        resultado["resultado_mip_continuo"] = {"executado": False}

    resultado["tempos_estagios"] = tempos_estagios
    conn.commit()
    return resultado


def _ler_idade(texto, rotulo):
    texto = texto.strip()
    if not texto:
        raise ValueError(f"{rotulo} é obrigatória.")
    try:
        valor = int(float(texto.replace(",", ".")))
    except ValueError:
        raise ValueError(f"{rotulo} precisa ser um número inteiro.")
    if valor < 1:
        raise ValueError(f"{rotulo} precisa ser maior que zero.")
    return valor


def _formatar_intensidade(valor):
    return f"{valor * 100:.2f}%"
