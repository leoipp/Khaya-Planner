"""Diretório único para os arquivos temporários de execução do simulador."""
import os
from pathlib import Path
import sys
import tempfile


def pasta_simulador():
    # Nuitka fornece o diretório externo também no modo onefile, em que
    # __file__ pode apontar para a pasta de extração do pacote.
    compilado = globals().get('__compiled__')
    if compilado is not None:
        return Path(compilado.containing_dir).resolve()
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def pasta_temporaria():
    pasta = pasta_simulador() / 'temp'
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def configurar_temporarios():
    """Inclui bibliotecas e processos filhos que usam o temporário padrão."""
    pasta = str(pasta_temporaria())
    for chave in ('TEMP', 'TMP', 'TMPDIR', 'SQLITE_TMPDIR'):
        os.environ[chave] = pasta
    tempfile.tempdir = pasta
