"""Mapa vetorial com navegação e atributos no painel inferior."""
import json
import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGraphicsPathItem, QGraphicsScene, QGraphicsView,
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core import camadas, mapa_dados, projeto
from ..core.db import conectar
from ..widgets.tabela import Tabela
from ..theme import manager as tema
from .base import TelaBase


class CanvasMapa(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMinimumSize(200, 150)
        self.itens_feicoes = {}
        self._atualizar_tema()
        tema.obter().themeChanged.connect(self._atualizar_tema)

    def _atualizar_tema(self, _modo=None):
        self.setBackgroundBrush(QColor(tema.obter().cor_canvas_construtor()))

    def showEvent(self, evento):
        super().showEvent(evento)
        self.enquadrar()

    def wheelEvent(self, evento):
        fator = 1.2 if evento.angleDelta().y() > 0 else 1 / 1.2
        escala = self.transform().m11() * fator
        if 1e-12 < escala < 1e12:
            self.scale(fator, fator)
        evento.accept()

    def enquadrar(self):
        if self.scene().items():
            area = self.scene().itemsBoundingRect()
            margem = max(area.width(), area.height(), 1) * .05
            self.setSceneRect(area.adjusted(-margem, -margem, margem, margem))
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def carregar(self, geometrias):
        self.scene().clear()
        self.itens_feicoes = {}
        self.resetTransform()
        pontos = []
        def coletar(coords):
            if coords and isinstance(coords[0], (int, float)):
                if all(math.isfinite(v) for v in coords[:2]):
                    pontos.append(coords[:2])
            else:
                for parte in coords:
                    coletar(parte)
        for geo in geometrias:
            if geo:
                coletar(geo['coordinates'])
        origem = pontos[0] if pontos else (0, 0)
        raio = max((max(p[0] for p in pontos) - min(p[0] for p in pontos)),
                   (max(p[1] for p in pontos) - min(p[1] for p in pontos)), 1) * .004 if pontos else 1
        for indice, geo in enumerate(geometrias):
            if not geo:
                continue
            caminho = QPainterPath()
            caminho.setFillRule(Qt.FillRule.OddEvenFill)
            tipo, coords = geo['type'], geo['coordinates']
            def linha(parte, fechar=False):
                for i, p in enumerate(parte):
                    ponto = QPointF(p[0] - origem[0], origem[1] - p[1])
                    if i == 0:
                        caminho.moveTo(ponto)
                    else:
                        caminho.lineTo(ponto)
                if fechar:
                    caminho.closeSubpath()
            if tipo in ('Point', 'MultiPoint'):
                for p in ([coords] if tipo == 'Point' else coords):
                    caminho.addEllipse(QPointF(p[0] - origem[0], origem[1] - p[1]), raio, raio)
            elif tipo in ('LineString', 'MultiLineString'):
                for parte in ([coords] if tipo == 'LineString' else coords):
                    linha(parte)
            elif tipo in ('Polygon', 'MultiPolygon'):
                for poligono in ([coords] if tipo == 'Polygon' else coords):
                    for anel in poligono:
                        linha(anel, True)
            item = QGraphicsPathItem(caminho)
            caneta = QPen(QColor('#398b78'), 1.5)
            caneta.setCosmetic(True)
            item.setPen(caneta)
            if tipo in ('Point', 'MultiPoint', 'Polygon', 'MultiPolygon'):
                item.setBrush(QColor('#8070b49c'))
            self.scene().addItem(item)
            item.setData(0, indice + 1)
            item.setData(1, tipo in ('Point', 'MultiPoint', 'Polygon', 'MultiPolygon'))
            self.itens_feicoes[indice + 1] = item
        self.enquadrar()

    def colorir(self, cores):
        for id_, item in self.itens_feicoes.items():
            cor = QColor(cores.get(id_, '#70b49c'))
            caneta = QPen(cor.darker(125), 1.5)
            caneta.setCosmetic(True)
            item.setPen(caneta)
            if item.data(1):
                item.setBrush(QBrush(cor))


class TelaMapa(TelaBase):
    def __init__(self):
        super().__init__()
        self._camadas = []
        self._colunas = []
        self._linhas = []
        self._estilo = {}
        raiz = QVBoxLayout(self)
        barra = QHBoxLayout()
        barra.addWidget(QLabel('Mapa'))
        importar = QPushButton('Importar shapefile')
        importar.clicked.connect(self._importar)
        barra.addWidget(importar)
        self.combo = QComboBox()
        self.combo.setMinimumWidth(180)
        self.combo.currentIndexChanged.connect(self._selecionar)
        barra.addWidget(self.combo, 1)
        enquadrar = QPushButton('Enquadrar')
        enquadrar.clicked.connect(lambda: self.canvas.enquadrar())
        barra.addWidget(enquadrar)
        self.botao_atributos = QPushButton('Tabela de atributos')
        self.botao_atributos.setCheckable(True)
        self.botao_atributos.toggled.connect(self._alternar_atributos)
        barra.addWidget(self.botao_atributos)
        raiz.addLayout(barra)
        ferramentas = QHBoxLayout()
        self.botao_join = QPushButton('Join com cenários...')
        self.botao_join.clicked.connect(self._abrir_join)
        ferramentas.addWidget(self.botao_join)
        consulta = QPushButton('Tabela de cenários e filtros...')
        consulta.clicked.connect(self._abrir_consulta)
        ferramentas.addWidget(consulta)
        self.botao_cores = QPushButton('Valores únicos...')
        self.botao_cores.clicked.connect(self._abrir_valores_unicos)
        ferramentas.addWidget(self.botao_cores)
        self.botao_cor_unica = QPushButton('Restaurar cor única')
        self.botao_cor_unica.clicked.connect(self._restaurar_cor_unica)
        ferramentas.addWidget(self.botao_cor_unica)
        ferramentas.addStretch(1)
        raiz.addLayout(ferramentas)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.canvas = CanvasMapa()
        self.area_mapa = QSplitter(Qt.Orientation.Horizontal)
        self.area_mapa.addWidget(self.canvas)
        self.legenda = QTreeWidget()
        self.legenda.setHeaderLabels(['Cor', 'Valor', 'Feições'])
        self.legenda.setRootIsDecorated(False)
        self.legenda.setMinimumWidth(180)
        self.legenda.setMaximumWidth(340)
        self.legenda.setColumnWidth(0, 40)
        self.legenda.setColumnWidth(1, 145)
        self.legenda.hide()
        self.area_mapa.addWidget(self.legenda)
        self.area_mapa.setStretchFactor(0, 1)
        self.splitter.addWidget(self.area_mapa)
        self.painel = QWidget()
        inferior = QVBoxLayout(self.painel)
        self.tabela = Tabela(colunas=[])
        inferior.addWidget(self.tabela)
        self.splitter.addWidget(self.painel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        self.painel.hide()
        raiz.addWidget(self.splitter, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        raiz.addWidget(self.status)
        self.recarregar_lista()

    def _alternar_atributos(self, aberto):
        self.painel.setVisible(aberto)
        if aberto:
            self.splitter.setSizes([max(200, self.height() * 2 // 3), max(120, self.height() // 3)])

    def novo_registro(self):
        self.combo.clear()
        self.canvas.scene().clear()
        self.tabela.redefinir_colunas([])
        self.botao_atributos.setChecked(False)
        self._camadas = []
        self._colunas, self._linhas, self._estilo = [], [], {}
        self.canvas.itens_feicoes = {}
        self.legenda.clear()
        self.legenda.hide()

    def recarregar_lista(self):
        atual = self.combo.currentData()
        self._camadas = []
        if projeto.projeto_aberto():
            conn = conectar()
            try:
                self._camadas = camadas.listar(conn)
            finally:
                conn.close()
        self.combo.blockSignals(True)
        self.combo.clear()
        for tabela, nome, _, _ in self._camadas:
            self.combo.addItem(nome, tabela)
        indice = self.combo.findData(atual)
        self.combo.setCurrentIndex(indice if indice >= 0 else 0)
        self.combo.blockSignals(False)
        self._selecionar(self.combo.currentIndex())

    def _selecionar(self, indice):
        if indice < 0 or indice >= len(self._camadas):
            self.canvas.scene().clear()
            self.canvas.itens_feicoes = {}
            self.tabela.redefinir_colunas([])
            self._colunas, self._linhas, self._estilo = [], [], {}
            self.legenda.clear()
            self.legenda.hide()
            for botao in (self.botao_join, self.botao_cores, self.botao_cor_unica, self.botao_atributos):
                botao.setEnabled(False)
            self.status.setText('Crie ou abra um projeto e importe um shapefile para começar.')
            return
        tabela, nome, geometrias, projecao = self._camadas[indice]
        conn = conectar()
        try:
            cursor = conn.execute(f'SELECT * FROM {camadas.identificador(tabela)} ORDER BY id')
            self._colunas = [c[0] for c in cursor.description]
            self.tabela.redefinir_colunas(self._colunas)
            linhas = cursor.fetchall()
            self._linhas = linhas
            self.tabela.definir_linhas(linhas)
            self._estilo = mapa_dados.obter_estado(conn, 'estilo:' + tabela)
            join = mapa_dados.obter_estado(conn, 'join:' + tabela)
        finally:
            conn.close()
        self.canvas.carregar(json.loads(geometrias))
        self._aplicar_estilo()
        for botao in (self.botao_join, self.botao_cores, self.botao_cor_unica, self.botao_atributos):
            botao.setEnabled(True)
        sr = 'Projeção do arquivo preservada' if projecao else 'Sem arquivo .prj'
        info_join = (f' · Join: {join["correspondencias"]} correspondências, {join["sem_correspondencia"]} sem correspondência'
                     if join else '')
        self.status.setText(f'{nome} · {len(linhas)} feições · {sr}{info_join} · Arraste para mover; use a roda para zoom.')

    def _abrir_consulta(self):
        if not projeto.projeto_aberto():
            QMessageBox.information(self, 'Simulação', 'Crie ou abra um projeto primeiro.')
            return
        from ..widgets.mapa_dialogs import DialogoConsultaSimulacao
        dialogo = DialogoConsultaSimulacao(self)
        dialogo.exec()
        dialogo.deleteLater()

    def _abrir_join(self):
        camada = self.combo.currentData()
        if not camada:
            return
        from ..widgets.mapa_dialogs import DialogoJoin
        dialogo = DialogoJoin(camada, self)
        if dialogo.exec() == QDialog.DialogCode.Accepted:
            self.recarregar_lista()
            self.botao_atributos.setChecked(True)
        dialogo.deleteLater()

    def _abrir_valores_unicos(self):
        if not self._colunas:
            return
        from ..widgets.mapa_dialogs import DialogoValoresUnicos
        dialogo = DialogoValoresUnicos(self._colunas, self._linhas, self._estilo, self)
        if dialogo.exec() == QDialog.DialogCode.Accepted:
            self._estilo = dialogo.configuracao()
            mapa_dados.salvar_estado('estilo:' + self.combo.currentData(), self._estilo)
            self._aplicar_estilo()
        dialogo.deleteLater()

    def _restaurar_cor_unica(self):
        if not self.combo.currentData():
            return
        self._estilo = {}
        mapa_dados.salvar_estado('estilo:' + self.combo.currentData(), {})
        self._aplicar_estilo()

    def _aplicar_estilo(self):
        self.legenda.clear()
        coluna = self._estilo.get('coluna')
        if coluna not in self._colunas:
            self.canvas.colorir({})
            self.legenda.hide()
            return
        indice = self._colunas.index(coluna)
        id_indice = self._colunas.index('id')
        cores = self._estilo.get('cores', {})
        demais = self._estilo.get('cor_demais', '#b0b0b0')
        self.canvas.colorir({linha[id_indice]: cores.get(mapa_dados.chave_categoria(linha[indice]), demais)
                             for linha in self._linhas})
        self.legenda.setHeaderLabels(['Cor', coluna, 'Feições'])
        categorias = mapa_dados.categorias(linha[indice] for linha in self._linhas)
        for chave, info in categorias.items():
            item = QTreeWidgetItem(['', info['valor'], str(info['quantidade'])])
            item.setBackground(0, QBrush(QColor(cores.get(chave, demais))))
            item.setToolTip(1, chave)
            self.legenda.addTopLevelItem(item)
        self.legenda.show()

    def _importar(self):
        if not projeto.projeto_aberto():
            QMessageBox.information(self, 'Importar shapefile', 'Crie ou abra um projeto primeiro.')
            return
        caminho, _ = QFileDialog.getOpenFileName(self, 'Importar shapefile', '', 'Shapefile (*.shp *.SHP)')
        if not caminho:
            return
        try:
            try:
                tabela = camadas.importar(caminho)
            except UnicodeDecodeError:
                from PySide6.QtWidgets import QInputDialog
                encoding, ok = QInputDialog.getItem(self, 'Codificação dos atributos',
                    'Selecione a codificação do arquivo DBF:', ['cp1252', 'latin1', 'utf-8'], 0, False)
                if not ok:
                    return
                tabela = camadas.importar(caminho, encoding)
            self.recarregar_lista()
            self.combo.setCurrentIndex(self.combo.findData(tabela))
        except Exception as erro:
            QMessageBox.critical(self, 'Importar shapefile', str(erro))
