import collections
import functools
import logging
import math
import threading
import warnings

import numpy as np
from urllib import parse
from qtpy.QtCore import Slot, QThread, Signal, Qt
from qtpy.QtWidgets import QApplication

import pydm
from pydm.data_plugins.plugin import PyDMPlugin, PyDMConnection

logger = logging.getLogger(__name__)


def epics_string(value, string_encoding="utf-8"):
    # Stop at the first zero (EPICS convention)
    # Assume the ndarray is one-dimensional
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        zeros = np.where(value == 0)[0]
    if zeros.size > 0:
        value = value[:zeros[0]]
    r = value.tobytes().decode(string_encoding)
    return r


class CalcThread(QThread):
    eval_env = {'math': math,
                'np': np,
                'numpy': np,
                'epics_string': epics_string}

    new_data_signal = Signal(dict)

    def __init__(self, config, *args, **kwargs):
        QThread.__init__(self, *args, **kwargs)
        self.app = QApplication.instance()
        self.app.aboutToQuit.connect(self.requestInterruption)

        self.config = config
        self.listen_for_update = None

        self._calculate = threading.Event()
        self._names = []
        self._channels = []
        self._value = None
        self._values = collections.defaultdict(None)
        self._connections = collections.defaultdict(lambda: False)
        self._expression = self.config.get('expr', '')[0]

        channels = {}
        for key, channel in self.config.items():
            if key != 'update' and key != 'expr' and key != 'name':
                channels[key] = channel[0]

        update = self.config.get('update', None)

        if update is not None:
            self.listen_for_update = update[0].replace("[", "").replace("]", "").split(',')

        for name, channel in channels.items():
            conn_cb = functools.partial(self.callback_conn, name)
            value_cb = functools.partial(self.callback_value, name)
            c = pydm.PyDMChannel(channel, connection_slot=conn_cb,
                                 value_slot=value_cb)
            self._channels.append(c)
            self._names.append(name)

    @property
    def connected(self):
        return all(v for _, v in self._connections.items())

    def _connect(self):
        for ch in self._channels:
            ch.connect()

    def _disconnect(self):
        for ch in self._channels:
            ch.disconnect()

    def _send_update(self, conn, value):
        self.new_data_signal.emit({"connection": conn,
                                   "value": value})

    def run(self):
        self._connect()

        while True:
            self._calculate.wait()
            self._calculate.clear()
            if self.isInterruptionRequested():
                break
            self.calculate_expression()
        self._disconnect()

    def callback_value(self, name, value):
        """
        Callback executed when a channel receives a new value.

        Parameters
        ----------
        name : str
            The channel variable name.
        value : any
            The new value for this channel.

        Returns
        -------
        None
        """
        self._values[name] = value
        if not self.connected:
            logger.debug(
                "Calculation '%s': Not all channels are connected, skipping execution.",
                self.objectName())
            return

        if self.listen_for_update is None or name in self.listen_for_update:
            self._calculate.set()

    def callback_conn(self, name, value):
        """
        Callback executed when a channel connection status is changed.

        Parameters
        ----------
        name : str
            The channel variable name.
        value : bool
            Whether or not this channel is connected.

        """
        self._connections[name] = value
        self._send_update(self.connected, self._value)

    def calculate_expression(self):
        """
        Evaluate the expression defined by the rule and emit the `rule_signal`
        with the new value.
        """
        vals = self._values.copy()
        if any([vals.get(n) is None for n in self._names]):
            logger.debug('Skipping execution as not all values are set.')
            return

        env = dict(CalcThread.eval_env)
        env.update({k: v
                    for k, v in math.__dict__.items()
                    if k[0] != '_'})
        env.update(**vals)

        try:
            ret = eval(self._expression, env)
            self._value = ret
            self._send_update(self.connected, ret)
        except Exception as e:
            logger.exception("Error while evaluating CalcPlugin connection %s",
                             self.objectName())


class Connection(PyDMConnection):
    def __init__(self, channel, address, protocol=None, parent=None):
        super(Connection, self).__init__(channel, address, protocol, parent)
        self._calc_thread = None
        self.value = None
        self._configuration = {}
        self._waiting_config = True

        self.add_listener(channel)
        self._init_connection()

    def _init_connection(self):
        self.write_access_signal.emit(False)

    def add_listener(self, channel):
        self._setup_calc(channel)
        super(Connection, self).add_listener(channel)
        self.broadcast_value()

    def broadcast_value(self):
        self.connection_state_signal.emit(self.connected)
        if self.value is not None:
            self.new_value_signal[type(self.value)].emit(self.value)

    def _setup_calc(self, channel):
        if not self._waiting_config:
            logger.debug('CalcPlugin connection already configured.')
            return

        try:
            url_data = UrlToPython(channel).get_info()
        except ValueError("Not enough information"):
            logger.debug('Invalid configuration for Calc Plugin connection', exc_info=True)
            return

        self._configuration['name'] = url_data[1]
        self._configuration.update(url_data[0])
        self._waiting_config = False

        self._calc_thread = CalcThread(self._configuration)
        self._calc_thread.setObjectName("calc_{}".format(url_data[1]))
        self._calc_thread.new_data_signal.connect(self.receive_new_data,
                                                  Qt.QueuedConnection)
        self._calc_thread.start()
        return True

    @Slot(dict)
    def receive_new_data(self, data):
        if not data:
            return
        try:
            conn = data.get('connection')
            self.connected = conn
            self.connection_state_signal.emit(conn)
        except KeyError:
            logger.debug('Connection was not available yet for calc.')
        try:
            val = data.get('value')
            self.value = val
            if val is not None:
                self.new_value_signal[type(val)].emit(val)
        except KeyError:
            logger.debug('Value was not available yet for calc.')

    def close(self):
        self._calc_thread.requestInterruption()


class CalculationPlugin(PyDMPlugin):
    protocol = "calc"
    connection_class = Connection

    @staticmethod
    def get_connection_id(channel):
        obj = UrlToPython(channel)
        name = obj.get_info()[1]
        return name


class UrlToPython:
    def __init__(self, channel):
        self.channel = channel

    def get_info(self):
        """
        Parses a given url into a list and a string.

        Parameters
        ----------

        Returns
        -------
        A tuple: (<list>, <str>)
        """
        address = PyDMPlugin.get_address(self.channel)
        address = "calc://" + address
        name = None
        config = None

        try:
            config = parse.parse_qs(parse.urlsplit(address).query)
            name = parse.urlsplit(address).netloc

            if not name or not config:
                raise
        except Exception:
            try:
                if not name:
                    raise
                logger.debug('Calc Plugin  connection %s got new listener.', address)
                return None, name, address
            except Exeption:
                msg = "Invalid configuration for Calc Plugin  connection. %s"
                logger.exception(msg, address, exc_info=True)
                raise ValueError("error in Calc Plugin plugin input")

        return config, name, address
