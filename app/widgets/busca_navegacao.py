"""Busca de telas por nome e assunto, acessível por teclado."""
import unicodedata

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from ..theme import icones, qss


ASSUNTOS = {
    'visao_geral': 'início projeto etapas próximos passos painel dashboard',
    'mapa': 'talhões território shapefile atributos geografia',
    'modelos': 'equações coeficientes regressão crescimento',
    'sortimentos': 'classes comerciais preços comprimento dimensões toras',
    'custos': 'formação colheita despesas produtividade',
    'weibull_ifc': 'distribuição diamétrica ajuste parcela inventário',
    'construtor_variaveis': 'grafo nós fórmulas cálculos indicadores',
    'simulacao': 'manejo raleio desbaste corte cenários grade população',
    'toragem': 'taper afilamento árvore perfil ponta fina toretes',
    'desdobro': 'madeira tábuas serraria cerne alburno rendimento',
    'ingressos_curvas': 'idade ingresso curvas distribuição MIP ITD',
    'resumos_cenarios': 'junção agrupamento resumo indicadores pivô',
    'planejamento': 'otimização demanda produção VPL CPLEX solver',
    'configuracoes': 'importação dados base inventário parâmetros CPLEX manutenção',
}


def normalizar(texto):
    return ''.join(c for c in unicodedata.normalize('NFKD', texto.casefold())
                   if not unicodedata.combining(c))


class BuscaNavegacao(QDialog):
    telaEscolhida = Signal(str)

    def __init__(self, itens, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Ir para uma tela')
        self.resize(560, 500)
        self._itens = list(itens)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        titulo = QLabel('Encontre sua ferramenta')
        qss.aplicar_variante(titulo, 'titulo')
        layout.addWidget(titulo)
        self.busca = QLineEdit()
        self.busca.setPlaceholderText('Busque por tela ou assunto: taper, custos, cenários…')
        self.busca.setAccessibleName('Buscar telas e ferramentas')
        self.busca.setClearButtonEnabled(True)
        self.busca.installEventFilter(self)
        layout.addWidget(self.busca)
        self.resultados = QListWidget()
        self.resultados.setObjectName('ResultadosNavegacao')
        self.resultados.setWordWrap(True)
        self.resultados.setAccessibleName('Telas encontradas')
        layout.addWidget(self.resultados, 1)
        self.vazio = QLabel('Nenhuma tela encontrada. Tente outro nome ou assunto.')
        self.vazio.setWordWrap(True)
        layout.addWidget(self.vazio)
        dica = QLabel('↑ ↓ para selecionar · Enter para abrir · Esc para fechar')
        qss.aplicar_variante(dica, 'dica')
        layout.addWidget(dica)
        self.busca.textChanged.connect(self._filtrar)
        self.busca.returnPressed.connect(self._abrir_selecionada)
        self.resultados.itemClicked.connect(self._abrir_item)
        self.resultados.itemActivated.connect(self._abrir_item)
        self._filtrar('')

    def showEvent(self, evento):
        super().showEvent(evento)
        self.busca.setFocus()

    def _filtrar(self, texto):
        termos = normalizar(texto).split()
        self.resultados.clear()
        for chave, titulo in self._itens:
            assunto = ASSUNTOS.get(chave, '')
            busca = normalizar(titulo + ' ' + assunto)
            if all(termo in busca for termo in termos):
                item = QListWidgetItem(icones.icone(chave), titulo)
                item.setData(Qt.ItemDataRole.UserRole, chave)
                item.setToolTip(assunto)
                self.resultados.addItem(item)
        self.vazio.setVisible(self.resultados.count() == 0)
        if self.resultados.count():
            self.resultados.setCurrentRow(0)

    def eventFilter(self, objeto, evento):
        if objeto is self.busca and evento.type() == QEvent.Type.KeyPress:
            if evento.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                passo = 1 if evento.key() == Qt.Key.Key_Down else -1
                linha = max(0, min(self.resultados.count()-1, self.resultados.currentRow()+passo))
                if self.resultados.count():
                    self.resultados.setCurrentRow(linha)
                return True
        return super().eventFilter(objeto, evento)

    def _abrir_selecionada(self):
        self._abrir_item(self.resultados.currentItem())

    def _abrir_item(self, item):
        if item is not None:
            self.telaEscolhida.emit(item.data(Qt.ItemDataRole.UserRole))
            self.accept()
