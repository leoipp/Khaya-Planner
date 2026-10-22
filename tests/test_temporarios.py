import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.core import temporarios


class TestTemporarios(unittest.TestCase):
    def test_codigo_fonte_independe_da_pasta_atual(self):
        self.assertEqual(temporarios.pasta_simulador(), Path(__file__).resolve().parents[1])

    def test_nuitka_usa_diretorio_externo_do_executavel(self):
        with tempfile.TemporaryDirectory() as pasta:
            with patch.object(temporarios, '__compiled__',
                              SimpleNamespace(containing_dir=pasta), create=True):
                self.assertEqual(temporarios.pasta_temporaria(), Path(pasta).resolve() / 'temp')
                self.assertTrue((Path(pasta) / 'temp').is_dir())

    def test_padrao_e_processos_filhos_usam_temp_local(self):
        with tempfile.TemporaryDirectory() as pasta:
            destino = Path(pasta) / 'temp'
            destino.mkdir()
            with patch.object(temporarios, 'pasta_temporaria', return_value=destino), \
                 patch.dict(os.environ), patch.object(tempfile, 'tempdir', None):
                temporarios.configurar_temporarios()
                self.assertEqual(tempfile.gettempdir(), str(destino))
                for chave in ('TEMP', 'TMP', 'TMPDIR', 'SQLITE_TMPDIR'):
                    self.assertEqual(os.environ[chave], str(destino))
                with tempfile.TemporaryDirectory() as criado:
                    self.assertEqual(Path(criado).parent, destino)


if __name__ == '__main__':
    unittest.main()
