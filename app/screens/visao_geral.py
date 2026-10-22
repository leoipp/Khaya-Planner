"""Entrada guiada do projeto com estados derivados do banco atual."""
import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..core import projeto
from ..core.db import conectar
from ..core.visao_geral import consultar_projeto
from ..theme import icones, qss
from ..widgets.cabecalho_tela import CabecalhoTela
from ..widgets.cartao import Cartao
from ..widgets.layout_fluxo import LayoutFluxo
from .base import TelaBase


class TelaVisaoGeral(TelaBase):
    navegar = Signal(str)
    novoProjetoSolicitado = Signal()
    abrirProjetoSolicitado = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.estado = None
        self._duas_colunas = None
        self._cartoes_etapas = []
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 20, 20, 16)
        raiz.setSpacing(16)
        cabecalho = CabecalhoTela('Visão geral',
            'Acompanhe o projeto, encontre suas ferramentas e escolha o próximo passo.')
        cabecalho.adicionar_acao('Atualizar', self.recarregar_lista, icone='atualizar')
        raiz.addWidget(cabecalho)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        corpo = QWidget()
        self._conteudo = QVBoxLayout(corpo)
        self._conteudo.setContentsMargins(0, 0, 4, 8)
        self._conteudo.setSpacing(16)
        scroll.setWidget(corpo)
        raiz.addWidget(scroll, 1)

        painel = Cartao('Seu espaço de planejamento')
        self._conteudo.addWidget(painel)
        abertura = QVBoxLayout(painel.corpo)
        abertura.setContentsMargins(0, 0, 0, 0)
        abertura.setSpacing(12)
        self.nome_projeto = QLabel()
        self.nome_projeto.setWordWrap(True)
        qss.aplicar_variante(self.nome_projeto, 'titulo')
        abertura.addWidget(self.nome_projeto)
        self.orientacao = QLabel()
        self.orientacao.setWordWrap(True)
        qss.aplicar_variante(self.orientacao, 'descricao-tela')
        abertura.addWidget(self.orientacao)
        acoes = LayoutFluxo()
        self.botao_proximo = QPushButton()
        qss.aplicar_variante(self.botao_proximo, 'salvar')
        self.botao_proximo.clicked.connect(self._proximo)
        acoes.addWidget(self.botao_proximo)
        self.botao_abrir = QPushButton('Abrir projeto')
        icones.aplicar_icone(self.botao_abrir, 'abrir_projeto')
        self.botao_abrir.clicked.connect(self.abrirProjetoSolicitado.emit)
        acoes.addWidget(self.botao_abrir)
        abertura.addLayout(acoes)

        self.metricas = QWidget()
        self.metricas.setProperty('variante', 'transparente')
        indicadores = QHBoxLayout(self.metricas)
        indicadores.setContentsMargins(0, 0, 0, 0)
        indicadores.setSpacing(12)
        self._valores_metricas = {}
        for chave, titulo in (('modelos', 'Modelos cadastrados'), ('sortimentos', 'Classes comerciais'),
                             ('cenarios', 'Cenários cadastrados')):
            cartao = Cartao(titulo)
            valor = QLabel('—')
            valor.setObjectName('IndicadorProjeto')
            QVBoxLayout(cartao.corpo).addWidget(valor)
            indicadores.addWidget(cartao, 1)
            self._valores_metricas[chave] = valor
        self._conteudo.addWidget(self.metricas)
        self.aviso = QLabel()
        self.aviso.setWordWrap(True)
        self._conteudo.addWidget(self.aviso)

        self.painel_etapas = QWidget()
        etapas_layout = QVBoxLayout(self.painel_etapas)
        etapas_layout.setContentsMargins(0, 0, 0, 0)
        etapas_layout.setSpacing(12)
        titulo = QLabel('Organização do projeto')
        qss.aplicar_variante(titulo, 'titulo')
        etapas_layout.addWidget(titulo)
        dica = QLabel('Os indicadores mostram dados encontrados no arquivo. Confira a validade dos modelos '
                      'e dos resultados nas respectivas telas; as etapas são uma orientação, não uma obrigação.')
        dica.setWordWrap(True)
        qss.aplicar_variante(dica, 'dica')
        etapas_layout.addWidget(dica)
        self._grade = QGridLayout()
        self._grade.setSpacing(12)
        etapas_layout.addLayout(self._grade)
        self._conteudo.addWidget(self.painel_etapas)

        atalhos = Cartao('Explore as ferramentas')
        atalhos_layout = QVBoxLayout(atalhos.corpo)
        atalhos_layout.setContentsMargins(0, 0, 0, 0)
        texto = QLabel('O inventário e os modelos alimentam as simulações; os estudos de madeira '
                       'também podem ser explorados pelas dimensões dos toretes. Use Ctrl+K para buscar uma tela.')
        texto.setWordWrap(True)
        qss.aplicar_variante(texto, 'dica')
        atalhos_layout.addWidget(texto)
        acoes_atalhos = LayoutFluxo()
        for chave, rotulo in (('mapa', 'Explorar mapa'), ('simulacao', 'Simulação florestal'),
                              ('toragem', 'Desdobro')):
            botao = QPushButton(rotulo)
            icones.aplicar_icone(botao, chave)
            botao.clicked.connect(lambda _=False, c=chave: self.navegar.emit(c))
            acoes_atalhos.addWidget(botao)
        atalhos_layout.addLayout(acoes_atalhos)
        self._conteudo.addWidget(atalhos)
        self._conteudo.addStretch(1)
        self.recarregar_lista()

    def resizeEvent(self, evento):
        super().resizeEvent(evento)
        self._organizar_etapas()

    def _organizar_etapas(self):
        if not hasattr(self, '_grade'):
            return
        duas = self.width() >= 720
        if duas == self._duas_colunas:
            return
        self._duas_colunas = duas
        for painel in self._cartoes_etapas:
            self._grade.removeWidget(painel)
        self._grade.setColumnStretch(0, 1)
        self._grade.setColumnStretch(1, 1 if duas else 0)
        for i, painel in enumerate(self._cartoes_etapas):
            self._grade.addWidget(painel, i//2 if duas else i, i%2 if duas else 0)

    def _limpar_etapas(self):
        for painel in self._cartoes_etapas:
            self._grade.removeWidget(painel)
            painel.deleteLater()
        self._cartoes_etapas.clear()
        self._duas_colunas = None

    def _proximo(self):
        if not projeto.projeto_aberto():
            self.novoProjetoSolicitado.emit()
        elif self.estado:
            proxima = next((e for e in self.estado['etapas'] if not e['presente']), None)
            self.navegar.emit(proxima['tela'] if proxima else 'simulacao')

    def novo_registro(self):
        self.estado = None
        self._limpar_etapas()
        self.metricas.hide()
        self.painel_etapas.hide()
        self.aviso.hide()
        self.nome_projeto.setText('Comece pelo seu projeto')
        self.orientacao.setText('Crie um projeto ou abra um arquivo .mogno para reunir seus dados, '
                               'modelos e cenários em um só lugar.')
        self.botao_proximo.setText('Criar projeto')
        self.botao_proximo.setEnabled(True)
        self.botao_abrir.show()

    def recarregar_lista(self):
        self.novo_registro()
        if not projeto.projeto_aberto():
            return
        self.nome_projeto.setText(projeto.nome_projeto())
        self.botao_abrir.hide()
        try:
            conn = conectar()
            try:
                self.estado = consultar_projeto(conn)
            finally:
                conn.close()
        except (RuntimeError, sqlite3.Error) as erro:
            self.orientacao.setText('Não foi possível consultar o projeto. Tente atualizar a visão geral.')
            self.botao_proximo.setEnabled(False)
            self.botao_proximo.setText('Dados indisponíveis')
            self.aviso.setText(str(erro))
            qss.aplicar_status(self.aviso, 'aviso')
            self.aviso.show()
            return
        self.metricas.show()
        self.painel_etapas.show()
        for chave, label in self._valores_metricas.items():
            label.setText(str(self.estado[chave]))
        proxima = next((e for e in self.estado['etapas'] if not e['presente']), None)
        self.orientacao.setText('Próximo passo sugerido: ' + proxima['detalhe'] if proxima
                               else 'Há dados em todas as etapas. Compare os cenários e revise seus resultados.')
        self.botao_proximo.setText('Continuar: ' + proxima['titulo'] if proxima else 'Abrir Simulação')
        if self.estado['erros']:
            self.aviso.setText(f'{self.estado["erros"]} cenário(s) com erro de geração. '
                               'Abra Simulação para conferir as mensagens e retomar os pendentes.')
            qss.aplicar_status(self.aviso, 'aviso')
            self.aviso.show()
        for etapa in self.estado['etapas']:
            painel = Cartao(etapa['titulo'])
            layout = QVBoxLayout(painel.corpo)
            layout.setContentsMargins(0, 0, 0, 0)
            estado = QLabel('Encontrado no projeto' if etapa['presente'] else 'A configurar')
            qss.aplicar_status(estado, 'sucesso' if etapa['presente'] else 'neutro')
            layout.addWidget(estado)
            detalhe = QLabel(etapa['detalhe'])
            detalhe.setWordWrap(True)
            layout.addWidget(detalhe)
            botao = QPushButton('Abrir ' + etapa['titulo'])
            botao.clicked.connect(lambda _=False, t=etapa['tela']: self.navegar.emit(t))
            layout.addWidget(botao, alignment=Qt.AlignmentFlag.AlignLeft)
            self._cartoes_etapas.append(painel)
        self._organizar_etapas()
