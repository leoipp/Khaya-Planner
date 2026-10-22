import io
import unittest
from unittest.mock import patch
from app.core import codificacao as c


class CodificacaoBlocos(unittest.TestCase):
    def test_compatibilidade_e_reuso_do_buffer_entre_blocos(self):
        bloco = len(c._CHAVE) * 3
        for tamanho in (0, 1, bloco - 1, bloco, bloco * 4 + 7):
            with self.subTest(tamanho=tamanho), patch.object(c, '_TAMANHO_BLOCO', bloco):
                original = bytes(i % 256 for i in range(tamanho))
                esperado = bytes(v ^ c._CHAVE[i % len(c._CHAVE)] for i, v in enumerate(original))
                saida = io.BytesIO()
                progresso = []
                c._xor_arquivo(io.BytesIO(original), saida, lambda n, t: progresso.append(n), tamanho)
                self.assertEqual(saida.getvalue(), esperado)
                volta = io.BytesIO()
                c._xor_arquivo(io.BytesIO(saida.getvalue()), volta, bytes_totais=tamanho)
                self.assertEqual(volta.getvalue(), original)
                if tamanho:self.assertEqual(progresso[-1], tamanho)
