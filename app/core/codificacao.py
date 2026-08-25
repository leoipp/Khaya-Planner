# -*- coding: utf-8 -*-
"""
Codificação do arquivo de projeto (.mogno).

Não é criptografia forte — é uma ofuscação simples (cabeçalho próprio +
XOR com chave fixa) só pra garantir que o arquivo é um projeto reconhecível
por este programa, e não abre à toa em outro leitor de SQLite genérico.
Não usar pra proteger dado sensível.
"""
from pathlib import Path

import numpy as np

CABECALHO = b"MOGNOSIM1"
_CHAVE = b"simulador-mogno-projeto-2026"

# Múltiplo do tamanho da chave — cada bloco lido do disco começa exatamente
# na mesma fase da chave (posição 0), senão cada bloco precisaria de um
# deslocamento próprio calculado a partir da posição absoluta no arquivo.
_TAMANHO_BLOCO = (64 * 1024 * 1024 // len(_CHAVE)) * len(_CHAVE)


def _bloco_chave(tamanho: int) -> np.ndarray:
    chave = np.frombuffer(_CHAVE, dtype=np.uint8)
    return np.resize(chave, tamanho)


def _xor_arquivo(entrada, saida, progress_callback=None, bytes_totais=None) -> None:
    """XOR byte a byte com _CHAVE repetida, lendo/gravando em blocos em
    vez de carregar o arquivo inteiro em memória — projetos grandes (base
    de árvores + detalhamento árvore por árvore de uma simulação de
    intensidades, que grava uma linha por árvore para CADA combinação de
    raleio/1º/2º desbaste) podem passar de vários GB, e processar tudo de
    uma vez em bytes/numpy precisava de várias cópias do arquivo inteiro
    em RAM ao mesmo tempo (bytes lidos + array da chave + resultado do
    XOR + bytes final), estourando memória bem antes de faltar espaço em
    disco.

    `progress_callback(bytes_processados, bytes_totais)`, se passado, é
    chamado depois de cada bloco — usado por quem chama (ver
    projeto.abrir_projeto) pra alimentar uma barra de progresso enquanto
    isso roda numa thread de fundo."""
    bloco_chave = _bloco_chave(_TAMANHO_BLOCO)
    bytes_processados = 0
    while True:
        pedaco = entrada.read(_TAMANHO_BLOCO)
        if not pedaco:
            break
        arr = np.frombuffer(pedaco, dtype=np.uint8)
        saida.write((arr ^ bloco_chave[:len(arr)]).tobytes())
        bytes_processados += len(pedaco)
        if progress_callback is not None:
            progress_callback(bytes_processados, bytes_totais)


def codificar_arquivo(caminho_entrada, caminho_saida, progress_callback=None) -> None:
    bytes_totais = Path(caminho_entrada).stat().st_size
    with open(caminho_entrada, "rb") as entrada, open(caminho_saida, "wb") as saida:
        saida.write(CABECALHO)
        _xor_arquivo(entrada, saida, progress_callback, bytes_totais)


def decodificar_arquivo(caminho_entrada, caminho_saida, progress_callback=None) -> None:
    bytes_totais = Path(caminho_entrada).stat().st_size
    with open(caminho_entrada, "rb") as entrada:
        if entrada.read(len(CABECALHO)) != CABECALHO:
            raise ValueError("Arquivo não é um projeto válido do Khaya Planner.")
        with open(caminho_saida, "wb") as saida:
            _xor_arquivo(entrada, saida, progress_callback, bytes_totais)
