from qtpy.QtCore import QModelIndex, QVariant, Slot
from .baseplot_table_model import BasePlotCurvesModel
from .baseplot_curve_editor import BasePlotCurveEditorDialog


class PyDMArchiverTimePlotCurvesModel(BasePlotCurvesModel):

    def __init__(self, plot, parent=None):
        super(PyDMArchiverTimePlotCurvesModel, self).__init__(plot, parent=parent)
        self._column_names = ("Channel", "Archive Data") + self._column_names

    def get_data(self, column_name, curve):
        if column_name == "Channel":
            if curve.address is None:
                return QVariant()
            return str(curve.address)
        elif column_name == "Archive Data":
            print(f'Get with ad: {curve.use_archive_data}')
            return bool(curve.use_archive_data)
        return super(PyDMArchiverTimePlotCurvesModel, self).get_data(column_name, curve)

    def set_data(self, column_name, curve, value):
        if column_name == "Channel":
            curve.address = str(value)
        elif column_name == "Archive Data":
            print(f'Set with ad: {value}')
            curve.use_archive_data = bool(value)
        else:
            return super(PyDMArchiverTimePlotCurvesModel, self).set_data(
                column_name=column_name, curve=curve, value=value)
        return True

    def append(self, address=None, name=None, color=None):
        self.beginInsertRows(QModelIndex(), len(self._plot._curves),
                             len(self._plot._curves))
        self._plot.addYChannel(address, name, color)
        self.endInsertRows()

    def removeAtIndex(self, index):
        self.beginRemoveRows(QModelIndex(), index.row(), index.row())
        self._plot.removeYChannelAtIndex(index.row())
        self.endRemoveRows()


class ArchiverTimePlotCurveEditorDialog(BasePlotCurveEditorDialog):
    """TimePlotCurveEditorDialog is a QDialog that is used in Qt Designer
    to edit the properties of the curves in a waveform plot.  This dialog is
    shown when you double-click the plot, or when you right click it and
    choose 'edit curves'.

    This thing is mostly just a wrapper for a table view, with a couple
    buttons to add and remove curves, and a button to save the changes."""
    TABLE_MODEL_CLASS = PyDMArchiverTimePlotCurvesModel

    def __init__(self, plot, parent=None):
        super(ArchiverTimePlotCurveEditorDialog, self).__init__(plot, parent)
        self.setup_delegate_columns(index=3)

    @Slot(int)
    def fillAxisData(self, tab_index, axis_name_col_index=4):
        super(ArchiverTimePlotCurveEditorDialog, self).fillAxisData(tab_index, axis_name_col_index=axis_name_col_index)
