# -*- coding: utf-8 -*-
"""
Configurações do projeto, mais os pontos de entrada de operações que
antes tinham tela própria (Base IFC ByTalhao/ByTree, Simulação de
Intensidades) só pra mostrar uma tabela inteira depois de importar/rodar.
Porte completo de app/screens/configuracoes.py (Tkinter) — mesma lógica de
CRUD/importação/simulação/compactação, só a camada de widgets trocada pra
Qt (ver app/widgets/tabela.py, cartao.py, importacao_dialogs.py) e as 3
threading.Thread+queue.Queue+after(...) do original por QThread com sinais
(ver _ThreadImportarBase, _ThreadSimulacaoIntensidades,
_ThreadCompactarBanco, uma classe por operação de fundo) — Qt entrega o
resultado na thread da GUI sozinho, sem fila nem polling.
"""
import pandas as pd
from PySide6.QtCore import QRegularExpression, Qt, QThread, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import construtores, intensidades, projeto, simulacao
from ..core.db import (
    conectar, conectar_caminho, espaco_livre_suficiente_para_vacuum, excluir_tabelas,
    executar_vacuum, listar_tabelas_nao_essenciais,
)
from ..core.importador import (
    NOME_TABELA_BASE_IFC, NOME_TABELA_BASE_IFC_BYTREE, importar_planilha, listar_abas,
)
from ..theme import icones, qss
from ..widgets.cartao import Cartao
from ..widgets.importacao_dialogs import escolher_aba, escolher_cabecalho, escolher_mapeamento_colunas
from ..widgets.tabela import IID_RESUMO, Tabela, emoldurar_tabela
from .base import TelaBase

# (chave, título, nome da tabela, palavras-chave pra sugerir a aba padrão
# da planilha — ver _sugerir_aba_padrao mais abaixo)
_BASES_IFC = (
    ("talhao", "Base IFC ByTalhao", NOME_TABELA_BASE_IFC, ("talh",)),
    ("arvore", "Base IFC ByTree", NOME_TABELA_BASE_IFC_BYTREE, ("tree", "arv")),
)

_TABELAS_CONTAGEM_INTENSIDADES = (
    ("árvore (detalhamento)", intensidades.TABELA_DETALHAMENTO),
    ("parcela (resumo)", intensidades.TABELA_RESUMO_PARCELA),
    ("talhão (resumo)", intensidades.TABELA_RESUMO_TALHAO),
)


class _ThreadImportarBase(QThread):
    """Roda inteiramente numa thread de fundo — nada aqui toca em widget
    nenhum (ver core/importador.py:importar_planilha); progresso/resultado
    só via sinais Qt, entregues automaticamente na thread da GUI."""

    progresso = Signal(int, object)
    concluido = Signal(int, list, str, int)
    falhou = Signal(object)

    def __init__(self, caminho_planilha, caminho_banco, nome_tabela, aba_escolhida,
                 linha_cabecalho, parent=None):
        super().__init__(parent)
        self._caminho_planilha = caminho_planilha
        self._caminho_banco = caminho_banco
        self._nome_tabela = nome_tabela
        self._aba_escolhida = aba_escolhida
        self._linha_cabecalho = linha_cabecalho

    def run(self):
        try:
            n_linhas, colunas = importar_planilha(
                self._caminho_planilha, self._caminho_banco, self._nome_tabela,
                aba=self._aba_escolhida, linha_cabecalho=self._linha_cabecalho,
                progress_callback=lambda linhas, total: self.progresso.emit(linhas, total))
            self.concluido.emit(n_linhas, colunas, self._aba_escolhida, self._linha_cabecalho)
        except Exception as e:
            self.falhou.emit(e)


class _ThreadSimulacaoIntensidades(QThread):
    """Roda inteiramente numa thread de fundo (ver
    core/intensidades.py:rodar_simulacao) — conexão própria, não
    compartilha o objeto sqlite3 com a thread principal."""

    progresso = Signal(int, int)
    concluido = Signal(dict)
    falhou = Signal(object)

    def __init__(self, caminho_trabalho, mapeamento, parent=None):
        super().__init__(parent)
        self._caminho_trabalho = caminho_trabalho
        self._mapeamento = mapeamento

    def run(self):
        try:
            conn = conectar_caminho(self._caminho_trabalho)
            try:
                estatisticas = intensidades.rodar_simulacao(
                    conn, self._mapeamento,
                    progress_callback=lambda numero, total: self.progresso.emit(numero, total))
            finally:
                conn.close()
            self.concluido.emit(estatisticas)
        except Exception as e:
            self.falhou.emit(e)


class _ThreadCompactarBanco(QThread):
    """Roda inteiramente numa thread de fundo (ver
    core/db.py:executar_vacuum)."""

    # object, não int: tamanho em bytes de um projeto grande (este app
    # trabalha com bancos de vários GB) estoura o "int" de 32 bits que
    # Signal(int) mapeia no C++/Qt por baixo (limite ~2,147,483,647) —
    # PySide6/shiboken levanta OverflowError na hora do emit() nesse caso.
    # "object" passa o int do Python direto, sem conversão pro tipo C++,
    # sem limite de tamanho.
    concluido = Signal(object, object)
    falhou = Signal(object)

    def __init__(self, caminho_trabalho, parent=None):
        super().__init__(parent)
        self._caminho_trabalho = caminho_trabalho

    def run(self):
        try:
            tamanho_antes, tamanho_depois = executar_vacuum(self._caminho_trabalho)
            self.concluido.emit(tamanho_antes, tamanho_depois)
        except Exception as e:
            self.falhou.emit(e)


class TelaConfiguracoes(TelaBase):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._tabelas_extras_carregadas = []
        self._importando = {chave: False for chave, *_ in _BASES_IFC}
        self._threads_importacao = {}
        self._simulacao_rodando = False
        self._thread_simulacao = None
        self._compactando = False
        self._thread_compactar = None

        self._montar_form()
        self.recarregar_lista()

    # ---------------- formulário ----------------

    def _montar_form(self):
        layout_raiz = QVBoxLayout(self)
        layout_raiz.setContentsMargins(0, 0, 0, 0)

        # QScrollArea em vez de só um QWidget — sem isso, redimensionar a
        # janela menor que o conteúdo cortava os últimos campos, sem
        # nenhum jeito de rolar até eles.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout_raiz.addWidget(scroll)

        container = QWidget()
        layout_container = QVBoxLayout(container)
        layout_container.setContentsMargins(12, 12, 12, 12)
        layout_container.setSpacing(12)

        # Duas abas — "Parâmetros" (o que "Salvar configurações" grava,
        # exceto Sortimentos, que tem CRUD próprio) e "Operações" (importar
        # base, rodar Simulação de Intensidades, compactar banco). Antes
        # eram 9 cartões desiguais empilhados em só 2 colunas — misturava
        # configuração e operação na mesma vista e obrigava rolar muito
        # pra achar qualquer coisa. "Salvar configurações" continua FORA
        # das abas, sempre visível — grava os campos das duas, não só a
        # aba aberta no momento (ver salvar_registro).
        abas = QTabWidget()
        layout_container.addWidget(abas)

        aba_parametros = QWidget()
        abas.addTab(aba_parametros, "Parâmetros")
        self._montar_aba_parametros(aba_parametros)

        aba_operacoes = QWidget()
        abas.addTab(aba_operacoes, "Operações")
        self._montar_aba_operacoes(aba_operacoes)

        # Ação principal da tela, sempre por último e visualmente separada
        # das operações acima (que têm seu próprio botão específico) —
        # variante "salvar" (maior, negrito, cor de destaque, ver
        # app/theme/qss.py) pra não ser confundida com um botão de
        # operação. Fica dentro da área rolável, igual ao original
        # (PainelRolavel continha o container inteiro, barra_salvar incluída).
        separador = QFrame()
        separador.setFrameShape(QFrame.Shape.HLine)
        layout_container.addWidget(separador)

        barra_salvar = QHBoxLayout()
        barra_salvar.addStretch(1)
        botao_exportar_configuracoes = QPushButton("Exportar parametrizações...")
        icones.aplicar_icone(botao_exportar_configuracoes, "exportar")
        botao_exportar_configuracoes.setToolTip(
            "Um Excel com toda parametrização da tela Configurações, uma aba por seção: "
            "\"Parâmetros\" (campos da aba Parâmetros — os mesmos que \"Salvar configurações\" "
            "grava, com o valor ATUAL do formulário, mesmo que ainda não salvo), \"Sortimentos\", "
            "\"Custo de Formação\", \"Custo de Colheita\" e \"Produtividade Colheita\" (as 4 "
            "últimas, lidas do banco — têm CRUD próprio, já persistido). Só leitura — não muda "
            "nada no projeto.")
        botao_exportar_configuracoes.clicked.connect(self._exportar_configuracoes)
        barra_salvar.addWidget(botao_exportar_configuracoes)
        botao_salvar = QPushButton("Salvar configurações")
        qss.aplicar_variante(botao_salvar, "salvar")
        icones.aplicar_icone(botao_salvar, "salvar", cor="white")
        botao_salvar.clicked.connect(self.salvar_registro)
        barra_salvar.addWidget(botao_salvar)
        layout_container.addLayout(barra_salvar)

        scroll.setWidget(container)

    def _montar_aba_parametros(self, container):
        """Cartões que "Salvar configurações" grava, exceto Sortimentos/
        Custo de Formação/Custo de Colheita (CRUD próprio, persiste na
        hora — ver _montar_secao_sortimentos/_montar_secao_custo_formacao/
        _montar_secao_custo_colheita), mantidos aqui mesmo assim por serem
        conceitualmente parâmetros, não operações.

        Empilhado em linhas (não mais 2 colunas verticais inteiras): 1ª
        linha pareia "Classes diamétricas e manejo" com "Parâmetros
        econômicos" lado a lado (só campos, sem tabela — cabem bem numa
        largura de coluna); as 3 tabelas com preço/custo (Sortimentos,
        Custo de Formação, Custo de Colheita) vêm cada uma em largura
        CHEIA logo abaixo — Sortimentos tem 6 colunas hoje (Madeira
        Serrada/Madeira em Pé incluídas), Custo de Colheita tem cabeçalho
        longo (ex: "Disponibilidade Mecânica (%)") — meia largura deixava
        as 2 (`distribuir_igualmente=True` nas 3, ver Tabela) espremidas
        demais; última linha pareia os 2 cartões curtos que sobraram
        ("Dimensões da tora" e "Construtores de Variáveis")."""
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(16)

        # A edição deste conjunto agora vive na tela Simulação.
        # Os widgets continuam carregados fora da interface para preservar
        # a exportação e o salvamento geral desta tela.
        self._classes_manejo_ocultas = QWidget(self)
        self._montar_secao_classes_manejo(self._classes_manejo_ocultas)
        self._classes_manejo_ocultas.hide()

        cartao_economico = Cartao("Parâmetros econômicos")
        layout.addWidget(cartao_economico)
        self._montar_secao_economica(cartao_economico.corpo)

        # Mantém os valores disponíveis para salvar/exportar pela tela
        # Configurações, embora a edição visual agora viva em Sortimentos.
        self._dimensoes_tora_ocultas = QWidget(self)
        self._montar_secao_dimensoes_tora(self._dimensoes_tora_ocultas)
        self._dimensoes_tora_ocultas.hide()

        layout.addStretch(1)

    def _montar_aba_operacoes(self, container):
        """Simulação de intensidades (esquerda) e Importação de dados
        (direita) lado a lado — ambas autocontidas (cada uma com seu
        próprio botão/progresso/status), sem dependência de ordem uma da
        outra apesar do fluxo natural ser importar antes de rodar; embaixo
        das duas, Manutenção do banco, largura cheia (útil depois de
        qualquer uma das duas — ambas recriam tabelas inteiras)."""
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        linha_superior = QHBoxLayout()
        linha_superior.setSpacing(16)
        layout.addLayout(linha_superior)

        cartao_importacao = Cartao("Importação de dados")
        linha_superior.addWidget(cartao_importacao, 1)
        self._montar_secao_importacao(cartao_importacao.corpo)

        # Campos mantidos em memória para compatibilidade com salvar/
        # exportar parametrizações; a edição visual agora fica em Weibull.
        self._intensidades_ocultas = QWidget(self)
        self._montar_secao_intensidades(self._intensidades_ocultas)
        self._intensidades_ocultas.hide()

        cartao_manutencao = Cartao("Manutenção do banco")
        layout.addWidget(cartao_manutencao)
        self._montar_secao_manutencao(cartao_manutencao.corpo)
        layout.addStretch(1)

    @staticmethod
    def _linha_campo(layout, linha, rotulo, entry):
        layout.addWidget(QLabel(rotulo), linha, 0)
        layout.addWidget(entry, linha, 1)

    @staticmethod
    def _campo_empilhado(rotulo, entry):
        """Rótulo em cima do campo, os dois num QVBoxLayout só — bloco
        pronto pra entrar lado a lado com outros num QHBoxLayout (ver
        _montar_secao_sortimentos/_montar_secao_custo_formacao/
        _montar_secao_custo_colheita: os campos do formulário, um por
        coluna da tabela acima, numa linha horizontal só em vez de grade
        2D)."""
        coluna = QVBoxLayout()
        coluna.setSpacing(2)
        coluna.addWidget(QLabel(rotulo))
        coluna.addWidget(entry)
        return coluna

    @staticmethod
    def _definir_status(label, texto, chave_cor="neutro"):
        label.setText(texto)
        qss.aplicar_status(label, chave_cor)

    def _montar_secao_classes_manejo(self, container):
        layout = QGridLayout(container)
        layout.setColumnStretch(1, 1)
        linha = 0

        self.entry_primeira_classe = QLineEdit()
        self._linha_campo(layout, linha, "Primeira classe diamétrica", self.entry_primeira_classe)
        linha += 1

        self.entry_ultima_classe = QLineEdit()
        self._linha_campo(layout, linha, "Última classe diamétrica", self.entry_ultima_classe)
        linha += 1

        self.entry_idade_maxima_manejo = QLineEdit()
        self._linha_campo(layout, linha, "Idade máxima de manejo", self.entry_idade_maxima_manejo)
        linha += 1

        self.entry_numero_minimo_arvores = QLineEdit()
        self._linha_campo(
            layout, linha, "Número mínimo de árvores/ha ao final do manejo",
            self.entry_numero_minimo_arvores)
        linha += 1

        # Truncagem à esquerda no ajuste Weibull "Por Simulação" (tela
        # Weibull, botão "Ajustar com base na simulação de intensidades")
        # — ver core/weibull_fit.py:ajustar_a_partir_da_simulacao. Vivia
        # como checkbox solto na própria tela Weibull (não persistia,
        # voltava pro padrão desmarcado toda vez); virou config do
        # projeto pra ficar consistente entre sessões e entre a tela
        # "Weibull" e a aba "Por Simulação" de "Weibull IFC".
        self.checkbox_truncar_esquerda = QCheckBox(
            "Ajustar Weibull \"Por Simulação\" com truncagem à esquerda (desbaste por área basal)")
        self.checkbox_truncar_esquerda.setToolTip(
            "O MLE comum trata as árvores remanescentes como se fossem a população inteira, sem "
            "saber que as menores foram removidas pelo desbaste (por área basal) — isso infla o "
            "Shape estimado. Marcado, o botão \"Ajustar com base na simulação de intensidades\" "
            "(tela Weibull) usa o DAP mínimo remanescente do próprio grupo como ponto de corte e "
            "ajusta a Weibull truncada à esquerda nesse ponto, corrigindo esse viés.")
        # A edição visual desta preferência foi movida para
        # Weibull > Por Simulação. O widget permanece fora do layout para
        # preservar salvar/exportar parametrizações desta tela.
        self.checkbox_truncar_esquerda.setParent(container)
        self.checkbox_truncar_esquerda.hide()

        # Como a probabilidade por classe (S(classe-0,5)-S(classe+0,5))
        # normalmente não fecha em 1 (as classes configuradas não cobrem
        # a cauda inteira da Weibull), a diferença precisa ser distribuída
        # de algum jeito — ver simulacao.probabilidades_por_classe pras
        # duas fórmulas exatas. Afeta simulacao_distribuicao_diametrica
        # (tela Simulação) e qualquer nó "Distribuição Diamétrica" no
        # Construtor de Variáveis.
        self.combo_normalizacao_weibull = QComboBox()
        self.combo_normalizacao_weibull.addItem("Aditiva (padrão)", "aditiva")
        self.combo_normalizacao_weibull.addItem("Proporcional", "proporcional")
        self.combo_normalizacao_weibull.setToolTip(
            "Como fechar a soma das probabilidades por classe em 1 por talhão/idade. "
            "\"Aditiva\": soma a diferença que falta igualmente em toda classe — preserva a "
            "diferença absoluta entre classes, mas pode inflar desproporcionalmente uma classe "
            "de cauda quase zerada quando a diferença é grande (típico de shape baixo). "
            "\"Proporcional\": reescala cada classe pelo mesmo fator — preserva a forma relativa "
            "da curva, mas amplia mais as classes que já tinham maior probabilidade.")
        self._linha_campo(
            layout, linha, "Normalização da Weibull por classe", self.combo_normalizacao_weibull)
        linha += 1

        # Idade de Raleio/1º Desbaste/2º Desbaste é configurada como um
        # número fixo de anos, igual pra todo talhão — mas talhões plantados
        # em anos diferentes chegam nessa idade em anos-calendário
        # diferentes. Se esse ano-calendário já passou (antes de
        # ano_referencia, ver campo acima), não dá pra "voltar no tempo" pra
        # fazer o manejo: gerar_populacao empurra a idade daquele talhão pra
        # frente até cair em ano_referencia ou depois (ver
        # __idade_*_final__ em gerar_populacao). Corte Raso fica de fora
        # (idade fixa, não ajustada). Desligado por padrão: idade configurada
        # é usada ao pé da letra, igual pra todo talhão (comportamento de
        # sempre).
        self.checkbox_ajuste_manejo = QCheckBox(
            "Ajuste de manejo: empurrar Raleio/1º/2º Desbaste que caiam em ano-calendário já passado")
        self.checkbox_ajuste_manejo.setToolTip(
            "Se a idade configurada de Raleio/1º Desbaste/2º Desbaste cair, pro ano de plantio de "
            "um talhão específico, num ano-calendário anterior ao Ano de referência (acima), esse "
            "talhão não pode mais receber o manejo naquela idade (não dá pra voltar no tempo). "
            "Marcado, a idade efetiva desse talhão é empurrada pro primeiro ano que ainda cai em "
            "Ano de referência ou depois — a intensidade não muda, só a idade do evento. Não afeta "
            "o Corte Raso. Sem uma coluna de Data de plantio mapeada pro talhão, ou com a opção "
            "desligada, a idade usada é sempre a configurada, igual pra todo talhão.")
        layout.addWidget(self.checkbox_ajuste_manejo, linha, 0, 1, 2)
        linha += 1

        # Base do ajuste logístico (ITD, tela Ingressos e Curvas de
        # Distribuição): modelo y = a/(1+b·exp(-c·idade)) ajustado sobre
        # (idade, 1/IP) ou (idade, 1/IPM), ITD = ln(b)/c (ponto de
        # inflexão). Como IPM = IP/idade, IPM tende a já vir decrescente
        # desde a 1ª idade simulada (a divisão por uma idade sempre
        # crescente esconde o platô que pode existir no IP bruto antes do
        # povoamento realmente começar a se estabilizar) — nesse caso o
        # ajuste sobre 1/IPM empurra a ITD pra perto de zero mesmo quando
        # o IP bruto só começa a declinar bem mais tarde. "1/IP" (padrão)
        # ajusta em cima do Ingresso Percentual bruto em vez do IPM,
        # evitando essa distorção. Só afeta qual coluna alimenta o
        # ajuste — IP e IPM continuam sendo calculados e exibidos os
        # dois, sempre.
        self.combo_base_ajuste_logistico = QComboBox()
        self.combo_base_ajuste_logistico.addItem("1/IP (padrão)", "ip")
        self.combo_base_ajuste_logistico.addItem("1/IPM", "ipm")
        self.combo_base_ajuste_logistico.setToolTip(
            "Sobre qual coluna o ajuste logístico (ITD = ln(b)/c) roda. \"1/IP\" (padrão): "
            "ajusta sobre o inverso do Ingresso Percentual bruto. \"1/IPM\": ajusta sobre o "
            "inverso do Ingresso Percentual Médio (IP/idade) — a divisão por idade pode "
            "empurrar a ITD pra perto de zero em povoamentos onde o IP bruto fica estável por "
            "vários anos antes de declinar.")
        self._linha_campo(
            layout, linha, "Base do ajuste logístico (ITD)", self.combo_base_ajuste_logistico)
        linha += 1

        # Grandeza por classe usada no DD/IP do MIP (tela Ingressos e
        # Curvas de Distribuição): "fdp" compara a densidade pontual da
        # Weibull entre idades consecutivas (segue a Figura 3 de
        # Helfenstein, 2020 — curvas de densidade sobrepostas, DD no
        # cruzamento); "classe" compara a probabilidade/área por classe já
        # normalizada (mesma grandeza de simulacao_distribuicao_diametrica.
        # probabilidade, respeitando a normalização aditiva/proporcional
        # acima), mais próxima do texto de Leite et al. (2005).
        self.combo_base_calculo_mip = QComboBox()
        self.combo_base_calculo_mip.addItem("Densidade — fdp (padrão)", "fdp")
        self.combo_base_calculo_mip.addItem("Probabilidade por classe", "classe")
        self.combo_base_calculo_mip.setToolTip(
            "Qual grandeza por classe diamétrica o DD/IP do MIP compara entre idades "
            "consecutivas. \"Densidade — fdp\": densidade pontual da Weibull (altura da curva na "
            "classe) — padrão. \"Probabilidade por classe\": probabilidade/área por classe já "
            "normalizada pra somar 1 por idade (mesma normalização — aditiva/proporcional — "
            "configurada acima), a mesma grandeza usada em \"Curvas de Distribuição\".")
        self._linha_campo(
            layout, linha, "Grandeza por classe do MIP (DD/IP)", self.combo_base_calculo_mip)

    def _montar_secao_dimensoes_tora(self, container):
        layout = QGridLayout(container)
        layout.setColumnStretch(1, 1)
        linha = 0

        self.entry_comprimento_tora = QLineEdit()
        self._linha_campo(layout, linha, "Comprimento da tora (m)", self.entry_comprimento_tora)
        linha += 1

        self.entry_diametro_minimo_tora = QLineEdit()
        self._linha_campo(layout, linha, "Diâmetro mínimo (cm)", self.entry_diametro_minimo_tora)
        linha += 1

        # Troca o cálculo exato do nó "Afilamento" (grade fina/grossa
        # reintegrada do zero pra cada árvore, ver core/construtores.py:
        # _calcular_volumes_afilamento) por uma busca numa tabela
        # pré-calculada por classe diamétrica (ver _obter_tabela_
        # afilamento) — bem mais rápido num lote com muitos cenários (a
        # tabela é calculada 1x por classe e reaproveitada por todo
        # cenário/linha dali em diante), à custa de uma aproximação
        # pequena mas real: a altura de cada árvore entra arredondada pro
        # passo de 0,1m mais próximo da tabela, não exata — a equação usa
        # a altura como variável (não só como limite de integração), então
        # arredondar muda um pouco a curva calculada, mais perceptível
        # perto da borda de fechar ou não a última tora. Desligado por
        # padrão.
        self.checkbox_usar_tabela_afilamento = QCheckBox(
            "Acelerar cálculo do Afilamento com tabela pré-calculada por classe diamétrica")
        self.checkbox_usar_tabela_afilamento.setToolTip(
            "Cálculo exato (padrão, desligado): reintegra a curva de afilamento do zero pra cada "
            "árvore, sem aproximação. Ligado: pré-calcula uma tabela por classe diamétrica (altura "
            "arredondada pro passo de 0,1m mais próximo) e busca nela em vez de reintegrar — bem "
            "mais rápido em lotes com muitos cenários (\"Múltiplos cenários\"/\"Grade automática\"), "
            "mas introduz uma aproximação pequena: a altura de cada árvore entra na equação "
            "arredondada, não exata, o que pode mudar por pouco se a última tora fecha o diâmetro "
            "mínimo ou não perto da borda.")
        layout.addWidget(self.checkbox_usar_tabela_afilamento, linha, 0, 1, 2)

    def _montar_secao_sortimentos(self, container):
        """Lista de sortimentos (classificação comercial de toras por faixa
        de diâmetro, ex: "Fino" 8-15cm) — cada um com nome + limite
        inferior/superior + rendimento (%, ex: rendimento no desdobro pra
        aquela classe de tora) + dois preços (Madeira Serrada — produto já
        desdobrado — e Madeira em Pé — árvore em pé antes da colheita/
        desdobro), cadastrados e persistidos na hora (tabela `sortimentos`,
        própria — não faz parte da linha única de `configuracoes` — ver
        core/db.py), independente do botão "Salvar configurações" desta
        tela, igual o CRUD da tela Modelos. O nó "Receita Total" do
        Construtor de Variáveis escolhe qual dos dois preços usar (botão
        direito no nó, padrão Madeira Serrada)."""
        layout = QVBoxLayout(container)

        self.tabela_sortimentos = Tabela(
            colunas=(
                "nome", "limite_inferior", "limite_superior", "rendimento", "preco", "preco_pe"),
            tipos_iniciais={
                "limite_inferior": "Float", "limite_superior": "Float", "rendimento": "Float",
                "preco": "Float", "preco_pe": "Float"},
            rotulos={
                "nome": "Nome", "limite_inferior": "Limite inferior", "limite_superior": "Limite superior",
                "rendimento": "Rendimento (%)", "preco": "Madeira Serrada (R$/m³)",
                "preco_pe": "Madeira em Pé (R$/m³)",
            },
            distribuir_igualmente=True,
        )
        self.tabela_sortimentos.setFixedHeight(170)
        self.tabela_sortimentos.selecaoAlterada.connect(self._ao_selecionar_sortimento)
        layout.addWidget(self.tabela_sortimentos)

        # Todos os campos numa linha horizontal só, um por coluna da
        # tabela acima (mesma ordem) — ver _campo_empilhado. Sem esticar
        # (addStretch no final): os campos são curtos, não precisam
        # ocupar a largura toda do card, mesmo agora que o card é largura
        # cheia (ver _montar_aba_parametros).
        linha_campos = QHBoxLayout()
        layout.addLayout(linha_campos)

        self.entry_sortimento_nome = QLineEdit()
        self.entry_sortimento_nome.setMaximumWidth(130)
        linha_campos.addLayout(
            self._campo_empilhado("Nome", self.entry_sortimento_nome), 1)

        self.entry_sortimento_limite_inferior = _criar_entrada_numerica()
        self.entry_sortimento_limite_inferior.setMaximumWidth(16777215)
        linha_campos.addLayout(
            self._campo_empilhado("Limite inferior", self.entry_sortimento_limite_inferior), 1)

        self.entry_sortimento_limite_superior = _criar_entrada_numerica()
        self.entry_sortimento_limite_superior.setMaximumWidth(16777215)
        linha_campos.addLayout(
            self._campo_empilhado("Limite superior", self.entry_sortimento_limite_superior), 1)

        self.entry_sortimento_rendimento = _criar_entrada_numerica()
        self.entry_sortimento_rendimento.setMaximumWidth(16777215)
        linha_campos.addLayout(
            self._campo_empilhado("Rendimento (%)", self.entry_sortimento_rendimento), 1)

        self.entry_sortimento_preco = _criar_entrada_numerica()
        self.entry_sortimento_preco.setMaximumWidth(16777215)
        linha_campos.addLayout(
            self._campo_empilhado("Madeira Serrada (R$/m³)", self.entry_sortimento_preco), 1)

        self.entry_sortimento_preco_pe = _criar_entrada_numerica()
        self.entry_sortimento_preco_pe.setMaximumWidth(16777215)
        linha_campos.addLayout(
            self._campo_empilhado("Madeira em Pé (R$/m³)", self.entry_sortimento_preco_pe), 1)

        barra_botoes = QHBoxLayout()
        botao_adicionar = QPushButton("Adicionar")
        icones.aplicar_icone(botao_adicionar, "adicionar")
        botao_adicionar.clicked.connect(self._adicionar_sortimento)
        barra_botoes.addWidget(botao_adicionar)
        botao_salvar_sortimento = QPushButton("Salvar")
        icones.aplicar_icone(botao_salvar_sortimento, "salvar")
        botao_salvar_sortimento.clicked.connect(self._salvar_sortimento)
        barra_botoes.addWidget(botao_salvar_sortimento)
        botao_excluir = QPushButton("Excluir")
        qss.aplicar_variante(botao_excluir, "perigo")
        icones.aplicar_icone(botao_excluir, "excluir")
        botao_excluir.clicked.connect(self._excluir_sortimento)
        barra_botoes.addWidget(botao_excluir)
        botao_limpar = QPushButton("Limpar")
        icones.aplicar_icone(botao_limpar, "limpar")
        botao_limpar.clicked.connect(self._limpar_form_sortimento)
        barra_botoes.addWidget(botao_limpar)
        barra_botoes.addStretch(1)
        layout.addLayout(barra_botoes)

    def _montar_secao_custo_formacao(self, container):
        """Lista de custos de formação florestal (preparo de solo, plantio,
        manutenção etc.), cada um com nome + ano (idade do povoamento,
        contada desde o plantio — casa com idade_simulada, não com o
        ano-calendário) + custo (R$/ha), cadastrados e persistidos na hora
        (tabela `custos_formacao`, própria, mesmo padrão CRUD de
        Sortimentos acima). Não entra em coluna nenhuma sozinho — quem lê
        essa tabela é o nó "Custo de Formação" do Construtor de Variáveis
        (soma, por idade, todo custo cujo `ano` bate com idade_simulada da
        linha — ver core/construtores.py:avaliar_grafo/
        obter_custos_formacao), que o usuário precisa montar e salvar lá
        pra virar coluna na população."""
        layout = QVBoxLayout(container)

        self.tabela_custo_formacao = Tabela(
            colunas=("nome", "ano", "custo"),
            tipos_iniciais={"ano": "Float", "custo": "Float"},
            rotulos={"ano": "Idade do Povoamento"},
            distribuir_igualmente=True,
        )
        self.tabela_custo_formacao.setFixedHeight(140)
        self.tabela_custo_formacao.selecaoAlterada.connect(self._ao_selecionar_custo_formacao)
        layout.addWidget(self.tabela_custo_formacao)

        # Todos os campos numa linha horizontal só, mesmo padrão de
        # _montar_secao_sortimentos (ver _campo_empilhado) — sem esticar,
        # os campos são curtos.
        linha_campos = QHBoxLayout()
        layout.addLayout(linha_campos)

        self.entry_custo_formacao_nome = QLineEdit()
        linha_campos.addLayout(
            self._campo_empilhado("Nome", self.entry_custo_formacao_nome), 2)

        self.entry_custo_formacao_ano = QLineEdit()
        coluna_ano = self._campo_empilhado("Idade do Povoamento", self.entry_custo_formacao_ano)
        coluna_ano.itemAt(0).widget().setToolTip(
            "Idade do povoamento (anos desde o plantio, ex: 0, 1, 2) em que esse custo é "
            "incorrido — casa com idade_simulada de cada linha da simulação, não com o "
            "ano-calendário.")
        linha_campos.addLayout(coluna_ano, 1)

        self.entry_custo_formacao_custo = QLineEdit()
        linha_campos.addLayout(
            self._campo_empilhado("Custo (R$/ha)", self.entry_custo_formacao_custo), 1)

        barra_botoes = QHBoxLayout()
        botao_adicionar = QPushButton("Adicionar")
        icones.aplicar_icone(botao_adicionar, "adicionar")
        botao_adicionar.clicked.connect(self._adicionar_custo_formacao)
        barra_botoes.addWidget(botao_adicionar)
        botao_salvar_custo_formacao = QPushButton("Salvar")
        icones.aplicar_icone(botao_salvar_custo_formacao, "salvar")
        botao_salvar_custo_formacao.clicked.connect(self._salvar_custo_formacao)
        barra_botoes.addWidget(botao_salvar_custo_formacao)
        botao_excluir = QPushButton("Excluir")
        qss.aplicar_variante(botao_excluir, "perigo")
        icones.aplicar_icone(botao_excluir, "excluir")
        botao_excluir.clicked.connect(self._excluir_custo_formacao)
        barra_botoes.addWidget(botao_excluir)
        botao_limpar = QPushButton("Limpar")
        icones.aplicar_icone(botao_limpar, "limpar")
        botao_limpar.clicked.connect(self._limpar_form_custo_formacao)
        barra_botoes.addWidget(botao_limpar)
        barra_botoes.addStretch(1)
        layout.addLayout(barra_botoes)

    def _montar_secao_custo_colheita(self, container):
        """Lista de custos de colheita (corte, baldeio, transporte etc.),
        cada um com nome + os parâmetros da fórmula clássica de custo de
        colheita florestal (Custo Hora Máquina / (Produtividade ×
        Disponibilidade Mecânica × Eficiência Operacional) = custo por
        m³) — cadastrados e persistidos na hora (tabela `custos_colheita`,
        própria, mesmo padrão CRUD de Custo de formação florestal acima,
        só sem o campo de idade). Produtividade não é um valor único (varia
        por classe diamétrica) — em vez de campo no formulário, a coluna
        "Produtividade" da lista acima tem um botão "+" POR LINHA (embutido
        via `view.setIndexWidget`, re-embutido a cada
        `tabela_custo_colheita.conteudoAtualizado` — ver
        _atualizar_botoes_produtividade) que abre um diálogo com uma linha
        por classe pra ESSE registro específico (ver
        _abrir_dialogo_produtividade), lido/gravado direto no banco
        (tabela `custo_colheita_produtividade`) assim que o diálogo fecha —
        não depende de "Adicionar"/"Salvar" nem existe pra um registro
        ainda não cadastrado (precisa ter id). Custo Hora Máquina/
        Disponibilidade/Eficiência continuam guardados como parâmetros, não
        o custo por m³ já calculado (não fica salvo em lugar nenhum — só
        exibido, recalculado na hora, no diálogo de produtividade) — sem
        aplicação automática na simulação por enquanto."""
        layout = QVBoxLayout(container)

        self.tabela_custo_colheita = Tabela(
            colunas=("nome", "custo_hora_maquina", "disponibilidade_mecanica",
                     "eficiencia_operacional", "produtividade"),
            tipos_iniciais={
                "custo_hora_maquina": "Float",
                "disponibilidade_mecanica": "Float", "eficiencia_operacional": "Float",
            },
            rotulos={
                "custo_hora_maquina": "Custo Hora Máquina (R$/HM)",
                "disponibilidade_mecanica": "Disponibilidade Mecânica (%)",
                "eficiencia_operacional": "Eficiência Operacional (%)",
                "produtividade": "Produtividade",
            },
            distribuir_igualmente=True,
        )
        self.tabela_custo_colheita.setFixedHeight(140)
        self.tabela_custo_colheita.selecaoAlterada.connect(self._ao_selecionar_custo_colheita)
        self.tabela_custo_colheita.conteudoAtualizado.connect(self._atualizar_botoes_produtividade)
        layout.addWidget(self.tabela_custo_colheita)

        # Todos os campos numa linha horizontal só, mesmo padrão de
        # _montar_secao_sortimentos (ver _campo_empilhado) — sem esticar,
        # os campos são curtos. "Produtividade" fica de fora (não é campo
        # de formulário — botão "+" por linha na tabela acima, ver
        # docstring desta função).
        linha_campos = QHBoxLayout()
        layout.addLayout(linha_campos)

        self.entry_custo_colheita_nome = QLineEdit()
        linha_campos.addLayout(
            self._campo_empilhado("Nome", self.entry_custo_colheita_nome), 2)

        self.entry_custo_colheita_custo_hora_maquina = QLineEdit()
        linha_campos.addLayout(self._campo_empilhado(
            "Custo Hora Máquina (R$/HM)", self.entry_custo_colheita_custo_hora_maquina), 1)

        self.entry_custo_colheita_disponibilidade_mecanica = QLineEdit()
        linha_campos.addLayout(self._campo_empilhado(
            "Disponibilidade Mecânica (%)", self.entry_custo_colheita_disponibilidade_mecanica), 1)

        self.entry_custo_colheita_eficiencia_operacional = QLineEdit()
        linha_campos.addLayout(self._campo_empilhado(
            "Eficiência Operacional (%)", self.entry_custo_colheita_eficiencia_operacional), 1)

        barra_botoes = QHBoxLayout()
        botao_adicionar = QPushButton("Adicionar")
        icones.aplicar_icone(botao_adicionar, "adicionar")
        botao_adicionar.clicked.connect(self._adicionar_custo_colheita)
        barra_botoes.addWidget(botao_adicionar)
        botao_salvar_custo_colheita = QPushButton("Salvar")
        icones.aplicar_icone(botao_salvar_custo_colheita, "salvar")
        botao_salvar_custo_colheita.clicked.connect(self._salvar_custo_colheita)
        barra_botoes.addWidget(botao_salvar_custo_colheita)
        botao_excluir = QPushButton("Excluir")
        qss.aplicar_variante(botao_excluir, "perigo")
        icones.aplicar_icone(botao_excluir, "excluir")
        botao_excluir.clicked.connect(self._excluir_custo_colheita)
        barra_botoes.addWidget(botao_excluir)
        botao_limpar = QPushButton("Limpar")
        icones.aplicar_icone(botao_limpar, "limpar")
        botao_limpar.clicked.connect(self._limpar_form_custo_colheita)
        barra_botoes.addWidget(botao_limpar)
        barra_botoes.addStretch(1)
        layout.addLayout(barra_botoes)

    @staticmethod
    def _custo_efetivo_colheita(produtividade, custo_hora_maquina, disponibilidade, eficiencia):
        """Custo efetivo (R$/m³) duma classe: Custo Hora Máquina / (Produtividade
        × Disponibilidade Mecânica × Eficiência Operacional) — fórmula
        clássica de custo de colheita florestal (quanto menor a
        disponibilidade/eficiência, MAIOR o custo por m³: máquina com mais
        parada custa mais por m³ produzido). Disponibilidade/eficiência
        entram como fração (divididas por 100 aqui — cadastradas em %,
        mesmo padrão de "Rendimento (%)" em Sortimentos). None se faltar
        algum dos 4 valores ou o denominador der zero (não dá pra calcular)."""
        if None in (produtividade, custo_hora_maquina, disponibilidade, eficiencia):
            return None
        denominador = produtividade * (disponibilidade / 100.0) * (eficiencia / 100.0)
        if denominador == 0:
            return None
        return custo_hora_maquina / denominador

    def _atualizar_botoes_produtividade(self):
        """Reembute o botão "+" na coluna "produtividade" de cada linha
        visível de tabela_custo_colheita — chamado a cada
        `conteudoAtualizado` (definir_linhas/ordenar/filtrar resetam o
        modelo, o que derruba qualquer index widget encaixado antes)."""
        tabela = self.tabela_custo_colheita
        indice_coluna = tabela.colunas.index("produtividade")
        for linha, (iid, _valores, eh_resumo) in enumerate(tabela.linhas_visiveis()):
            if eh_resumo or iid == IID_RESUMO:
                continue
            botao = QPushButton("+")
            botao.setFixedWidth(28)
            icones.aplicar_icone(botao, "adicionar")
            botao.clicked.connect(
                lambda _checked=False, id_=int(iid): self._ao_clicar_botao_produtividade(id_))
            indice = tabela.view.model().index(linha, indice_coluna)
            tabela.view.setIndexWidget(indice, botao)

    def _ao_clicar_botao_produtividade(self, id_custo_colheita):
        self.tabela_custo_colheita.selecionar_id(id_custo_colheita)
        self._abrir_dialogo_produtividade(id_custo_colheita)

    def _abrir_dialogo_produtividade(self, id_custo_colheita):
        """Diálogo de produtividade por classe diamétrica PRA UM custo de
        colheita já cadastrado (`id_custo_colheita`) — lê/grava direto em
        custo_colheita_produtividade assim que fecha, sem depender do
        formulário nem de "Adicionar"/"Salvar" (por isso só existe um
        botão por LINHA já existente da lista, ver
        _atualizar_botoes_produtividade — um registro novo, ainda não
        salvo, não tem id pra gravar)."""
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            try:
                classes = simulacao.obter_classes_diametricas(conn)
            except ValueError as e:
                QMessageBox.warning(self, "Configurações", str(e))
                return
            row = conn.execute(
                "SELECT custo_hora_maquina, disponibilidade_mecanica, eficiencia_operacional "
                "FROM custos_colheita WHERE id = ?",
                (id_custo_colheita,),
            ).fetchone()
            if row is None:
                return
            custo_hora_maquina, disponibilidade, eficiencia = row
            produtividade_atual = dict(conn.execute(
                "SELECT classe, produtividade FROM custo_colheita_produtividade "
                "WHERE custo_colheita_id = ? AND produtividade IS NOT NULL",
                (id_custo_colheita,),
            ).fetchall())
        finally:
            conn.close()

        dialogo = QDialog(self)
        dialogo.setWindowTitle("Produtividade por classe diamétrica")
        dialogo.resize(420, 420)
        layout = QVBoxLayout(dialogo)
        layout.addWidget(QLabel(
            "Produtividade (m³/HM) de cada classe diamétrica — o Custo Efetivo "
            "(R$/m³) recalcula sozinho ao digitar."))

        tabela = QTableWidget(len(classes), 3)
        tabela.setHorizontalHeaderLabels(["Classe", "Produtividade (m³/HM)", "Custo Efetivo (R$/m³)"])
        tabela.verticalHeader().setVisible(False)

        def _item_nao_editavel(texto):
            item = QTableWidgetItem(texto)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            return item

        def _ler_float_item(item):
            if item is None:
                return None
            texto = item.text().strip()
            if not texto:
                return None
            try:
                return float(texto.replace(",", "."))
            except ValueError:
                return None

        def _recalcular_linha(linha):
            produtividade = _ler_float_item(tabela.item(linha, 1))
            custo_efetivo = self._custo_efetivo_colheita(
                produtividade, custo_hora_maquina, disponibilidade, eficiencia)
            tabela.blockSignals(True)
            tabela.setItem(
                linha, 2,
                _item_nao_editavel(_formatar_numero(custo_efetivo) if custo_efetivo is not None else "—"))
            tabela.blockSignals(False)

        for linha, classe in enumerate(classes):
            tabela.setItem(linha, 0, _item_nao_editavel(_formatar_numero(classe)))
            valor_atual = produtividade_atual.get(classe)
            tabela.setItem(linha, 1, QTableWidgetItem(_formatar_numero(valor_atual)))
            _recalcular_linha(linha)

        def _ao_mudar_item(item):
            if item.column() == 1:
                _recalcular_linha(item.row())

        tabela.itemChanged.connect(_ao_mudar_item)
        layout.addWidget(emoldurar_tabela(tabela))

        resultado = {}

        def confirmar():
            novo = {}
            for linha, classe in enumerate(classes):
                texto = tabela.item(linha, 1).text().strip()
                if not texto:
                    continue
                try:
                    novo[classe] = float(texto.replace(",", "."))
                except ValueError:
                    QMessageBox.warning(
                        dialogo, "Produtividade por classe diamétrica",
                        f"Produtividade inválida na classe {_formatar_numero(classe)}: '{texto}'.")
                    return
            resultado["valor"] = novo
            dialogo.accept()

        botoes = QDialogButtonBox()
        botoes.addButton("Concluir", QDialogButtonBox.ButtonRole.AcceptRole)
        botoes.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        botoes.accepted.connect(confirmar)
        botoes.rejected.connect(dialogo.reject)
        layout.addWidget(botoes)

        if dialogo.exec() != QDialog.DialogCode.Accepted:
            return

        # Grava na hora (substitui: DELETE + INSERT) — esse diálogo não
        # depende de "Adicionar"/"Salvar" do formulário principal.
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            conn.execute(
                "DELETE FROM custo_colheita_produtividade WHERE custo_colheita_id = ?",
                (id_custo_colheita,))
            novo = resultado["valor"]
            if novo:
                conn.executemany(
                    "INSERT INTO custo_colheita_produtividade "
                    "(custo_colheita_id, classe, produtividade) VALUES (?, ?, ?)",
                    [(id_custo_colheita, classe, valor) for classe, valor in novo.items()],
                )
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()

    def _montar_secao_economica(self, container):
        layout = QGridLayout(container)
        layout.setColumnStretch(1, 1)
        linha = 0

        self.entry_taxa_desconto = QLineEdit()
        self._linha_campo(layout, linha, "Taxa de desconto (%)", self.entry_taxa_desconto)
        linha += 1

        self.entry_ano_referencia = QLineEdit()
        self._linha_campo(layout, linha, "Ano de referência da análise", self.entry_ano_referencia)
        linha += 1

        # Contra o que o nó "VPL" (Construtor de Variáveis) conta o
        # expoente do desconto financeiro — ver
        # core/simulacao.py:BASES_PERIODO_VPL/core/construtores.py:
        # avaliar_grafo, ramo "vpl_sortimento".
        self.combo_base_periodo_vpl = QComboBox()
        self.combo_base_periodo_vpl.addItem("Ano de referência (padrão)", "ano_referencia")
        self.combo_base_periodo_vpl.addItem("Ano Zero", "ano_zero")
        self.combo_base_periodo_vpl.setToolTip(
            "Contra o que o nó VPL (Construtor de Variáveis) conta os anos de desconto. \"Ano "
            "de referência\": desconta contra o ano de referência acima (n = ano_simulado - "
            "ano de referência, sem valor absoluto — receita ANTES do ano de referência é "
            "composta pra frente em vez de descontada) E desconta PIS+COFINS (pressupõe receita "
            "de venda tributável); a 2ª entrada do nó VPL, nesse modo, deve ser ano_simulado. "
            "\"Ano Zero\": desconta contra o plantio do próprio talhão (VPL = "
            "RT/(1+taxa)^idade_simulada) em vez de um ano-calendário fixo igual pra todos, SEM "
            "descontar PIS+COFINS — não usa \"Ano de referência\" nem PIS/COFINS acima; a 2ª "
            "entrada do nó VPL, nesse modo, deve ser idade_simulada.")
        self._linha_campo(layout, linha, "Base do período do VPL", self.combo_base_periodo_vpl)
        linha += 1

        self.entry_pis = QLineEdit()
        self._linha_campo(layout, linha, "PIS (%)", self.entry_pis)
        linha += 1

        self.entry_cofins = QLineEdit()
        self._linha_campo(layout, linha, "COFINS (%)", self.entry_cofins)
        linha += 1

        self.entry_funrural = QLineEdit()
        self._linha_campo(layout, linha, "Funrural (%)", self.entry_funrural)
        linha += 1

        self.entry_dias_trabalho = QLineEdit()
        self._linha_campo(layout, linha, "Dias de Trabalho", self.entry_dias_trabalho)
        linha += 1

        self.entry_horas_trabalho = QLineEdit()
        self._linha_campo(layout, linha, "Horas de Trabalho", self.entry_horas_trabalho)

    def _montar_secao_construtores(self, container):
        """Gerenciamento (duplicar/excluir/ativar-desativar) dos
        construtores de variáveis salvos (grafo montado na tela Construtor
        de Variáveis, ver app/screens/construtor_variaveis.py) — só isso;
        criar/editar o grafo continua só naquela tela, que tem seu próprio
        botão "Construtores" (menu flutuante) pra escolher qual abrir no
        canvas."""
        layout = QVBoxLayout(container)

        self.tabela_construtores = Tabela(
            colunas=("nome", "tabela_origem", "ativo"),
            rotulos={"tabela_origem": "Tabela de origem", "ativo": "Ativo"},
            distribuir_igualmente=True,
        )
        self.tabela_construtores.setFixedHeight(140)
        layout.addWidget(self.tabela_construtores)

        barra_botoes = QHBoxLayout()
        botao_duplicar_construtor = QPushButton("Duplicar")
        icones.aplicar_icone(botao_duplicar_construtor, "duplicar")
        botao_duplicar_construtor.clicked.connect(self._duplicar_construtor)
        botao_duplicar_construtor.setToolTip(
            "Salva uma cópia do construtor selecionado (mesmo canvas/grafo) com outro nome, "
            "desativada — útil pra testar uma variação sem mexer no original. Abra a tela "
            "Construtor de Variáveis (botão \"Construtores\") pra editar ou ativar a cópia.")
        barra_botoes.addWidget(botao_duplicar_construtor)
        botao_excluir_construtor = QPushButton("Excluir")
        qss.aplicar_variante(botao_excluir_construtor, "perigo")
        icones.aplicar_icone(botao_excluir_construtor, "excluir")
        botao_excluir_construtor.clicked.connect(self._excluir_construtor)
        barra_botoes.addWidget(botao_excluir_construtor)
        layout.addLayout(barra_botoes)
        botao_ativo_construtor = QPushButton("Ativar/Desativar selecionado")
        icones.aplicar_icone(botao_ativo_construtor, "ativar_desativar")
        botao_ativo_construtor.clicked.connect(self._alternar_ativo_construtor)
        layout.addWidget(botao_ativo_construtor)

    def _montar_secao_importacao(self, container):
        layout = QVBoxLayout(container)

        self._widgets_base = {}
        for chave, titulo, _nome_tabela, _palavras in _BASES_IFC:
            linha_widgets = QHBoxLayout()

            rotulo = QLabel(titulo)
            rotulo.setMinimumWidth(140)
            linha_widgets.addWidget(rotulo)

            botao = QPushButton("Importar planilha/CSV...")
            icones.aplicar_icone(botao, "importar")
            botao.clicked.connect(lambda _checked=False, c=chave: self._importar_base(c))
            linha_widgets.addWidget(botao)

            label = QLabel("")
            linha_widgets.addWidget(label)

            # Excel não emite nenhum evento de progresso (pandas/openpyxl
            # materializa a planilha inteira antes de retornar) e CSV
            # emite só a contagem de linhas sem total conhecido (ver
            # core/importador.py) — sem um "% concluído" real pra nenhum
            # dos dois casos, indeterminado (setRange(0, 0)) é a única
            # opção honesta aqui.
            progressbar = QProgressBar()
            progressbar.setRange(0, 0)
            progressbar.setMinimumWidth(120)
            progressbar.setMaximumWidth(120)
            progressbar.setVisible(False)
            linha_widgets.addWidget(progressbar)
            linha_widgets.addStretch(1)

            layout.addLayout(linha_widgets)
            self._widgets_base[chave] = {"botao": botao, "label": label, "progressbar": progressbar}

    def _montar_secao_intensidades(self, container):
        layout = QGridLayout(container)
        layout.setColumnStretch(1, 1)
        linha = 0

        self.entry_passo_intensidade = QLineEdit()
        self._linha_campo(layout, linha, "Passo da intensidade (%)", self.entry_passo_intensidade)
        linha += 1

        self.entry_intensidade_minima = QLineEdit()
        self._linha_campo(layout, linha, "Intensidade mínima (%)", self.entry_intensidade_minima)
        linha += 1

        self.entry_intensidade_maxima = QLineEdit()
        self._linha_campo(layout, linha, "Intensidade máxima (%)", self.entry_intensidade_maxima)
        linha += 1

        linha_botao = QHBoxLayout()
        self.botao_rodar_intensidades = QPushButton("Rodar simulação de intensidades...")
        icones.aplicar_icone(self.botao_rodar_intensidades, "gerar")
        self.botao_rodar_intensidades.clicked.connect(self._rodar_intensidades)
        linha_botao.addWidget(self.botao_rodar_intensidades)
        self.label_status_intensidades = QLabel("")
        linha_botao.addWidget(self.label_status_intensidades)
        linha_botao.addStretch(1)
        layout.addLayout(linha_botao, linha, 0, 1, 2)
        linha += 1

        self.progressbar_intensidades = QProgressBar()
        self.progressbar_intensidades.setVisible(False)
        layout.addWidget(self.progressbar_intensidades, linha, 0, 1, 2)
        linha += 1

        self.label_faixa_intensidades = QLabel("")
        self.label_faixa_intensidades.setWordWrap(True)
        layout.addWidget(self.label_faixa_intensidades, linha, 0, 1, 2)
        linha += 1

        self._labels_contagem_intensidades = {}
        for rotulo, nome_tabela in _TABELAS_CONTAGEM_INTENSIDADES:
            linha_contagem = QHBoxLayout()
            linha_contagem.addWidget(QLabel(f"Linhas geradas por {rotulo}:"))
            label = QLabel("—")
            linha_contagem.addWidget(label)
            linha_contagem.addStretch(1)
            layout.addLayout(linha_contagem, linha, 0, 1, 2)
            self._labels_contagem_intensidades[nome_tabela] = label
            linha += 1

    def _montar_secao_manutencao(self, container):
        layout = QVBoxLayout(container)

        rotulo = QLabel(
            "Operações como \"Rodar simulação de intensidades\" e \"Gerar simulação\" "
            "recriam tabelas inteiras (DROP + CREATE) — o espaço da versão antiga fica "
            "reservado no arquivo do projeto, mesmo sem uso, até compactar.")
        rotulo.setWordWrap(True)
        layout.addWidget(rotulo)

        linha_botao = QHBoxLayout()
        self.botao_compactar = QPushButton("Compactar banco de dados...")
        icones.aplicar_icone(self.botao_compactar, "compactar")
        self.botao_compactar.clicked.connect(self._compactar_banco)
        linha_botao.addWidget(self.botao_compactar)
        self.label_status_compactacao = QLabel("")
        linha_botao.addWidget(self.label_status_compactacao)
        linha_botao.addStretch(1)
        layout.addLayout(linha_botao)

        self.progressbar_compactacao = QProgressBar()
        self.progressbar_compactacao.setRange(0, 0)
        self.progressbar_compactacao.setMaximumWidth(160)
        self.progressbar_compactacao.setVisible(False)
        layout.addWidget(self.progressbar_compactacao)

        separador = QFrame()
        separador.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separador)

        rotulo_extras = QLabel(
            "Tabelas do arquivo de trabalho além das essenciais: as de cada cenário do modo "
            "\"Múltiplos cenários\" (tela Simulação) e qualquer sobra que o app não reconhece "
            "mais (ex: de um recurso removido, ou de uma limpeza de cenário que falhou) — nunca "
            "inclui a tabela \"ativa\" que o app usa normalmente. Selecione uma ou mais "
            "(Ctrl/Shift-clique) para excluir.")
        rotulo_extras.setWordWrap(True)
        layout.addWidget(rotulo_extras)

        self.tabela_tabelas_extras = Tabela(
            colunas=("tabela", "linhas"), rotulos={"tabela": "Tabela", "linhas": "Linhas"},
            distribuir_igualmente=True,
        )
        self.tabela_tabelas_extras.setFixedHeight(140)
        layout.addWidget(self.tabela_tabelas_extras)

        linha_botoes_extras = QHBoxLayout()
        botao_atualizar_extras = QPushButton("Atualizar lista")
        icones.aplicar_icone(botao_atualizar_extras, "atualizar")
        botao_atualizar_extras.clicked.connect(self._carregar_tabelas_extras)
        linha_botoes_extras.addWidget(botao_atualizar_extras)
        botao_excluir_extras = QPushButton("Excluir selecionada(s)...")
        qss.aplicar_variante(botao_excluir_extras, "perigo")
        icones.aplicar_icone(botao_excluir_extras, "excluir")
        botao_excluir_extras.clicked.connect(self._excluir_tabelas_extras)
        linha_botoes_extras.addWidget(botao_excluir_extras)
        linha_botoes_extras.addStretch(1)
        layout.addLayout(linha_botoes_extras)

    # ---------------- lógica: campos numéricos ----------------

    def novo_registro(self):
        # esta tela não tem múltiplos registros pra "limpar" — existe só
        # pra manter a mesma interface das outras telas (chamada ao trocar
        # de projeto, antes de recarregar_lista popular com os dados reais)
        self.entry_primeira_classe.setText("")
        self.entry_ultima_classe.setText("")
        self.entry_idade_maxima_manejo.setText("")
        self.entry_numero_minimo_arvores.setText("")
        self.entry_taxa_desconto.setText("")
        self.entry_ano_referencia.setText("")
        self.combo_base_periodo_vpl.setCurrentIndex(
            self.combo_base_periodo_vpl.findData("ano_referencia"))
        self.entry_pis.setText("")
        self.entry_cofins.setText("")
        self.entry_funrural.setText("")
        self.entry_dias_trabalho.setText("")
        self.entry_horas_trabalho.setText("")
        self.entry_passo_intensidade.setText("")
        self.entry_intensidade_minima.setText("")
        self.entry_intensidade_maxima.setText("")
        self.entry_comprimento_tora.setText("")
        self.entry_diametro_minimo_tora.setText("")
        self.checkbox_usar_tabela_afilamento.setChecked(False)
        self.checkbox_truncar_esquerda.setChecked(False)
        self.combo_normalizacao_weibull.setCurrentIndex(
            self.combo_normalizacao_weibull.findData("aditiva"))
        self.checkbox_ajuste_manejo.setChecked(False)
        self.combo_base_ajuste_logistico.setCurrentIndex(
            self.combo_base_ajuste_logistico.findData("ip"))
        self.combo_base_calculo_mip.setCurrentIndex(
            self.combo_base_calculo_mip.findData("fdp"))

        for widgets in self._widgets_base.values():
            self._definir_status(widgets["label"], "", "neutro")
        self._definir_status(self.label_status_intensidades, "", "neutro")
        for label in self._labels_contagem_intensidades.values():
            self._definir_status(label, "—", "sucesso")
        self._definir_status(self.label_faixa_intensidades, "", "neutro")
        self._definir_status(self.label_status_compactacao, "", "neutro")

    def recarregar_lista(self):
        self.novo_registro()
        self._carregar_tabelas_extras()
        try:
            conn = conectar()
        except RuntimeError:
            conn = None

        if conn is not None:
            try:
                row = conn.execute(
                    "SELECT primeira_classe_diametrica, ultima_classe_diametrica, "
                    "idade_maxima_manejo, numero_minimo_arvores_ha, taxa_desconto, "
                    "ano_referencia, base_periodo_vpl, pis, cofins, "
                    "funrural, passo_intensidade, intensidade_minima, "
                    "intensidade_maxima, comprimento_tora, diametro_minimo_tora, "
                    "usar_tabela_afilamento, "
                    "truncar_esquerda_padrao, tipo_normalizacao_weibull, ajuste_manejo_padrao, "
                    "base_ajuste_logistico, base_calculo_mip, dias_trabalho, horas_trabalho "
                    "FROM configuracoes WHERE id = 1"
                ).fetchone()
            finally:
                conn.close()
        else:
            row = None

        if row is not None:
            (primeira_classe, ultima_classe, idade_maxima_manejo,
             numero_minimo_arvores, taxa_desconto, ano_referencia, base_periodo_vpl, pis, cofins,
             funrural, passo_intensidade, intensidade_minima, intensidade_maxima, comprimento_tora,
             diametro_minimo_tora, usar_tabela_afilamento, truncar_esquerda_padrao,
             tipo_normalizacao_weibull, ajuste_manejo_padrao, base_ajuste_logistico, base_calculo_mip,
             dias_trabalho, horas_trabalho) = row

            self.entry_primeira_classe.setText(_formatar_numero(primeira_classe))
            self.entry_ultima_classe.setText(_formatar_numero(ultima_classe))
            self.entry_idade_maxima_manejo.setText(_formatar_numero(idade_maxima_manejo))
            self.entry_numero_minimo_arvores.setText(_formatar_numero(numero_minimo_arvores))
            self.entry_taxa_desconto.setText(_formatar_numero(taxa_desconto))
            self.entry_ano_referencia.setText(_formatar_numero(ano_referencia))
            indice_base_periodo_vpl = self.combo_base_periodo_vpl.findData(
                base_periodo_vpl if base_periodo_vpl in simulacao.BASES_PERIODO_VPL
                else "ano_referencia")
            self.combo_base_periodo_vpl.setCurrentIndex(max(indice_base_periodo_vpl, 0))
            self.entry_pis.setText(_formatar_numero(pis))
            self.entry_cofins.setText(_formatar_numero(cofins))
            self.entry_funrural.setText(_formatar_numero(funrural))
            self.entry_dias_trabalho.setText(_formatar_numero(dias_trabalho))
            self.entry_horas_trabalho.setText(_formatar_numero(horas_trabalho))
            self.entry_passo_intensidade.setText(_formatar_numero(passo_intensidade))
            self.entry_intensidade_minima.setText(_formatar_numero(intensidade_minima))
            self.entry_intensidade_maxima.setText(_formatar_numero(intensidade_maxima))
            self.entry_comprimento_tora.setText(_formatar_numero(comprimento_tora))
            self.entry_diametro_minimo_tora.setText(_formatar_numero(diametro_minimo_tora))
            self.checkbox_usar_tabela_afilamento.setChecked(bool(usar_tabela_afilamento))
            self.checkbox_truncar_esquerda.setChecked(bool(truncar_esquerda_padrao))
            indice_normalizacao = self.combo_normalizacao_weibull.findData(
                tipo_normalizacao_weibull if tipo_normalizacao_weibull in simulacao.TIPOS_NORMALIZACAO_WEIBULL
                else "aditiva")
            self.combo_normalizacao_weibull.setCurrentIndex(max(indice_normalizacao, 0))
            self.checkbox_ajuste_manejo.setChecked(bool(ajuste_manejo_padrao))
            indice_base_logistico = self.combo_base_ajuste_logistico.findData(
                base_ajuste_logistico if base_ajuste_logistico in simulacao.BASES_AJUSTE_LOGISTICO
                else "ip")
            self.combo_base_ajuste_logistico.setCurrentIndex(max(indice_base_logistico, 0))
            indice_base_calculo_mip = self.combo_base_calculo_mip.findData(
                base_calculo_mip if base_calculo_mip in simulacao.BASES_CALCULO_MIP else "fdp")
            self.combo_base_calculo_mip.setCurrentIndex(max(indice_base_calculo_mip, 0))

        # Não sobrescreve o status de uma operação em andamento (import ou
        # simulação) — mostrar_tela chama recarregar_lista toda vez que o
        # usuário navega até essa tela, inclusive enquanto uma operação
        # disparada por ela mesma ainda está rodando em segundo plano.
        for chave, _titulo, nome_tabela, _palavras in _BASES_IFC:
            if not self._importando[chave]:
                self._atualizar_status_base(chave, nome_tabela)
        if not self._simulacao_rodando:
            self._atualizar_contagens_intensidades()
        if not self._compactando:
            self._atualizar_status_compactacao()

    def salvar_registro(self):
        valores = {}
        for chave, entry in (
            ("primeira_classe_diametrica", self.entry_primeira_classe),
            ("ultima_classe_diametrica", self.entry_ultima_classe),
            ("idade_maxima_manejo", self.entry_idade_maxima_manejo),
            ("numero_minimo_arvores_ha", self.entry_numero_minimo_arvores),
            ("taxa_desconto", self.entry_taxa_desconto),
            ("pis", self.entry_pis),
            ("cofins", self.entry_cofins),
            ("funrural", self.entry_funrural),
            ("dias_trabalho", self.entry_dias_trabalho),
            ("horas_trabalho", self.entry_horas_trabalho),
            ("passo_intensidade", self.entry_passo_intensidade),
            ("intensidade_minima", self.entry_intensidade_minima),
            ("intensidade_maxima", self.entry_intensidade_maxima),
            ("comprimento_tora", self.entry_comprimento_tora),
            ("diametro_minimo_tora", self.entry_diametro_minimo_tora),
        ):
            texto = entry.text().strip()
            if not texto:
                valores[chave] = None
                continue
            try:
                valores[chave] = float(texto.replace(",", "."))
            except ValueError:
                QMessageBox.warning(
                    self, "Configurações", f"Valor inválido em '{_ROTULOS[chave]}': '{texto}'.")
                return

        texto_ano = self.entry_ano_referencia.text().strip()
        ano_referencia = None
        if texto_ano:
            try:
                ano_referencia = int(float(texto_ano.replace(",", ".")))
            except ValueError:
                QMessageBox.warning(
                    self, "Configurações", f"Ano de referência inválido: '{texto_ano}'.")
                return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            conn.execute(
                "INSERT INTO configuracoes "
                "(id, primeira_classe_diametrica, ultima_classe_diametrica, intervalo_classe, "
                "idade_maxima_manejo, numero_minimo_arvores_ha, taxa_desconto, "
                "ano_referencia, base_periodo_vpl, pis, cofins, "
                "funrural, passo_intensidade, intensidade_minima, "
                "intensidade_maxima, comprimento_tora, diametro_minimo_tora, "
                "usar_tabela_afilamento, "
                "truncar_esquerda_padrao, tipo_normalizacao_weibull, ajuste_manejo_padrao, "
                "base_ajuste_logistico, base_calculo_mip, dias_trabalho, horas_trabalho) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "primeira_classe_diametrica=excluded.primeira_classe_diametrica, "
                "ultima_classe_diametrica=excluded.ultima_classe_diametrica, "
                "intervalo_classe=excluded.intervalo_classe, "
                "idade_maxima_manejo=excluded.idade_maxima_manejo, "
                "numero_minimo_arvores_ha=excluded.numero_minimo_arvores_ha, "
                "taxa_desconto=excluded.taxa_desconto, "
                "ano_referencia=excluded.ano_referencia, "
                "base_periodo_vpl=excluded.base_periodo_vpl, "
                "pis=excluded.pis, cofins=excluded.cofins, funrural=excluded.funrural, "
                "passo_intensidade=excluded.passo_intensidade, "
                "intensidade_minima=excluded.intensidade_minima, "
                "intensidade_maxima=excluded.intensidade_maxima, "
                "comprimento_tora=excluded.comprimento_tora, "
                "diametro_minimo_tora=excluded.diametro_minimo_tora, "
                "usar_tabela_afilamento=excluded.usar_tabela_afilamento, "
                "truncar_esquerda_padrao=excluded.truncar_esquerda_padrao, "
                "tipo_normalizacao_weibull=excluded.tipo_normalizacao_weibull, "
                "ajuste_manejo_padrao=excluded.ajuste_manejo_padrao, "
                "base_ajuste_logistico=excluded.base_ajuste_logistico, "
                "base_calculo_mip=excluded.base_calculo_mip, "
                "dias_trabalho=excluded.dias_trabalho, "
                "horas_trabalho=excluded.horas_trabalho",
                (
                    valores["primeira_classe_diametrica"], valores["ultima_classe_diametrica"],
                    # Intervalo de classe não é mais editável na tela — sempre 1
                    # (removido a pedido do usuário; ver core/db.py, coluna com
                    # DEFAULT 1 agora).
                    1.0,
                    valores["idade_maxima_manejo"], valores["numero_minimo_arvores_ha"],
                    valores["taxa_desconto"], ano_referencia,
                    self.combo_base_periodo_vpl.currentData(),
                    valores["pis"], valores["cofins"],
                    valores["funrural"],
                    valores["passo_intensidade"], valores["intensidade_minima"],
                    valores["intensidade_maxima"], valores["comprimento_tora"],
                    valores["diametro_minimo_tora"],
                    int(self.checkbox_usar_tabela_afilamento.isChecked()),
                    int(self.checkbox_truncar_esquerda.isChecked()),
                    self.combo_normalizacao_weibull.currentData(),
                    int(self.checkbox_ajuste_manejo.isChecked()),
                    self.combo_base_ajuste_logistico.currentData(),
                    self.combo_base_calculo_mip.currentData(),
                    valores["dias_trabalho"], valores["horas_trabalho"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

        projeto.sincronizar()
        QMessageBox.information(self, "Configurações", "Configurações salvas.")

    def _exportar_configuracoes(self):
        """Um .xlsx só com toda parametrização desta tela, uma aba por
        seção — "Parâmetros" vem do FORMULÁRIO em memória (mesmos campos
        que salvar_registro grava, mas o valor atual na tela, mesmo se
        ainda não salvo — útil pra conferir antes de salvar), as outras 4
        vêm do banco (Sortimentos/Custo de Formação/Custo de Colheita têm
        CRUD próprio, persistido na hora — ver _montar_secao_sortimentos/
        _montar_secao_custo_formacao/_montar_secao_custo_colheita). Só
        leitura — não muda nada no projeto, ao contrário de "Salvar
        configurações"."""
        linhas_parametros = [
            ("Primeira classe diamétrica", self.entry_primeira_classe.text()),
            ("Última classe diamétrica", self.entry_ultima_classe.text()),
            ("Idade máxima de manejo", self.entry_idade_maxima_manejo.text()),
            ("Número mínimo de árvores/ha ao final do manejo", self.entry_numero_minimo_arvores.text()),
            ("Ano de referência da análise", self.entry_ano_referencia.text()),
            ("Base do período do VPL", self.combo_base_periodo_vpl.currentText()),
            ("Taxa de desconto (%)", self.entry_taxa_desconto.text()),
            ("PIS (%)", self.entry_pis.text()),
            ("COFINS (%)", self.entry_cofins.text()),
            ("Funrural (%)", self.entry_funrural.text()),
            ("Dias de Trabalho", self.entry_dias_trabalho.text()),
            ("Horas de Trabalho", self.entry_horas_trabalho.text()),
            ("Passo da intensidade (%)", self.entry_passo_intensidade.text()),
            ("Intensidade mínima (%)", self.entry_intensidade_minima.text()),
            ("Intensidade máxima (%)", self.entry_intensidade_maxima.text()),
            ("Comprimento da tora (m)", self.entry_comprimento_tora.text()),
            ("Diâmetro mínimo (cm)", self.entry_diametro_minimo_tora.text()),
            (
                "Acelerar cálculo do Afilamento com tabela pré-calculada",
                "Sim" if self.checkbox_usar_tabela_afilamento.isChecked() else "Não"),
            (
                "Ajustar Weibull \"Por Simulação\" com truncagem à esquerda",
                "Sim" if self.checkbox_truncar_esquerda.isChecked() else "Não"),
            ("Normalização da Weibull por classe", self.combo_normalizacao_weibull.currentText()),
            (
                "Ajuste de manejo: empurrar Raleio/1º/2º Desbaste que caiam em ano-calendário já passado",
                "Sim" if self.checkbox_ajuste_manejo.isChecked() else "Não"),
            ("Base do ajuste logístico (ITD)", self.combo_base_ajuste_logistico.currentText()),
            ("Grandeza por classe do MIP (DD/IP)", self.combo_base_calculo_mip.currentText()),
        ]
        df_parametros = pd.DataFrame(linhas_parametros, columns=["Parâmetro", "Valor"])

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            df_sortimentos = pd.read_sql_query(
                "SELECT nome, limite_inferior, limite_superior, rendimento, preco, preco_pe "
                "FROM sortimentos ORDER BY limite_inferior, nome", conn)
            df_custo_formacao = pd.read_sql_query(
                "SELECT nome, ano, custo FROM custos_formacao ORDER BY ano, nome", conn)
            df_custo_colheita = pd.read_sql_query(
                "SELECT nome, custo_hora_maquina, disponibilidade_mecanica, eficiencia_operacional "
                "FROM custos_colheita ORDER BY nome", conn)
            df_produtividade = pd.read_sql_query(
                "SELECT cc.nome AS custo_colheita, p.classe, p.produtividade "
                "FROM custo_colheita_produtividade p JOIN custos_colheita cc "
                "ON cc.id = p.custo_colheita_id ORDER BY cc.nome, p.classe", conn)
        finally:
            conn.close()

        df_sortimentos.columns = [
            "Nome", "Limite inferior", "Limite superior", "Rendimento (%)",
            "Madeira Serrada (R$/m³)", "Madeira em Pé (R$/m³)"]
        df_custo_formacao.columns = ["Nome", "Idade do Povoamento", "Custo (R$/ha)"]
        df_custo_colheita.columns = [
            "Nome", "Custo Hora Máquina (R$/HM)", "Disponibilidade Mecânica (%)",
            "Eficiência Operacional (%)"]
        df_produtividade.columns = ["Custo de Colheita", "Classe Diamétrica", "Produtividade (m³/HM)"]

        caminho, _ = QFileDialog.getSaveFileName(
            self, "Exportar parametrizações", "", "Planilha Excel (*.xlsx)")
        if not caminho:
            return
        if not caminho.endswith(".xlsx"):
            caminho += ".xlsx"

        try:
            with pd.ExcelWriter(caminho) as writer:
                df_parametros.to_excel(writer, sheet_name="Parâmetros", index=False)
                df_sortimentos.to_excel(writer, sheet_name="Sortimentos", index=False)
                df_custo_formacao.to_excel(writer, sheet_name="Custo de Formação", index=False)
                df_custo_colheita.to_excel(writer, sheet_name="Custo de Colheita", index=False)
                df_produtividade.to_excel(writer, sheet_name="Produtividade Colheita", index=False)
        except Exception as e:
            QMessageBox.critical(self, "Configurações", f"Não foi possível exportar:\n{e}")
            return

        QMessageBox.information(self, "Configurações", f"Parametrizações exportadas em\n{caminho}")

    # ---------------- sortimentos: CRUD (tabela própria, persiste na hora) ----------------

    def _carregar_sortimentos(self):
        try:
            conn = conectar()
        except RuntimeError:
            self.tabela_sortimentos.definir_linhas([])
            return
        try:
            linhas = conn.execute(
                "SELECT id, nome, limite_inferior, limite_superior, rendimento, preco, preco_pe "
                "FROM sortimentos ORDER BY limite_inferior, nome"
            ).fetchall()
        finally:
            conn.close()
        ids = [str(r[0]) for r in linhas]
        valores = [(r[1], r[2], r[3], r[4], r[5], r[6]) for r in linhas]
        self.tabela_sortimentos.definir_linhas(valores, ids=ids)

    def _ao_selecionar_sortimento(self):
        selecao = self.tabela_sortimentos.selecionados()
        if not selecao or selecao[0] == IID_RESUMO:
            return
        id_sortimento = int(selecao[0])
        try:
            conn = conectar()
        except RuntimeError:
            return
        try:
            row = conn.execute(
                "SELECT id, nome, limite_inferior, limite_superior, rendimento, preco, preco_pe "
                "FROM sortimentos WHERE id = ?",
                (id_sortimento,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return
        self._sortimento_atual_id = row[0]
        self.entry_sortimento_nome.setText(row[1] or "")
        self.entry_sortimento_limite_inferior.setText(_formatar_numero(row[2]))
        self.entry_sortimento_limite_superior.setText(_formatar_numero(row[3]))
        self.entry_sortimento_rendimento.setText(_formatar_numero(row[4]))
        self.entry_sortimento_preco.setText(_formatar_numero(row[5]))
        self.entry_sortimento_preco_pe.setText(_formatar_numero(row[6]))

    def _coletar_dados_sortimento(self):
        """Valida os campos e monta a tupla (nome, limite_inferior,
        limite_superior, rendimento, preco, preco_pe) pronta pro
        INSERT/UPDATE — devolve None (já com o QMessageBox de erro
        mostrado) se algo for inválido."""
        nome = self.entry_sortimento_nome.text().strip()
        if not nome:
            QMessageBox.warning(self, "Configurações", "Informe o nome do sortimento.")
            return None

        valores = []
        for rotulo, entry in (
            ("Limite inferior", self.entry_sortimento_limite_inferior),
            ("Limite superior", self.entry_sortimento_limite_superior),
            ("Rendimento", self.entry_sortimento_rendimento),
            ("Madeira Serrada", self.entry_sortimento_preco),
            ("Madeira em Pé", self.entry_sortimento_preco_pe),
        ):
            texto = entry.text().strip()
            if not texto:
                valores.append(None)
                continue
            try:
                valores.append(float(texto.replace(",", ".")))
            except ValueError:
                QMessageBox.warning(self, "Configurações", f"{rotulo} inválido: '{texto}'.")
                return None
        limite_inferior, limite_superior, rendimento, preco, preco_pe = valores

        if limite_inferior is not None and limite_superior is not None and limite_inferior > limite_superior:
            QMessageBox.warning(
                self, "Configurações", "O limite inferior não pode ser maior que o limite superior.")
            return None

        return nome, limite_inferior, limite_superior, rendimento, preco, preco_pe

    def _adicionar_sortimento(self):
        dados = self._coletar_dados_sortimento()
        if dados is None:
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            conn.execute(
                "INSERT INTO sortimentos "
                "(nome, limite_inferior, limite_superior, rendimento, preco, preco_pe) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                dados)
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        self._limpar_form_sortimento()
        self._carregar_sortimentos()

    def _salvar_sortimento(self):
        """Atualiza o sortimento selecionado na lista — sem seleção, avisa
        (pra criar um novo, é "Adicionar")."""
        if self._sortimento_atual_id is None:
            QMessageBox.warning(
                self, "Configurações",
                "Selecione um sortimento na lista pra editar, ou use \"Adicionar\" pra criar um novo.")
            return
        dados = self._coletar_dados_sortimento()
        if dados is None:
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            conn.execute(
                "UPDATE sortimentos SET nome=?, limite_inferior=?, limite_superior=?, rendimento=?, "
                "preco=?, preco_pe=? WHERE id=?",
                dados + (self._sortimento_atual_id,))
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        id_atual = self._sortimento_atual_id
        self._carregar_sortimentos()
        self.tabela_sortimentos.selecionar_id(id_atual)

    def _excluir_sortimento(self):
        selecionados = [s for s in self.tabela_sortimentos.selecionados() if s != IID_RESUMO]
        if not selecionados:
            return
        pergunta = (
            "Excluir este sortimento?" if len(selecionados) == 1
            else f"Excluir os {len(selecionados)} sortimentos selecionados?")
        if QMessageBox.question(
            self, "Configurações", pergunta,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            ids = [int(s) for s in selecionados]
            marcadores = ", ".join("?" for _ in ids)
            conn.execute(f"DELETE FROM sortimentos WHERE id IN ({marcadores})", ids)
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        self._limpar_form_sortimento()
        self._carregar_sortimentos()

    def _limpar_form_sortimento(self):
        self._sortimento_atual_id = None
        self.tabela_sortimentos.limpar_selecao()
        self.entry_sortimento_nome.setText("")
        self.entry_sortimento_limite_inferior.setText("")
        self.entry_sortimento_limite_superior.setText("")
        self.entry_sortimento_rendimento.setText("")
        self.entry_sortimento_preco.setText("")
        self.entry_sortimento_preco_pe.setText("")

    # ---------------- construtores de variáveis: duplicar/excluir/ativar-desativar ----------------
    # (criar/editar o grafo continua só na tela Construtor de Variáveis —
    # aqui só gerencia os já salvos, ver _montar_secao_construtores)

    def _carregar_construtores(self):
        try:
            conn = conectar()
        except RuntimeError:
            self._construtores_cadastrados = []
            self.tabela_construtores.definir_linhas([])
            return
        try:
            self._construtores_cadastrados = construtores.listar_construtores(conn)
        except Exception:
            self._construtores_cadastrados = []
        finally:
            conn.close()
        ids = [str(c["id"]) for c in self._construtores_cadastrados]
        valores = [
            (c["nome"], c["tabela_origem"], "Sim" if c["ativo"] else "Não")
            for c in self._construtores_cadastrados
        ]
        self.tabela_construtores.definir_linhas(valores, ids=ids)

    def _construtor_selecionado(self):
        selecao = self.tabela_construtores.selecionados()
        if not selecao or selecao[0] == IID_RESUMO:
            return None
        id_selecionado = int(selecao[0])
        return next((c for c in self._construtores_cadastrados if c["id"] == id_selecionado), None)

    def _duplicar_construtor(self):
        """Mesma lógica que a tela Construtor de Variáveis tinha (ver
        duplicar_construtor lá) — INSERT novo (construtor_id=None),
        desativado por padrão — só que sem abrir a cópia em canvas
        nenhum, já que esta tela não tem um: fica pronta pra abrir na
        tela Construtor de Variáveis (botão "Construtores")."""
        resumo = self._construtor_selecionado()
        if resumo is None:
            QMessageBox.information(
                self, "Configurações", "Selecione um construtor na lista antes de duplicar.")
            return

        nome, ok = QInputDialog.getText(
            self, "Duplicar construtor", "Nome da cópia:", text=f"{resumo['nome']} (cópia)")
        if not ok or not nome.strip():
            return
        nome = nome.strip()

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            novo_id = construtores.salvar_construtor(
                conn, nome, resumo["tabela_origem"], resumo["grafo"], construtor_id=None)
            construtores.definir_ativo(conn, novo_id, False)
        finally:
            conn.close()

        projeto.sincronizar()
        self._carregar_construtores()
        self.tabela_construtores.selecionar_id(novo_id)
        QMessageBox.information(
            self, "Configurações",
            f"Construtor duplicado como \"{nome}\" (desativado).")

    def _excluir_construtor(self):
        resumo = self._construtor_selecionado()
        if resumo is None:
            QMessageBox.information(
                self, "Configurações", "Selecione um construtor na lista antes de excluir.")
            return
        if QMessageBox.question(
            self, "Configurações", f"Excluir o construtor \"{resumo['nome']}\"?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            construtores.excluir_construtor(conn, resumo["id"])
        finally:
            conn.close()
        projeto.sincronizar()
        self._carregar_construtores()

    def _alternar_ativo_construtor(self):
        resumo = self._construtor_selecionado()
        if resumo is None:
            QMessageBox.information(
                self, "Configurações",
                "Selecione um construtor na lista antes de ativar/desativar.")
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            construtores.definir_ativo(conn, resumo["id"], not resumo["ativo"])
        finally:
            conn.close()
        projeto.sincronizar()
        self._carregar_construtores()
        self.tabela_construtores.selecionar_id(resumo["id"])

    # ---------------- manutenção: tabelas extras/órfãs do banco ----------------

    def _carregar_tabelas_extras(self):
        try:
            conn = conectar()
        except RuntimeError:
            self._tabelas_extras_carregadas = []
            self.tabela_tabelas_extras.definir_linhas([])
            return
        try:
            self._tabelas_extras_carregadas = listar_tabelas_nao_essenciais(conn)
        except Exception:
            self._tabelas_extras_carregadas = []
        finally:
            conn.close()
        valores = [(nome, contagem) for nome, contagem in self._tabelas_extras_carregadas]
        self.tabela_tabelas_extras.definir_linhas(valores, ids=[nome for nome, _ in valores])

    def _excluir_tabelas_extras(self):
        selecionados = [
            s for s in self.tabela_tabelas_extras.selecionados() if s != IID_RESUMO
        ]
        if not selecionados:
            QMessageBox.information(
                self, "Configurações",
                "Selecione uma ou mais tabelas na lista antes de excluir.")
            return
        plural = "s" if len(selecionados) > 1 else ""
        aviso_cenario = (
            "\n\nAlguma dessas tabelas pertence a um cenário do modo \"Múltiplos cenários\" "
            "(tela Simulação) — excluí-la aqui também remove o registro desse cenário da lista "
            "de lá, já que ele deixa de ter dado por trás."
            if any("__cenario" in nome for nome in selecionados) else ""
        )
        if QMessageBox.question(
            self, "Configurações",
            f"Excluir {len(selecionados)} tabela{plural} do arquivo de trabalho?\n\n"
            + "\n".join(selecionados) + aviso_cenario + "\n\nEssa ação não pode ser desfeita.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            excluir_tabelas(conn, selecionados)
            simulacao.limpar_registro_cenarios_orfaos(conn)
        finally:
            conn.close()
        projeto.sincronizar()
        self._carregar_tabelas_extras()

    # ---------------- custo de formação florestal: CRUD (tabela própria, persiste na hora) ----------------

    def _carregar_custos_formacao(self):
        try:
            conn = conectar()
        except RuntimeError:
            self.tabela_custo_formacao.definir_linhas([])
            return
        try:
            linhas = conn.execute(
                "SELECT id, nome, ano, custo FROM custos_formacao ORDER BY ano, nome"
            ).fetchall()
        finally:
            conn.close()
        ids = [str(r[0]) for r in linhas]
        valores = [(r[1], r[2], r[3]) for r in linhas]
        self.tabela_custo_formacao.definir_linhas(valores, ids=ids)

    def _ao_selecionar_custo_formacao(self):
        selecao = self.tabela_custo_formacao.selecionados()
        if not selecao or selecao[0] == IID_RESUMO:
            return
        id_custo_formacao = int(selecao[0])
        try:
            conn = conectar()
        except RuntimeError:
            return
        try:
            row = conn.execute(
                "SELECT id, nome, ano, custo FROM custos_formacao WHERE id = ?",
                (id_custo_formacao,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return
        self._custo_formacao_atual_id = row[0]
        self.entry_custo_formacao_nome.setText(row[1] or "")
        self.entry_custo_formacao_ano.setText(_formatar_numero(row[2]))
        self.entry_custo_formacao_custo.setText(_formatar_numero(row[3]))

    def _coletar_dados_custo_formacao(self):
        """Valida os campos e monta a tupla (nome, ano, custo) pronta pro
        INSERT/UPDATE — devolve None (já com o QMessageBox de erro
        mostrado) se algo for inválido."""
        nome = self.entry_custo_formacao_nome.text().strip()
        if not nome:
            QMessageBox.warning(self, "Configurações", "Informe o nome do custo de formação.")
            return None

        valores = []
        for rotulo, entry in (
            ("Ano", self.entry_custo_formacao_ano),
            ("Custo", self.entry_custo_formacao_custo),
        ):
            texto = entry.text().strip()
            if not texto:
                valores.append(None)
                continue
            try:
                valores.append(float(texto.replace(",", ".")))
            except ValueError:
                QMessageBox.warning(self, "Configurações", f"{rotulo} inválido: '{texto}'.")
                return None
        ano, custo = valores

        return nome, ano, custo

    def _adicionar_custo_formacao(self):
        dados = self._coletar_dados_custo_formacao()
        if dados is None:
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            conn.execute(
                "INSERT INTO custos_formacao (nome, ano, custo) VALUES (?, ?, ?)", dados)
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        self._limpar_form_custo_formacao()
        self._carregar_custos_formacao()

    def _salvar_custo_formacao(self):
        """Atualiza o custo de formação selecionado na lista — sem seleção,
        avisa (pra criar um novo, é "Adicionar")."""
        if self._custo_formacao_atual_id is None:
            QMessageBox.warning(
                self, "Configurações",
                "Selecione um custo na lista pra editar, ou use \"Adicionar\" pra criar um novo.")
            return
        dados = self._coletar_dados_custo_formacao()
        if dados is None:
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            conn.execute(
                "UPDATE custos_formacao SET nome=?, ano=?, custo=? WHERE id=?",
                dados + (self._custo_formacao_atual_id,))
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        id_atual = self._custo_formacao_atual_id
        self._carregar_custos_formacao()
        self.tabela_custo_formacao.selecionar_id(id_atual)

    def _excluir_custo_formacao(self):
        selecionados = [s for s in self.tabela_custo_formacao.selecionados() if s != IID_RESUMO]
        if not selecionados:
            return
        pergunta = (
            "Excluir este custo de formação?" if len(selecionados) == 1
            else f"Excluir os {len(selecionados)} custos de formação selecionados?")
        if QMessageBox.question(
            self, "Configurações", pergunta,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            ids = [int(s) for s in selecionados]
            marcadores = ", ".join("?" for _ in ids)
            conn.execute(f"DELETE FROM custos_formacao WHERE id IN ({marcadores})", ids)
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        self._limpar_form_custo_formacao()
        self._carregar_custos_formacao()

    def _limpar_form_custo_formacao(self):
        self._custo_formacao_atual_id = None
        self.tabela_custo_formacao.limpar_selecao()
        self.entry_custo_formacao_nome.setText("")
        self.entry_custo_formacao_ano.setText("")
        self.entry_custo_formacao_custo.setText("")

    # ---------------- custo de colheita: CRUD (tabela própria, persiste na hora) ----------------

    def _carregar_custos_colheita(self):
        try:
            conn = conectar()
        except RuntimeError:
            self.tabela_custo_colheita.definir_linhas([])
            return
        try:
            linhas = conn.execute(
                "SELECT id, nome, custo_hora_maquina, disponibilidade_mecanica, "
                "eficiencia_operacional FROM custos_colheita ORDER BY nome"
            ).fetchall()
        finally:
            conn.close()
        ids = [str(r[0]) for r in linhas]
        # "" pra coluna "produtividade" — só existe pra levar o botão "+"
        # embutido por linha (ver _atualizar_botoes_produtividade), sem
        # texto próprio.
        valores = [(r[1], r[2], r[3], r[4], "") for r in linhas]
        self.tabela_custo_colheita.definir_linhas(valores, ids=ids)

    def _ao_selecionar_custo_colheita(self):
        selecao = self.tabela_custo_colheita.selecionados()
        if not selecao or selecao[0] == IID_RESUMO:
            return
        id_custo_colheita = int(selecao[0])
        try:
            conn = conectar()
        except RuntimeError:
            return
        try:
            row = conn.execute(
                "SELECT id, nome, custo_hora_maquina, disponibilidade_mecanica, "
                "eficiencia_operacional FROM custos_colheita WHERE id = ?",
                (id_custo_colheita,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return
        self._custo_colheita_atual_id = row[0]
        self.entry_custo_colheita_nome.setText(row[1] or "")
        self.entry_custo_colheita_custo_hora_maquina.setText(_formatar_numero(row[2]))
        self.entry_custo_colheita_disponibilidade_mecanica.setText(_formatar_numero(row[3]))
        self.entry_custo_colheita_eficiencia_operacional.setText(_formatar_numero(row[4]))

    def _coletar_dados_custo_colheita(self):
        """Valida os campos e monta a tupla (nome, custo_hora_maquina,
        disponibilidade_mecanica, eficiencia_operacional) pronta pro
        INSERT/UPDATE — devolve None (já com o QMessageBox de erro
        mostrado) se algo for inválido. Produtividade por classe é gravada
        à parte (custo_colheita_produtividade), direto pelo botão "+" de
        cada linha (ver _abrir_dialogo_produtividade) — não entra nessa
        tupla nem depende de "Adicionar"/"Salvar"."""
        nome = self.entry_custo_colheita_nome.text().strip()
        if not nome:
            QMessageBox.warning(self, "Configurações", "Informe o nome do custo de colheita.")
            return None

        valores = []
        for rotulo, entry in (
            ("Custo Hora Máquina", self.entry_custo_colheita_custo_hora_maquina),
            ("Disponibilidade Mecânica", self.entry_custo_colheita_disponibilidade_mecanica),
            ("Eficiência Operacional", self.entry_custo_colheita_eficiencia_operacional),
        ):
            texto = entry.text().strip()
            if not texto:
                valores.append(None)
                continue
            try:
                valores.append(float(texto.replace(",", ".")))
            except ValueError:
                QMessageBox.warning(self, "Configurações", f"{rotulo} inválido: '{texto}'.")
                return None

        return (nome,) + tuple(valores)

    def _adicionar_custo_colheita(self):
        dados = self._coletar_dados_custo_colheita()
        if dados is None:
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            conn.execute(
                "INSERT INTO custos_colheita "
                "(nome, custo_hora_maquina, disponibilidade_mecanica, eficiencia_operacional) "
                "VALUES (?, ?, ?, ?)",
                dados)
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        self._limpar_form_custo_colheita()
        self._carregar_custos_colheita()

    def _salvar_custo_colheita(self):
        """Atualiza o custo de colheita selecionado na lista — sem seleção,
        avisa (pra criar um novo, é "Adicionar")."""
        if self._custo_colheita_atual_id is None:
            QMessageBox.warning(
                self, "Configurações",
                "Selecione um custo na lista pra editar, ou use \"Adicionar\" pra criar um novo.")
            return
        dados = self._coletar_dados_custo_colheita()
        if dados is None:
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            conn.execute(
                "UPDATE custos_colheita SET nome=?, custo_hora_maquina=?, "
                "disponibilidade_mecanica=?, eficiencia_operacional=? WHERE id=?",
                dados + (self._custo_colheita_atual_id,))
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        id_atual = self._custo_colheita_atual_id
        self._carregar_custos_colheita()
        self.tabela_custo_colheita.selecionar_id(id_atual)

    def _excluir_custo_colheita(self):
        selecionados = [s for s in self.tabela_custo_colheita.selecionados() if s != IID_RESUMO]
        if not selecionados:
            return
        pergunta = (
            "Excluir este custo de colheita?" if len(selecionados) == 1
            else f"Excluir os {len(selecionados)} custos de colheita selecionados?")
        if QMessageBox.question(
            self, "Configurações", pergunta,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Configurações", str(e))
            return
        try:
            ids = [int(s) for s in selecionados]
            marcadores = ", ".join("?" for _ in ids)
            # custo_colheita_produtividade tem ON DELETE CASCADE (ver
            # core/db.py) — não precisa de DELETE próprio aqui.
            conn.execute(f"DELETE FROM custos_colheita WHERE id IN ({marcadores})", ids)
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        self._limpar_form_custo_colheita()
        self._carregar_custos_colheita()

    def _limpar_form_custo_colheita(self):
        self._custo_colheita_atual_id = None
        self.tabela_custo_colheita.limpar_selecao()
        self.entry_custo_colheita_nome.setText("")
        self.entry_custo_colheita_custo_hora_maquina.setText("")
        self.entry_custo_colheita_disponibilidade_mecanica.setText("")
        self.entry_custo_colheita_eficiencia_operacional.setText("")

    # ---------------- importação das bases IFC ----------------

    def _atualizar_status_base(self, chave, nome_tabela):
        label = self._widgets_base[chave]["label"]
        try:
            conn = conectar()
        except RuntimeError:
            self._definir_status(label, "Nenhum projeto aberto.", "neutro")
            return
        try:
            existe = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (nome_tabela,)
            ).fetchone()
            if existe is None:
                self._definir_status(label, "Nenhum dado importado ainda.", "neutro")
                return
            total = conn.execute(f'SELECT COUNT(*) FROM "{nome_tabela}"').fetchone()[0]
            n_colunas = len(
                [d[0] for d in conn.execute(f'SELECT * FROM "{nome_tabela}" LIMIT 0').description
                 if d[0] != "id"])
            self._definir_status(label, f"{total:,} linhas / {n_colunas} colunas.", "sucesso")
        finally:
            conn.close()

    def _importar_base(self, chave):
        titulo, nome_tabela, palavras_chave_aba = next(
            (t, nt, p) for c, t, nt, p in _BASES_IFC if c == chave)

        try:
            caminho_banco = projeto.caminho_trabalho()
        except RuntimeError as e:
            QMessageBox.warning(self, titulo, str(e))
            return

        caminho_planilha, _ = QFileDialog.getOpenFileName(
            self, f"Importar planilha da {titulo}", "",
            "Planilhas e CSV (*.xlsx *.xls *.csv);;Excel (*.xlsx *.xls);;CSV (*.csv)")
        if not caminho_planilha:
            return

        try:
            abas = listar_abas(caminho_planilha)
        except Exception as e:
            QMessageBox.critical(self, titulo, f"Não foi possível ler a planilha:\n{e}")
            return

        if len(abas) == 1:
            aba_escolhida = abas[0]
        else:
            indice_padrao = _sugerir_aba_padrao(abas, palavras_chave_aba)
            aba_escolhida = escolher_aba(self, "Escolher aba da planilha", abas, indice_padrao)
            if aba_escolhida is None:
                return

        linha_cabecalho = escolher_cabecalho(self, titulo, caminho_planilha, aba_escolhida)
        if linha_cabecalho is None:
            return

        self._iniciar_importacao(
            chave, titulo, nome_tabela, caminho_planilha, caminho_banco,
            aba_escolhida, linha_cabecalho)

    def _iniciar_importacao(self, chave, _titulo, nome_tabela, caminho_planilha, caminho_banco,
                             aba_escolhida, linha_cabecalho):
        # Resolve qualquer sincronização pendente ANTES de começar — mesmo
        # motivo de weibull.py:_ao_clicar_ajustar_simulacao (importação
        # grande escreve pesado no banco de trabalho; correr em paralelo
        # com uma sincronização agendada arrisca "database is locked").
        projeto.finalizar_sincronizacao_pendente()

        self._importando[chave] = True
        self._atualizar_estado_botoes()
        widgets = self._widgets_base[chave]
        self._definir_status(widgets["label"], "Importando...", "neutro")
        widgets["progressbar"].setVisible(True)

        thread = _ThreadImportarBase(
            caminho_planilha, caminho_banco, nome_tabela, aba_escolhida, linha_cabecalho, parent=self)
        self._threads_importacao[chave] = thread
        thread.progresso.connect(lambda linhas, _total, c=chave: self._ao_progredir_importacao(c, linhas))
        thread.concluido.connect(
            lambda n_linhas, colunas, aba, cab, c=chave: self._finalizar_importacao(
                c, n_linhas=n_linhas, colunas=colunas, aba_escolhida=aba, linha_cabecalho=cab))
        thread.falhou.connect(lambda erro, c=chave: self._finalizar_importacao(c, erro=erro))
        thread.start()

    def _ao_progredir_importacao(self, chave, linhas):
        self._definir_status(
            self._widgets_base[chave]["label"],
            f"Importando... {linhas:,} linha(s) processada(s).", "neutro")

    def _finalizar_importacao(self, chave, n_linhas=None, colunas=None, aba_escolhida=None,
                               linha_cabecalho=None, erro=None):
        self._importando[chave] = False
        self._threads_importacao.pop(chave, None)
        self._atualizar_estado_botoes()
        self._widgets_base[chave]["progressbar"].setVisible(False)

        titulo, nome_tabela, _palavras = next(
            (t, nt, p) for c, t, nt, p in _BASES_IFC if c == chave)

        if erro is not None:
            self._widgets_base[chave]["label"].setText("")
            QMessageBox.critical(self, titulo, f"Não foi possível importar a planilha:\n{erro}")
            self._atualizar_status_base(chave, nome_tabela)
            return

        projeto.sincronizar()
        self._atualizar_status_base(chave, nome_tabela)
        QMessageBox.information(
            self, titulo,
            f"Importação concluída: aba '{aba_escolhida}', linha {linha_cabecalho + 1} como "
            f"cabeçalho, {n_linhas} linhas, {len(colunas)} colunas.")

    # ---------------- simulação de intensidades ----------------

    def _atualizar_contagens_intensidades(self):
        try:
            conn = conectar()
        except RuntimeError:
            for label in self._labels_contagem_intensidades.values():
                self._definir_status(label, "—", "neutro")
            self._definir_status(self.label_faixa_intensidades, "", "neutro")
            return
        try:
            for nome_tabela, label in self._labels_contagem_intensidades.items():
                existe = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (nome_tabela,)
                ).fetchone()
                if existe is None:
                    self._definir_status(label, "nenhuma simulação executada ainda.", "neutro")
                    continue
                total = conn.execute(f'SELECT COUNT(*) FROM "{nome_tabela}"').fetchone()[0]
                self._definir_status(label, f"{total:,}", "sucesso")

            self._atualizar_faixa_intensidades(conn)
        finally:
            conn.close()

    def _atualizar_faixa_intensidades(self, conn):
        """Mostra a faixa de intensidades (mínima/máxima/quantidade de
        valores) REALMENTE gravada na última "Rodar simulação de
        intensidades" — em vez de só a contagem de linhas (que pode
        coincidir entre duas rodadas com parâmetros diferentes, dando a
        falsa impressão de que a simulação anterior não foi substituída).
        Lê direto de intensidades_resumo_talhao (dados de fato gravados),
        nunca da tela de Configurações (que pode ter valores editados e
        ainda não usados numa rodada de verdade)."""
        existe = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (intensidades.TABELA_RESUMO_TALHAO,)
        ).fetchone()
        if existe is None:
            self._definir_status(self.label_faixa_intensidades, "", "neutro")
            return

        linha = conn.execute(
            f'SELECT MIN(int_raleio), MAX(int_raleio), COUNT(DISTINCT int_raleio), '
            f'MIN(int_desbaste_1), MAX(int_desbaste_1), COUNT(DISTINCT int_desbaste_1), '
            f'MIN(int_desbaste_2), MAX(int_desbaste_2), COUNT(DISTINCT int_desbaste_2) '
            f'FROM "{intensidades.TABELA_RESUMO_TALHAO}"'
        ).fetchone()

        if linha is None or linha[0] is None:
            self._definir_status(self.label_faixa_intensidades, "", "neutro")
            return

        (min_raleio, max_raleio, n_raleio, min_d1, max_d1, n_d1, min_d2, max_d2, n_d2) = linha

        def _faixa(minimo, maximo, quantidade):
            return f"{minimo * 100:.2f}% a {maximo * 100:.2f}% ({quantidade} valor(es))"

        self._definir_status(
            self.label_faixa_intensidades,
            (
                "Intensidades na última simulação — "
                f"Raleio: {_faixa(min_raleio, max_raleio, n_raleio)}; "
                f"1º Desbaste: {_faixa(min_d1, max_d1, n_d1)}; "
                f"2º Desbaste: {_faixa(min_d2, max_d2, n_d2)}."
            ),
            "sucesso")

    def _salvar_passo_min_max_intensidade(self, conn):
        """Persiste só passo_intensidade/intensidade_minima/intensidade_maxima
        (os 3 campos usados por "Rodar simulação de intensidades") sem
        mexer no resto de `configuracoes` — evita a pegadinha de editar o
        passo/mínima/máxima e esquecer de clicar "Salvar" antes de rodar,
        rodando a simulação com um valor antigo ainda gravado no banco.
        Levanta ValueError com mensagem amigável se algum dos 3 campos
        estiver com texto inválido."""
        valores = {}
        for chave, entry in (
            ("passo_intensidade", self.entry_passo_intensidade),
            ("intensidade_minima", self.entry_intensidade_minima),
            ("intensidade_maxima", self.entry_intensidade_maxima),
        ):
            texto = entry.text().strip()
            if not texto:
                valores[chave] = None
                continue
            try:
                valores[chave] = float(texto.replace(",", "."))
            except ValueError:
                raise ValueError(f"Valor inválido em '{_ROTULOS[chave]}': '{texto}'.")

        conn.execute(
            "INSERT INTO configuracoes (id, passo_intensidade, intensidade_minima, intensidade_maxima) "
            "VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "passo_intensidade=excluded.passo_intensidade, "
            "intensidade_minima=excluded.intensidade_minima, "
            "intensidade_maxima=excluded.intensidade_maxima",
            (valores["passo_intensidade"], valores["intensidade_minima"], valores["intensidade_maxima"]),
        )
        conn.commit()

    def _rodar_intensidades(self):
        if self._simulacao_rodando:
            return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Simulação de Intensidades", str(e))
            return

        try:
            self._salvar_passo_min_max_intensidade(conn)
        except ValueError as e:
            QMessageBox.warning(self, "Simulação de Intensidades", str(e))
            conn.close()
            return
        projeto.sincronizar()

        # Conexão só pra preparar a simulação (ler colunas disponíveis,
        # validar a configuração de intensidades e deixar o usuário mapear
        # colunas num diálogo modal) — fecha antes de iniciar a thread, que
        # abre sua própria conexão pra não compartilhar objeto sqlite3
        # entre threads.
        try:
            try:
                colunas_disponiveis = intensidades.colunas_base_arvore(conn)
            except Exception:
                QMessageBox.warning(
                    self, "Simulação de Intensidades",
                    "Nenhuma base IFC ByTree importada ainda.\n\n"
                    "Importe a base em \"Base IFC ByTree\", nesta tela, antes de rodar a simulação.")
                return

            try:
                lista_intensidades = intensidades.obter_intensidades(conn)
            except ValueError as e:
                QMessageBox.warning(self, "Simulação de Intensidades", str(e))
                return

            mapeamento = escolher_mapeamento_colunas(
                self, "Simulação de Intensidades — mapear colunas da base IFC ByTree",
                colunas_disponiveis, intensidades.CAMPOS_MAPEAMENTO)
            if mapeamento is None:
                return

            combinacoes = len(lista_intensidades) ** 3
            if QMessageBox.question(
                self, "Simulação de Intensidades",
                f"{len(lista_intensidades)} intensidades por manejo "
                f"({combinacoes:,} combinações de raleio + 1º e 2º desbaste por parcela).\n\n"
                "Isso substitui o resultado de uma simulação anterior, se houver. Continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        finally:
            conn.close()

        caminho_trabalho = projeto.caminho_trabalho()
        self._iniciar_execucao_intensidades(caminho_trabalho, mapeamento)

    def _iniciar_execucao_intensidades(self, caminho_trabalho, mapeamento):
        # Mesmo motivo de _iniciar_importacao acima — a Simulação de
        # Intensidades pode escrever pesado (e por bastante tempo) no banco
        # de trabalho.
        projeto.finalizar_sincronizacao_pendente()

        self._simulacao_rodando = True
        self._atualizar_estado_botoes()
        self._definir_status(self.label_status_intensidades, "Carregando a base de árvores...", "neutro")
        self.progressbar_intensidades.setRange(0, 0)
        self.progressbar_intensidades.setValue(0)
        self.progressbar_intensidades.setVisible(True)

        thread = _ThreadSimulacaoIntensidades(caminho_trabalho, mapeamento, parent=self)
        self._thread_simulacao = thread
        thread.progresso.connect(self._ao_progredir_simulacao)
        thread.concluido.connect(
            lambda estatisticas: self._finalizar_execucao_intensidades(estatisticas=estatisticas))
        thread.falhou.connect(lambda erro: self._finalizar_execucao_intensidades(erro=erro))
        thread.start()

    def _ao_progredir_simulacao(self, numero, total):
        self._definir_status(
            self.label_status_intensidades, f"Processando parcela {numero:,}/{total:,}...", "neutro")
        self.progressbar_intensidades.setRange(0, total)
        self.progressbar_intensidades.setValue(numero)

    def _finalizar_execucao_intensidades(self, estatisticas=None, erro=None):
        self._simulacao_rodando = False
        self._thread_simulacao = None
        self._atualizar_estado_botoes()
        self.progressbar_intensidades.setVisible(False)

        if erro is not None:
            self.label_status_intensidades.setText("")
            if isinstance(erro, ValueError):
                QMessageBox.warning(self, "Simulação de Intensidades", str(erro))
            else:
                QMessageBox.critical(
                    self, "Simulação de Intensidades", f"Falha ao rodar a simulação:\n{erro}")
            self._atualizar_contagens_intensidades()
            return

        projeto.sincronizar()
        self._atualizar_contagens_intensidades()
        self._definir_status(self.label_status_intensidades, "Simulação concluída.", "sucesso")

        QMessageBox.information(
            self, "Simulação de Intensidades",
            "Simulação concluída.\n\n"
            f"Árvores válidas: {estatisticas['arvores_validas']:,}\n"
            f"Árvores descartadas (dados inconsistentes): "
            f"{estatisticas['arvores_removidas_invalidas']:,}\n"
            f"Talhões: {estatisticas['talhoes']:,}\n"
            f"Parcelas: {estatisticas['parcelas']:,}\n\n"
            f"Linhas gravadas — resumo por talhão: {estatisticas['linhas_resumo_talhao']:,}\n"
            f"Linhas gravadas — resumo por parcela: {estatisticas['linhas_resumo_parcela']:,}\n"
            f"Linhas gravadas — detalhamento: {estatisticas['linhas_detalhamento']:,}")

    # ---------------- compactação do banco ----------------

    def _atualizar_status_compactacao(self):
        try:
            caminho = projeto.caminho_trabalho()
        except RuntimeError:
            self._definir_status(self.label_status_compactacao, "", "neutro")
            return
        tamanho = caminho.stat().st_size
        self._definir_status(
            self.label_status_compactacao,
            f"Tamanho atual do arquivo de trabalho: {_formatar_tamanho(tamanho)}.", "neutro")

    def _compactar_banco(self):
        if self._compactando:
            return

        try:
            caminho_trabalho = projeto.caminho_trabalho()
        except RuntimeError as e:
            QMessageBox.warning(self, "Compactar banco de dados", str(e))
            return

        # Checagem síncrona aqui (stat + espaço em disco, é barato) antes de
        # perguntar "continuar?" — sem sentido confirmar uma operação que já
        # sabemos que vai falhar por falta de espaço (ver
        # core/db.py:espaco_livre_suficiente_para_vacuum: VACUUM pode
        # precisar de até 2x o tamanho atual do arquivo livre durante a
        # operação).
        suficiente, necessario, livre = espaco_livre_suficiente_para_vacuum(caminho_trabalho)
        if not suficiente:
            QMessageBox.warning(
                self, "Compactar banco de dados",
                "Espaço em disco insuficiente para compactar: o SQLite precisa de até 2x o "
                f"tamanho atual do arquivo livre durante a operação (~{_formatar_tamanho(necessario)}), "
                f"mas só há {_formatar_tamanho(livre)} livres em "
                f"\"{caminho_trabalho.drive or caminho_trabalho.anchor}\". "
                "Libere espaço em disco e tente novamente.")
            return

        if QMessageBox.question(
            self, "Compactar banco de dados",
            "Isso reescreve o arquivo de trabalho inteiro pra liberar o espaço deixado por "
            "operações anteriores (pode levar de segundos a minutos, dependendo do tamanho do "
            "projeto). Nenhum dado é perdido. Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        self._iniciar_compactacao(caminho_trabalho)

    def _iniciar_compactacao(self, caminho_trabalho):
        # Mesmo motivo de _iniciar_importacao acima — VACUUM precisa de
        # lock EXCLUSIVE pelo tempo inteiro da compactação, então é ainda
        # mais sensível a correr em paralelo com uma sincronização pendente.
        projeto.finalizar_sincronizacao_pendente()

        self._compactando = True
        self._atualizar_estado_botoes()
        self._definir_status(self.label_status_compactacao, "Compactando...", "neutro")
        self.progressbar_compactacao.setVisible(True)

        thread = _ThreadCompactarBanco(caminho_trabalho, parent=self)
        self._thread_compactar = thread
        thread.concluido.connect(
            lambda antes, depois: self._finalizar_compactacao(tamanho_antes=antes, tamanho_depois=depois))
        thread.falhou.connect(lambda erro: self._finalizar_compactacao(erro=erro))
        thread.start()

    def _finalizar_compactacao(self, tamanho_antes=None, tamanho_depois=None, erro=None):
        self._compactando = False
        self._thread_compactar = None
        self._atualizar_estado_botoes()
        self.progressbar_compactacao.setVisible(False)

        if erro is not None:
            self.label_status_compactacao.setText("")
            if isinstance(erro, ValueError):
                QMessageBox.warning(self, "Compactar banco de dados", str(erro))
            else:
                QMessageBox.critical(self, "Compactar banco de dados", f"Não foi possível compactar:\n{erro}")
            self._atualizar_status_compactacao()
            return

        # VACUUM já reescreveu o arquivo de trabalho na hora (não precisa de
        # commit) — sincroniza pra refletir o tamanho menor no .mogno também,
        # senão o usuário compacta e o arquivo final continua grande até a
        # próxima edição disparar sincronizar() por outro motivo.
        projeto.sincronizar()
        self._atualizar_status_compactacao()

        economia = tamanho_antes - tamanho_depois
        if economia <= 0:
            QMessageBox.information(
                self, "Compactar banco de dados",
                f"Concluído. O arquivo já estava compacto ({_formatar_tamanho(tamanho_depois)}).")
        else:
            percentual = economia / tamanho_antes * 100 if tamanho_antes else 0
            QMessageBox.information(
                self, "Compactar banco de dados",
                f"Concluído.\n\n"
                f"Antes: {_formatar_tamanho(tamanho_antes)}\n"
                f"Depois: {_formatar_tamanho(tamanho_depois)}\n"
                f"Economia: {_formatar_tamanho(economia)} ({percentual:.1f}%)")

    # ---------------- estado compartilhado dos botões de ação ----------------

    def _atualizar_estado_botoes(self):
        """As três operações (importar ByTalhao, importar ByTree, rodar
        simulação) escrevem na mesma cópia de trabalho SQLite — desabilita
        as três enquanto qualquer uma estiver rodando, pra não disparar
        duas ao mesmo tempo achando que são independentes. Compactar
        entra na mesma exclusão: VACUUM reescreve o arquivo de trabalho
        inteiro, então não pode rodar junto de nenhuma dessas operações
        (nem elas junto dele)."""
        alguma_rodando = self._simulacao_rodando or self._compactando or any(self._importando.values())
        for widgets in self._widgets_base.values():
            widgets["botao"].setEnabled(not alguma_rodando)
        self.botao_rodar_intensidades.setEnabled(not alguma_rodando)
        self.botao_compactar.setEnabled(not alguma_rodando)

        janela = self.window()
        if hasattr(janela, "travar_navegacao"):
            janela.travar_navegacao(alguma_rodando)


def _sugerir_aba_padrao(abas, palavras_chave_aba):
    """Tenta achar a aba mais específica: alguma palavra-chave própria
    (ex: 'talh', 'tree') combinada com 'ifc'. 'IFC ByX' e 'IFC ByX - BASE
    MDD' batem as duas com o critério frouxo, então prioriza quem também
    tem 'base'/'mdd' no nome antes de cair pro critério frouxo."""
    def bate_palavra_chave(aba):
        return any(palavra in aba.lower() for palavra in palavras_chave_aba)

    candidatos_especificos = [
        i for i, a in enumerate(abas)
        if "ifc" in a.lower() and bate_palavra_chave(a)
        and ("base" in a.lower() or "mdd" in a.lower())
    ]
    if candidatos_especificos:
        return candidatos_especificos[0]

    candidatos_frouxos = [i for i, a in enumerate(abas) if "ifc" in a.lower() and bate_palavra_chave(a)]
    if candidatos_frouxos:
        return candidatos_frouxos[0]

    return 0


_LARGURA_ENTRADA_NUMERICA = 90

# Aceita opcionalmente um "-" inicial, dígitos, e um separador decimal
# (ponto OU vírgula — mesma tolerância de _coletar_dados_sortimento/
# _coletar_dados_custo_formacao, que fazem texto.replace(",", ".") antes
# de float()); vazio também é válido (campo opcional). Não valida o
# número final por completo (ex: "-" ou "1," sozinhos passam) — só barra
# o usuário de digitar letra, só isso já resolve o pedido de "só número"
# aqui; a validação de verdade continua em _coletar_dados_*.
_VALIDADOR_NUMERICO = QRegularExpressionValidator(QRegularExpression(r"^-?\d*[.,]?\d*$"))


def _criar_entrada_numerica(largura=_LARGURA_ENTRADA_NUMERICA):
    """QLineEdit estreito (não precisa do espaço todo — são só números
    curtos) que só aceita dígitos/sinal/separador decimal."""
    entrada = QLineEdit()
    entrada.setValidator(_VALIDADOR_NUMERICO)
    entrada.setMaximumWidth(largura)
    return entrada


_ROTULOS = {
    "primeira_classe_diametrica": "Primeira classe diamétrica",
    "ultima_classe_diametrica": "Última classe diamétrica",
    "idade_maxima_manejo": "Idade máxima de manejo",
    "numero_minimo_arvores_ha": "Número mínimo de árvores/ha ao final do manejo",
    "taxa_desconto": "Taxa de desconto (%)",
    "pis": "PIS (%)",
    "cofins": "COFINS (%)",
    "funrural": "Funrural (%)",
    "dias_trabalho": "Dias de Trabalho",
    "horas_trabalho": "Horas de Trabalho",
    "passo_intensidade": "Passo da intensidade (%)",
    "intensidade_minima": "Intensidade mínima (%)",
    "intensidade_maxima": "Intensidade máxima (%)",
    "comprimento_tora": "Comprimento da tora (m)",
    "diametro_minimo_tora": "Diâmetro mínimo (cm)",
}


def _formatar_numero(valor):
    if valor is None:
        return ""
    if float(valor).is_integer():
        return str(int(valor))
    return str(valor)


def _formatar_tamanho(bytes_):
    tamanho = float(bytes_)
    for unidade in ("B", "KB", "MB", "GB"):
        if tamanho < 1024 or unidade == "GB":
            return f"{tamanho:.1f} {unidade}" if unidade != "B" else f"{int(tamanho)} B"
        tamanho /= 1024
