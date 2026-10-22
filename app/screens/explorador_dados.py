"""Explorador SQL com paginação, exportação integral e gráficos."""
from pathlib import Path
from PySide6.QtCore import QAbstractTableModel,QModelIndex,Qt,QThread,Signal
from PySide6.QtGui import QKeySequence,QShortcut
from PySide6.QtWidgets import (QAbstractItemView,QComboBox,QFileDialog,QHBoxLayout,QHeaderView,QLabel,QLineEdit,
    QPlainTextEdit,QPushButton,QSpinBox,QSplitter,QTabWidget,QTableView,QVBoxLayout,QWidget)
from ..core import explorador_dados as dados,projeto
from ..widgets.cabecalho_tela import CabecalhoTela
from .base import TelaBase


class _Modelo(QAbstractTableModel):
    def __init__(self,parent=None):super().__init__(parent);self.colunas=[];self.linhas=[]
    def definir(self,colunas,linhas):
        self.beginResetModel();self.colunas=colunas;self.linhas=linhas;self.endResetModel()
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.linhas)
    def columnCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.colunas)
    def data(self,indice,role=Qt.ItemDataRole.DisplayRole):
        if indice.isValid() and role in (Qt.ItemDataRole.DisplayRole,Qt.ItemDataRole.ToolTipRole):
            valor=self.linhas[indice.row()][indice.column()];return '' if valor is None else str(valor)
    def headerData(self,secao,orientacao,role=Qt.ItemDataRole.DisplayRole):
        if role in (Qt.ItemDataRole.DisplayRole,Qt.ItemDataRole.ToolTipRole):return self.colunas[secao] if orientacao==Qt.Orientation.Horizontal else str(secao+1)


class _Trabalho(QThread):
    progresso=Signal(object)
    def __init__(self,tipo,funcao,parent):super().__init__(parent);self.tipo=tipo;self.funcao=funcao;self.resultado=None;self.erro=None
    def run(self):
        try:self.resultado=self.funcao(self.progresso.emit) if self.tipo=='exportar' else self.funcao()
        except Exception as erro:self.erro=str(erro)


class TelaExploradorDados(TelaBase):
    def __init__(self,parent=None):
        super().__init__(parent);self.ocupado=False;self._thread=None;self._pagina_atual=1;self._mais=False
        self._sql_resultado='';self._base_resultado='';self._banco_resultado='';self._tamanho_resultado=500;self._figura=None;self._canvas=None
        raiz=QVBoxLayout(self);raiz.setContentsMargins(20,20,20,12)
        raiz.addWidget(CabecalhoTela('Explorador de Dados','Consulte tabelas com SQL, exporte os resultados completos e crie gráficos.'))
        self.controles=QWidget();c=QVBoxLayout(self.controles);c.setContentsMargins(0,0,0,0)
        fonte=QHBoxLayout();self.banco=QLineEdit();self.banco.setReadOnly(True)
        fonte.addWidget(QLabel('Banco'));fonte.addWidget(self.banco,1)
        projeto_btn=QPushButton('Projeto aberto');projeto_btn.clicked.connect(self._usar_projeto);fonte.addWidget(projeto_btn)
        externo=QPushButton('Abrir SQLite…');externo.clicked.connect(self._abrir_banco);fonte.addWidget(externo);c.addLayout(fonte)
        linha=QHBoxLayout();linha.addWidget(QLabel('Tabela'));self.tabelas=QComboBox();self.tabelas.setMinimumContentsLength(10)
        self.tabelas.activated.connect(lambda _:self._ver_tabela())
        self.tabelas.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        linha.addWidget(self.tabelas,1);ver=QPushButton('Ver tabela');ver.clicked.connect(self._ver_tabela);linha.addWidget(ver)
        atualizar=QPushButton('Atualizar lista');atualizar.clicked.connect(self.recarregar_lista);linha.addWidget(atualizar);c.addLayout(linha)
        self.sql=QPlainTextEdit();self.sql.setPlaceholderText('SELECT * FROM "cenarios_escolhidos_seu_sufixo" ORDER BY "TALHÃO"');self.sql.setMaximumHeight(115)
        c.addWidget(self.sql)
        acoes=QHBoxLayout();executar=QPushButton('Executar SELECT');executar.clicked.connect(self._executar_sql);acoes.addWidget(executar)
        dica=QLabel('Somente leitura • Ctrl+Enter executa • Clique no cabeçalho para ordenar');dica.setWordWrap(True);dica.setToolTip('Todas as linhas ficam acessíveis por páginas. Exportação e gráficos usam a consulta inteira. Campos BLOB são mostrados e exportados apenas como indicação de tamanho.');acoes.addWidget(dica,1)
        self.exportar=QPushButton('Exportar consulta inteira para Excel…');self.exportar.clicked.connect(self._exportar);acoes.addWidget(self.exportar);c.addLayout(acoes)
        raiz.addWidget(self.controles)
        self.abas=QTabWidget();raiz.addWidget(self.abas,1)
        tabela_painel=QWidget();tl=QVBoxLayout(tabela_painel);tl.setContentsMargins(0,6,0,0)
        self.tabela=QTableView();self.modelo=_Modelo(self);self.tabela.setModel(self.modelo)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.tabela.setAlternatingRowColors(True)
        self.tabela.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tabela.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive);self.tabela.horizontalHeader().setDefaultSectionSize(150)
        self.tabela.horizontalHeader().sectionClicked.connect(self._ordenar);tl.addWidget(self.tabela,1)
        paginas=QHBoxLayout();self.anterior=QPushButton('Anterior');self.proxima=QPushButton('Próxima')
        self.anterior.clicked.connect(lambda:self._pagina(self._pagina_atual-1));self.proxima.clicked.connect(lambda:self._pagina(self._pagina_atual+1))
        paginas.addWidget(self.anterior);paginas.addWidget(self.proxima);paginas.addWidget(QLabel('Página'))
        self.numero=QSpinBox();self.numero.setRange(1,1000000000);paginas.addWidget(self.numero)
        ir=QPushButton('Ir');ir.clicked.connect(lambda:self._pagina(self.numero.value()));paginas.addWidget(ir)
        paginas.addWidget(QLabel('Linhas por página'));self.tamanho=QComboBox()
        for n in (100,500,1000,5000):self.tamanho.addItem(str(n),n)
        self.tamanho.setCurrentIndex(1);self.tamanho.currentIndexChanged.connect(lambda _:self._pagina(1));paginas.addWidget(self.tamanho);paginas.addStretch(1);tl.addLayout(paginas)
        self.abas.addTab(tabela_painel,'Dados')
        grafico_painel=QWidget();self.gl=QVBoxLayout(grafico_painel);self.gl.setContentsMargins(0,6,0,0)
        glinha=QHBoxLayout();self.eixo_x=QComboBox();self.eixo_y=QComboBox();self.tipo=QComboBox();self.agregacao=QComboBox()
        for nome,chave in [('Barras','bar'),('Linhas','line'),('Dispersão','scatter')]:self.tipo.addItem(nome,chave)
        for nome,chave in [('Soma por X','sum'),('Média por X','avg'),('Contagem por X','count'),('Sem agregar','raw')]:self.agregacao.addItem(nome,chave)
        for rotulo,widget in [('X',self.eixo_x),('Y',self.eixo_y),('Tipo',self.tipo),('Agregação',self.agregacao)]:
            glinha.addWidget(QLabel(rotulo));glinha.addWidget(widget,1)
        self.gl.addLayout(glinha)
        gacoes=QHBoxLayout();gerar=QPushButton('Gerar gráfico da consulta inteira');gerar.clicked.connect(self._grafico);gacoes.addWidget(gerar)
        self.salvar_grafico=QPushButton('Salvar PNG…');self.salvar_grafico.clicked.connect(self._salvar_grafico);gacoes.addWidget(self.salvar_grafico);gacoes.addStretch(1);self.gl.addLayout(gacoes)
        self.gl.addStretch(1);self.abas.addTab(grafico_painel,'Gráficos')
        self.status=QLabel('Selecione uma tabela ou escreva uma consulta SELECT.');self.status.setWordWrap(True);raiz.addWidget(self.status)
        self.atalho=QShortcut(QKeySequence('Ctrl+Return'),self);self.atalho.activated.connect(self._executar_sql)
        self._ordem=None;self._atualizar_botoes();self.recarregar_lista()

    def _atualizar_botoes(self):
        self.controles.setEnabled(not self.ocupado);self.anterior.setEnabled(not self.ocupado and self._pagina_atual>1)
        self.proxima.setEnabled(not self.ocupado and self._mais);self.exportar.setEnabled(not self.ocupado and bool(self._sql_resultado))
        self.abas.setEnabled(not self.ocupado);self.salvar_grafico.setEnabled(self._figura is not None)

    def _iniciar(self,tipo,funcao):
        if self.ocupado:return
        self.ocupado=True;self._atualizar_botoes();self.status.setText({'consulta':'Consultando…','exportar':'Exportando todas as linhas da consulta…','grafico':'Preparando gráfico da consulta inteira…'}[tipo])
        janela=self.window()
        if hasattr(janela,'travar_navegacao'):janela.travar_navegacao(True)
        self._thread=_Trabalho(tipo,funcao,self);self._thread.finished.connect(self._concluir)
        self._thread.progresso.connect(lambda n:self.status.setText(f'Exportando a consulta inteira: {n:,} linhas processadas…'))
        self._thread.start()

    def _concluir(self):
        thread=self._thread;self.ocupado=False
        janela=self.window()
        if hasattr(janela,'travar_navegacao'):janela.travar_navegacao(False)
        try:
            if thread.erro:self.status.setText('Erro: '+thread.erro)
            elif thread.tipo=='consulta':
                r=thread.resultado;self.modelo.definir(r['colunas'],r['linhas']);self._mais=r['mais'];self._pagina_atual=r['pagina'];self.numero.setValue(r['pagina'])
                self._sql_resultado=r['sql'];self._base_resultado=r['base'];self._banco_resultado=r['banco'];self._tamanho_resultado=r['tamanho']
                for combo in (self.eixo_x,self.eixo_y):
                    atual=combo.currentText();combo.clear();combo.addItems(r['colunas']);combo.setCurrentText(atual)
                if len(r['colunas'])>1 and self.eixo_x.currentIndex()==self.eixo_y.currentIndex():
                    numericas=[j for j in range(len(r['colunas'])) if j!=self.eixo_x.currentIndex() and any(isinstance(l[j],(int,float)) for l in r['linhas'][:64])]
                    self.eixo_y.setCurrentIndex(numericas[0] if numericas else 1)
                ini=(r['pagina']-1)*r['tamanho']+1;fim=ini+len(r['linhas'])-1
                self.status.setText(f'Página {r["pagina"]}: linhas {ini if r["linhas"] else 0}–{fim if r["linhas"] else 0}. '+('Há mais linhas: use Próxima.' if r['mais'] else 'Fim da consulta.'))
            elif thread.tipo=='exportar':
                r=thread.resultado;self.status.setText(f'Excel salvo: {r["linhas"]:,} linhas em {r["abas"]} aba(s). {r["destino"]}')
            else:self._desenhar_grafico(thread.resultado)
        except Exception as erro:self.status.setText('Erro: '+str(erro))
        finally:self._atualizar_botoes();thread.deleteLater();self._thread=None

    def _carregar_banco(self,caminho,selecionada=None):
        if self.ocupado:return
        try:
            if self.banco.text() and Path(self.banco.text()).resolve()!=Path(caminho).resolve():self.novo_registro()
            nomes=dados.tabelas(caminho);atual=selecionada or self.tabelas.currentText();self.banco.setText(str(caminho))
            self.tabelas.clear();self.tabelas.addItems(nomes);self.tabelas.setCurrentText(atual)
            self.status.setText(f'{len(nomes)} tabelas/visões disponíveis. Selecione uma e clique em Ver tabela.')
        except Exception as erro:self.status.setText('Erro: '+str(erro))

    def recarregar_lista(self):
        if self.ocupado:return
        caminho=self.banco.text()
        if not caminho and projeto.projeto_aberto():caminho=str(projeto.caminho_trabalho())
        if caminho:self._carregar_banco(caminho)

    def _usar_projeto(self):
        try:self._carregar_banco(str(projeto.caminho_trabalho()))
        except RuntimeError as erro:self.status.setText(str(erro))

    def _abrir_banco(self):
        caminho,_=QFileDialog.getOpenFileName(self,'Abrir banco SQLite','','SQLite (*.sqlite *.sqlite3 *.db);;Todos os arquivos (*)')
        if caminho:self.novo_registro();self._carregar_banco(caminho)

    def abrir_tabela(self,caminho,tabela=None):
        self.novo_registro();self._carregar_banco(caminho,tabela)
        if self.tabelas.currentText():self._ver_tabela()

    def _ver_tabela(self):
        if self.ocupado:return
        try:self.sql.setPlainText(dados.consulta_tabela(self.banco.text(),self.tabelas.currentText()));self._executar_sql()
        except Exception as erro:self.status.setText('Erro: '+str(erro))

    def _executar_sql(self):
        if self.ocupado:return
        self.tabela.horizontalHeader().setSortIndicatorShown(False)
        self._ordem=None;consulta=self.sql.toPlainText();self._pagina(1,consulta,self.banco.text(),base=consulta)

    def _pagina(self,numero,sql=None,banco=None,base=None):
        if self.ocupado or numero<1:return
        sql=sql or self._sql_resultado;banco=banco or self._banco_resultado;tamanho=self.tamanho.currentData()
        if not sql:return
        base=base or self._base_resultado or sql
        def consultar():return dict(dados.pagina(banco,sql,numero,tamanho),sql=sql,banco=banco,base=base)
        self._iniciar('consulta',consultar)

    def _ordenar(self,coluna):
        if not self._sql_resultado or self.ocupado:return
        descendente=self._ordem==(coluna,False);self._ordem=(coluna,descendente)
        sql=f'SELECT * FROM ({self._base_resultado}) ORDER BY {dados.identificador(self.modelo.colunas[coluna])} '+('DESC' if descendente else 'ASC')
        self.tabela.horizontalHeader().setSortIndicator(coluna,Qt.SortOrder.DescendingOrder if descendente else Qt.SortOrder.AscendingOrder)
        self.tabela.horizontalHeader().setSortIndicatorShown(True);self._pagina(1,sql)

    def _exportar(self):
        if self.ocupado or not self._sql_resultado:return
        caminho,_=QFileDialog.getSaveFileName(self,'Exportar resultado completo','','Excel (*.xlsx)')
        if not caminho:return
        if not caminho.lower().endswith('.xlsx'):caminho+='.xlsx'
        banco,sql=self._banco_resultado,self._sql_resultado
        self._iniciar('exportar',lambda progresso:dados.exportar_excel(banco,sql,caminho,progresso))

    def _grafico(self):
        if self.ocupado or not self._sql_resultado:return
        banco,sql,x,y,agg=self._banco_resultado,self._sql_resultado,self.eixo_x.currentText(),self.eixo_y.currentText(),self.agregacao.currentData()
        tipo=self.tipo.currentData()
        self._iniciar('grafico',lambda:dict(linhas=dados.dados_grafico(banco,sql,x,y,agg),x=x,y=y,agg=agg,tipo=tipo))

    def _desenhar_grafico(self,r):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        if self._canvas is not None:self.gl.removeWidget(self._canvas);self._canvas.deleteLater()
        self._figura=Figure(figsize=(8,4),tight_layout=True);ax=self._figura.add_subplot(111)
        linhas=[(x,y) for x,y in r['linhas'] if y is not None];xs=[str(x) if x is not None else '(vazio)' for x,y in linhas];ys=[float(y) for x,y in linhas]
        if r['tipo']!='bar' and all(isinstance(x,(int,float)) for x,y in linhas):xs=[x for x,y in linhas]
        if r['tipo']=='bar':ax.bar(xs,ys)
        elif r['tipo']=='line':ax.plot(xs,ys,marker='o')
        else:ax.scatter(xs,ys)
        ax.set_xlabel(r['x']);ax.set_ylabel('Contagem' if r['agg']=='count' else r['y']);ax.tick_params(axis='x',labelrotation=45)
        if len(xs)>30:
            passo=max(1,len(xs)//20);ax.set_xticks(xs[::passo] if r['tipo']!='bar' and all(isinstance(x,(int,float)) for x in xs) else list(range(0,len(xs),passo)),[str(x) for x in xs[::passo]])
        item=self.gl.itemAt(2)
        if item is not None and item.spacerItem() is not None:self.gl.takeAt(2)
        if xs and r['tipo']!='bar' and all(isinstance(x,(int,float)) and float(x).is_integer() for x in xs):
            from matplotlib.ticker import MaxNLocator
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        self._canvas=FigureCanvasQTAgg(self._figura);self.gl.insertWidget(2,self._canvas,1);self._canvas.draw()
        self.status.setText(f'Gráfico da consulta completa: {len(linhas):,} pontos.');self.abas.setCurrentIndex(1)

    def _salvar_grafico(self):
        if self._figura is None:return
        caminho,_=QFileDialog.getSaveFileName(self,'Salvar gráfico','','Imagem PNG (*.png)')
        if caminho:
            try:self._figura.savefig(caminho if caminho.lower().endswith('.png') else caminho+'.png',dpi=180);self.status.setText('Gráfico salvo.')
            except Exception as erro:self.status.setText('Erro: '+str(erro))

    def novo_registro(self):
        if self.ocupado:return
        self.banco.clear();self.tabelas.clear();self.sql.clear();self.modelo.definir([],[]);self._sql_resultado='';self._base_resultado='';self._banco_resultado='';self._pagina_atual=1;self._mais=False;self._ordem=None
        self._figura=None
        if self._canvas is not None:self.gl.removeWidget(self._canvas);self._canvas.deleteLater();self._canvas=None
        self._atualizar_botoes()
