# -*- coding: utf-8 -*-
"""
Gerencia o projeto atualmente aberto no app.

Um "projeto" é um arquivo .mogno (SQLite codificado, ver codificacao.py).
Enquanto está aberto, trabalhamos numa cópia decodificada em pasta
temporária (_caminho_trabalho); toda alteração é sincronizada de volta
pro arquivo .mogno original via sincronizar().
"""
import sqlite3
import tempfile
import threading
import uuid
from pathlib import Path

from PySide6.QtCore import QTimer

from . import codificacao
from . import db

EXTENSAO = ".mogno"

_caminho_projeto = None
_caminho_trabalho = None
_revisao_dados = 0

# sincronizar() é chamado depois de qualquer commit em qualquer tela
# (dezenas de pontos no app) — recodificar o projeto inteiro a cada
# clique de "Salvar" trava a UI pelo tempo de reler+regravar o arquivo
# inteiro, mesmo quando só uma linha mudou. _temporizador (um QTimer
# singleShot, ligado sob demanda por definir_agendador_qt — chamado uma
# vez em main.py, já com QApplication instanciado) permite agendar a
# regravação de fato alguns instantes depois — várias chamadas em
# sequência (ex: editando registros um atrás do outro) viram UMA
# regravação só, rodando em thread de fundo pra não travar a UI.
_temporizador = None
_temporizador_pendente = False
_proxima_sincronizacao = None  # (caminho_projeto, caminho_trabalho) pendente pro QTimer disparar
_lock_sincronizacao = threading.Lock()
_INTERVALO_DEBOUNCE_MS_BASE = 1500
# Cada sincronização dispara um backup() + XOR completos do arquivo de
# trabalho inteiro (ambos já streamados, então não estouram memória — ver
# codificacao.py — mas o tempo de disco/CPU ainda escala com o tamanho
# total do banco, independente de qual tabela mudou). Pra projetos grandes
# (5GB+), esperar mais antes de regravar reduz quantas vezes esse
# full-rewrite roda numa sessão de edição contínua, sem abrir mão de
# nenhuma garantia de sincronizar(): fechar/trocar de projeto sempre força
# a sincronização pendente antes de liberar
# (_finalizar_sincronizacao_pendente), então esse atraso só afeta quanto
# tempo uma edição fica só na cópia de trabalho antes de estar refletida
# no .mogno em disco — o mesmo tipo de risco que já existe hoje a 1.5s
# (perda das últimas edições numa queda de energia/crash durante edição
# ativa), só que numa janela maior pra projetos grandes. Valores abaixo são
# ponto de partida, calibrados a olho — ajustar se o tempo real de
# backup+XOR medido em projetos grandes pedir outra curva.
_TAMANHO_LIMIAR_MB = 200  # abaixo disso, debounce fica igual a hoje
_TAMANHO_TETO_EXTRA_MS = 18500  # debounce nunca passa de 1500+18500 = 20s


def _intervalo_debounce_ms():
    try:
        tamanho_mb = _caminho_trabalho.stat().st_size / (1024 * 1024)
    except (OSError, AttributeError):
        return _INTERVALO_DEBOUNCE_MS_BASE
    if tamanho_mb <= _TAMANHO_LIMIAR_MB:
        return _INTERVALO_DEBOUNCE_MS_BASE
    extra = min(_TAMANHO_TETO_EXTRA_MS, int((tamanho_mb - _TAMANHO_LIMIAR_MB) / 500 * 1000))
    return _INTERVALO_DEBOUNCE_MS_BASE + extra


def definir_agendador_qt():
    """Habilita o agendamento assíncrono do debounce via QTimer — chamar
    uma vez, com QApplication já instanciado (ver main.py), antes de abrir
    qualquer projeto. Sem chamar isto, sincronizar() regrava o .mogno na
    hora (síncrono), do jeito que já funciona fora da UI (scripts/testes)."""
    global _temporizador
    if _temporizador is None:
        _temporizador = QTimer()
        _temporizador.setSingleShot(True)
        _temporizador.timeout.connect(_ao_disparar_temporizador)


def projeto_aberto():
    return _caminho_projeto is not None


def revisao_dados():
    """Versão monotônica do estado visível às telas nesta sessão.

    A navegação usa este contador para não refazer consultas e gráficos
    quando nada foi alterado desde a última visita. Não é persistido nem
    representa versão de schema; serve apenas para invalidação da UI.
    """
    return _revisao_dados


def _avancar_revisao_dados():
    global _revisao_dados
    _revisao_dados += 1


def caminho_trabalho():
    if _caminho_trabalho is None:
        raise RuntimeError("Nenhum projeto aberto. Crie ou abra um projeto primeiro.")
    return _caminho_trabalho


def nome_projeto():
    if _caminho_projeto is None:
        return None
    return _caminho_projeto.stem


def caminho_projeto():
    """Caminho do .mogno do projeto aberto (o arquivo de verdade, não a
    cópia de trabalho decodificada — ver caminho_trabalho) ou None se
    nenhum projeto estiver aberto. Usado por quem precisa gravar algo AO
    LADO do projeto (ex: simulacao.py:_gravar_log_construtores, o log de
    diagnóstico de performance da geração em lote)."""
    return _caminho_projeto


def _novo_arquivo_trabalho():
    pasta = Path(tempfile.gettempdir()) / "khaya_planner_v2"
    pasta.mkdir(exist_ok=True)
    return pasta / f"{uuid.uuid4().hex}.sqlite"


def _apagar_arquivo_trabalho(caminho):
    """Remove a cópia de trabalho antiga em %TEMP% — sem isso, cada
    abertura de projeto deixava a cópia decodificada anterior (mesmo
    tamanho do .mogno, sem compressão) perdida em C:\\...\\Temp\\
    khaya_planner_v2 pra sempre, mesmo trocando de projeto ou fechando o
    app."""
    if caminho is not None:
        caminho.unlink(missing_ok=True)


def limpar_arquivos_orfaos():
    """Apaga qualquer arquivo sobrando em %TEMP%/khaya_planner_v2 de uma
    sessão anterior que não fechou normalmente (processo morto à força,
    crash, queda de energia) — _apagar_arquivo_trabalho só cobre troca/
    fechamento normal de projeto, então um encerramento anormal deixava
    a cópia de trabalho (pode ser do tamanho do projeto inteiro, vários
    GB) perdida no disco pra sempre. Chamar uma vez no início do app,
    antes de qualquer projeto ser aberto nesta sessão — nesse momento
    tudo que já está na pasta é garantidamente órfão (esta sessão ainda
    não criou nenhum arquivo de trabalho).

    Se outra instância do app estiver rodando ao mesmo tempo com um
    projeto aberto, o arquivo dela está em uso — no Windows isso faz
    unlink() falhar com PermissionError (não dá pra apagar um arquivo
    aberto por outro processo), então só pula: não é órfão de verdade,
    é só um falso positivo de "existe na pasta sem eu ter criado"."""
    pasta = Path(tempfile.gettempdir()) / "khaya_planner_v2"
    if not pasta.exists():
        return
    for arquivo in pasta.iterdir():
        try:
            arquivo.unlink()
        except OSError:
            pass


def _finalizar_sincronizacao_pendente():
    """Cancela qualquer regravação agendada (debounce) e espera uma
    regravação em andamento terminar — chamado antes de trocar/fechar o
    projeto, senão o arquivo de trabalho antigo seria apagado (1) com
    alterações ainda não gravadas no .mogno, se havia uma regravação só
    agendada, ou (2) enquanto uma regravação em segundo plano ainda está
    lendo esse mesmo arquivo. Se havia uma regravação agendada, força ela
    a acontecer agora (síncrono) antes de liberar — perder essa última
    versão seria perda de dado de verdade, não só lentidão."""
    havia_pendente = _cancelar_pendente()
    with _lock_sincronizacao:
        pass
    if havia_pendente and _caminho_projeto is not None and _caminho_trabalho is not None:
        _sincronizar_agora(_caminho_projeto, _caminho_trabalho)


def finalizar_sincronizacao_pendente():
    """Wrapper público de _finalizar_sincronizacao_pendente — chamar (na
    thread principal) antes de disparar uma operação de fundo longa que
    vai escrever pesado no banco de trabalho (ex: ajuste de Weibull em
    lote, minutos de duração), pra não competir com uma sincronização já
    agendada/em andamento: as duas usam Connection.backup(), que segura o
    arquivo de trabalho por um tempo proporcional ao tamanho dele — numa
    coincidência de timing, a escrita da operação de fundo podia esperar
    o busy_timeout inteiro (30s) atrás do backup e estourar "database is
    locked". Flush aqui deixa a sincronização pendente resolvida ANTES da
    operação começar, em vez de correndo em paralelo com ela."""
    _finalizar_sincronizacao_pendente()


def novo_projeto(caminho_mogno):
    global _caminho_projeto, _caminho_trabalho

    caminho_mogno = Path(caminho_mogno)
    _finalizar_sincronizacao_pendente()

    trabalho = _novo_arquivo_trabalho()
    db.inicializar_schema(trabalho)

    _apagar_arquivo_trabalho(_caminho_trabalho)
    _caminho_projeto = caminho_mogno
    _caminho_trabalho = trabalho
    _avancar_revisao_dados()
    sincronizar()


def preparar_abertura():
    """Chamar na thread principal ANTES de `abrir_projeto` — finaliza
    qualquer sincronização pendente do projeto atual (se houver), senão
    o arquivo de trabalho dele seria descartado com alterações ainda não
    gravadas no .mogno."""
    _finalizar_sincronizacao_pendente()


def abrir_projeto(caminho_mogno, progress_callback=None):
    """Decodifica o .mogno numa cópia de trabalho nova e inicializa o
    schema — só isso, sem tocar no estado global do módulo (por isso é
    seguro chamar numa thread de fundo, ver window.py:
    _executar_abertura_em_thread). Decodificar é streamed (ver
    codificacao.py) mas ainda é O(tamanho do arquivo) de I/O+CPU — um
    .mogno de vários GB pode levar dezenas de segundos a alguns minutos,
    tempo bom demais pra travar a janela principal.

    Retorna (caminho_mogno, caminho_trabalho); quem chamou deve passar os
    dois pra `confirmar_abertura`, de volta na thread principal, antes de
    considerar o projeto de fato aberto. `preparar_abertura()` precisa já
    ter rodado (na thread principal) antes de chamar esta função."""
    caminho_mogno = Path(caminho_mogno)
    trabalho = _novo_arquivo_trabalho()
    codificacao.decodificar_arquivo(caminho_mogno, trabalho, progress_callback=progress_callback)
    db.inicializar_schema(trabalho)
    return caminho_mogno, trabalho


def confirmar_abertura(caminho_mogno, caminho_trabalho_novo):
    """Troca o projeto atual pelo resultado de `abrir_projeto` — chamar
    de volta na thread principal depois que a decodificação em segundo
    plano terminar. Separado de `abrir_projeto` porque mexer no estado
    global do módulo fora da thread principal arriscaria outra parte do
    app ler `_caminho_trabalho`/`_caminho_projeto` no meio da troca."""
    global _caminho_projeto, _caminho_trabalho
    caminho_trabalho_antigo = _caminho_trabalho
    _caminho_projeto = caminho_mogno
    _caminho_trabalho = caminho_trabalho_novo
    _avancar_revisao_dados()
    _apagar_arquivo_trabalho(caminho_trabalho_antigo)


def fechar_projeto():
    """Apaga a cópia de trabalho atual em %TEMP%, se houver — chamado ao
    fechar o app (ver App._ao_fechar em app/window.py)."""
    global _caminho_projeto, _caminho_trabalho
    _finalizar_sincronizacao_pendente()
    _apagar_arquivo_trabalho(_caminho_trabalho)
    _caminho_projeto = None
    _caminho_trabalho = None


def sincronizar():
    """Agenda a regravação do .mogno com o conteúdo atual do banco de
    trabalho. Chamar depois de qualquer commit que altere dados.

    Não regrava na hora: agenda pra alguns instantes depois (QTimer
    singleShot, ver definir_agendador_qt), cancelando qualquer regravação
    ainda não disparada — várias chamadas em sequência (telas que salvam
    registro a registro) colapsam numa regravação só. Quando dispara, a
    regravação de fato roda em thread de fundo (_sincronizar_agora), então
    nem o agendamento nem a regravação travam a UI.
    _finalizar_sincronizacao_pendente garante que nada fica pra trás ao
    trocar/fechar o projeto."""
    global _temporizador_pendente, _proxima_sincronizacao
    if _caminho_projeto is None or _caminho_trabalho is None:
        return

    # A alteração já foi commitada no banco de trabalho quando este método
    # é chamado; invalida caches visuais imediatamente, sem esperar o
    # debounce da gravação física do .mogno.
    _avancar_revisao_dados()

    if _temporizador is None:
        # Sem QTimer disponível (ex: uso fora da UI, scripts/testes) —
        # regrava na hora, síncrono.
        _sincronizar_agora(_caminho_projeto, _caminho_trabalho)
        return

    _cancelar_pendente()
    # _temporizador.timeout já está conectado uma vez só (ver
    # definir_agendador_qt) — reconectar aqui a cada chamada faria a lista
    # de slots crescer sem limite; guarda o alvo num módulo-level pra
    # _ao_disparar_temporizador ler quando o QTimer disparar.
    _proxima_sincronizacao = (_caminho_projeto, _caminho_trabalho)
    _temporizador_pendente = True
    _temporizador.start(_intervalo_debounce_ms())


def _ao_disparar_temporizador():
    global _temporizador_pendente
    _temporizador_pendente = False
    if _proxima_sincronizacao is not None:
        _disparar_sincronizacao_em_thread(*_proxima_sincronizacao)


def _cancelar_pendente():
    global _temporizador_pendente
    havia = _temporizador_pendente
    if havia and _temporizador is not None:
        _temporizador.stop()
    _temporizador_pendente = False
    return havia


def _disparar_sincronizacao_em_thread(caminho_projeto, caminho_trabalho_atual):
    global _temporizador_pendente
    _temporizador_pendente = None
    threading.Thread(
        target=_sincronizar_agora, args=(caminho_projeto, caminho_trabalho_atual), daemon=True
    ).start()


def _sincronizar_agora(caminho_projeto, caminho_trabalho_atual):
    """Regrava o .mogno a partir de uma cópia consistente do banco de
    trabalho. Usa Connection.backup() (API do sqlite3 feita pra copiar um
    banco "ao vivo") em vez de ler os bytes do arquivo direto — assim a
    cópia fica correta mesmo que essa função rode numa thread de fundo ao
    mesmo tempo em que a thread principal está fazendo outro commit no
    mesmo arquivo (ler os bytes brutos nesse cenário arriscaria pegar uma
    página gravada pela metade). Serializado por _lock_sincronizacao —
    evita duas regravações mexendo no mesmo .tmp/arquivo de trabalho ao
    mesmo tempo."""
    with _lock_sincronizacao:
        if not caminho_trabalho_atual.exists():
            return  # projeto foi trocado/fechado antes de rodar

        caminho_copia = caminho_trabalho_atual.with_name(
            caminho_trabalho_atual.name + ".backup.tmp")
        try:
            origem = sqlite3.connect(str(caminho_trabalho_atual))
            origem.execute("PRAGMA busy_timeout = 30000")
            try:
                destino = sqlite3.connect(str(caminho_copia))
                try:
                    origem.backup(destino)
                finally:
                    destino.close()
            finally:
                origem.close()

            caminho_temporario = caminho_projeto.with_name(caminho_projeto.name + ".tmp")
            codificacao.codificar_arquivo(caminho_copia, caminho_temporario)
            caminho_temporario.replace(caminho_projeto)
        finally:
            Path(caminho_copia).unlink(missing_ok=True)
