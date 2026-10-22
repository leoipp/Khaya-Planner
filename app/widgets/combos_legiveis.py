"""Altura legível de comboboxes, inclusive dentro de tabelas e diálogos."""
from PySide6.QtCore import QObject,QEvent,QSize
from PySide6.QtWidgets import QComboBox,QStyledItemDelegate,QAbstractItemView,QHeaderView


class _OpcaoLegivel(QStyledItemDelegate):
    def sizeHint(self,opcao,indice):
        tamanho=super().sizeHint(opcao,indice)
        return QSize(tamanho.width(),max(tamanho.height(),opcao.fontMetrics.height()+12,32))


class _AjustarCombos(QObject):
    def eventFilter(self,obj,evento):
        if isinstance(obj,QComboBox) and evento.type() in (QEvent.Type.Polish,QEvent.Type.FontChange,QEvent.Type.StyleChange):
            altura=max(34,obj.fontMetrics().height()+14)
            if obj.minimumHeight()!=altura:obj.setMinimumHeight(altura)
            if obj.maximumHeight()<altura:obj.setMaximumHeight(altura)
            if not isinstance(obj.itemDelegate(),_OpcaoLegivel):obj.setItemDelegate(_OpcaoLegivel(obj))
            pai=obj.parentWidget()
            while pai is not None:
                if isinstance(pai,QAbstractItemView) and hasattr(pai,'verticalHeader'):
                    cab=pai.verticalHeader()
                    cab.setMinimumSectionSize(max(cab.minimumSectionSize(),altura+4))
                    cab.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
                    break
                pai=pai.parentWidget()
        return False


def instalar(app):
    if not hasattr(app,'_ajuste_combos'):
        app._ajuste_combos=_AjustarCombos(app)
        app.installEventFilter(app._ajuste_combos)
