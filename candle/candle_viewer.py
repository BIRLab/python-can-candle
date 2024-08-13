# mypy: disable-error-code="union-attr"
import sys
import usb
from can import BitTiming, BitTimingFd
from typing import Optional, List, Tuple, Union, Any, cast
from enum import Enum, auto
from functools import partial
from random import randrange
import csv
from PySide6.QtCore import (
    Signal,
    Slot,
    QObject,
    QTimer,
    Qt,
    QThread,
    QMutex,
    QMutexLocker,
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QCommandLineParser,
    QElapsedTimer
)
from PySide6.QtGui import (
    QFocusEvent,
    QFont,
    QCloseEvent
)
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QSpacerItem,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QMessageBox,
    QCheckBox,
    QGridLayout,
    QLineEdit,
    QSpinBox,
    QDialog,
    QGroupBox,
    QHeaderView,
    QTableView,
    QFileDialog,
    QProgressBar
)
from candle.candle_api import (
    CandleDevice,
    CandleInterface,
    CandleChannel,
    GSHostFrame,
    GSHostFrameHeader,
    GSCANFlag,
    GSDeviceBTConstExtended,
    GSDeviceBitTiming,
    GSCANFeature,
    DLC2LEN
)
from candle import __version__


class CandleManagerState(Enum):
    DeviceSelection = auto()
    ChannelSelection = auto()
    Configuration = auto()
    Running = auto()


class CandleManager(QObject):
    scanResult = Signal(list)
    selectDeviceResult = Signal(int, int, int)

    stateTransition = Signal(CandleManagerState, CandleManagerState)
    messageReceived = Signal(GSHostFrame)
    busLoad = Signal(int)
    exceptionOccurred = Signal(str)
    channelInfo = Signal(GSDeviceBTConstExtended)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.state = CandleManagerState.DeviceSelection

        self.device_list: List[CandleDevice] = []
        self.interface: Optional[CandleInterface] = None
        self.channel: Optional[CandleChannel] = None

        self.arbitration_rate: Optional[float] = None
        self.payload_rate: Optional[float] = None
        self.total_transfer_time: float = 0
        self.transfer_elapsed_timer = QElapsedTimer()
        self.transfer_elapsed_timer.start()
        self.transfer_time_mutex = QMutexLocker(QMutex())
        self.bus_load_calculation_timer = QTimer()
        self.bus_load_calculation_timer.setInterval(1000)
        self.bus_load_calculation_timer.timeout.connect(self.calculate_bus_load)
        self.bus_load_calculation_timer.start()

        self.state_transition_mutex = QMutexLocker(QMutex())

        self.polling_timer = QTimer()   # Do not set parent, timers cannot be stopped from another thread.
        self.polling_timer.timeout.connect(self.polling)
        self.polling_timer.setInterval(10)
        self.polling_timer.start()

    @Slot()
    def scan(self) -> None:
        with self.state_transition_mutex:
            if self.state == CandleManagerState.Running:
                self.channel.close()
                self.interface = None
                self.channel = None
            if self.state == CandleManagerState.Configuration:
                self.interface = None
                self.channel = None
            if self.state == CandleManagerState.ChannelSelection:
                self.interface = None
            self.transition(CandleManagerState.DeviceSelection)
            self.device_list.clear()
            try:
                self.device_list = list(CandleDevice.scan())
            except usb.USBError as e:
                self.handle_exception(str(e))
            else:
                self.scanResult.emit(self.device_list)

    @Slot(int)
    def select_device(self, index: int) -> None:
        with self.state_transition_mutex:
            if index < 0:
                return
            if self.state == CandleManagerState.DeviceSelection or self.state == CandleManagerState.ChannelSelection or self.state == CandleManagerState.Configuration:
                try:
                    self.interface = self.device_list[index][0]
                except usb.core.USBError as e:
                    self.handle_exception(str(e))
                else:
                    self.channel = None
                    self.transition(CandleManagerState.ChannelSelection)
                    self.selectDeviceResult.emit(self.interface.hardware_version, self.interface.software_version, len(self.interface))

    @Slot(int)
    def select_channel(self, index: int) -> None:
        with self.state_transition_mutex:
            if index < 0:
                return
            if self.state == CandleManagerState.ChannelSelection or self.state == CandleManagerState.Configuration:
                try:
                    self.channel = self.interface[index]    # type: ignore[index]
                except usb.core.USBError as e:
                    self.handle_exception(str(e))
                else:
                    self.channel.close()
                    self.transition(CandleManagerState.Configuration)
                    self.channelInfo.emit(self.channel._bt_const)

    @Slot(GSDeviceBitTiming)
    def set_bit_timing(self, bit_timing: GSDeviceBitTiming) -> None:
        with self.state_transition_mutex:
            if self.state == CandleManagerState.Configuration:
                try:
                    self.channel.set_bit_timing(bit_timing.prop_seg, bit_timing.phase_seg1, bit_timing.phase_seg2, bit_timing.sjw, bit_timing.brp)
                except usb.core.USBError as e:
                    self.handle_exception(str(e))
                else:
                    self.arbitration_rate = self.channel.clock_frequency / ((1 + bit_timing.prop_seg + bit_timing.phase_seg1 + bit_timing.phase_seg2) * bit_timing.brp)

    @Slot(GSDeviceBitTiming)
    def set_data_bit_timing(self, bit_timing: GSDeviceBitTiming) -> None:
        with self.state_transition_mutex:
            if self.state == CandleManagerState.Configuration:
                try:
                    self.channel.set_data_bit_timing(bit_timing.prop_seg, bit_timing.phase_seg1, bit_timing.phase_seg2, bit_timing.sjw, bit_timing.brp)
                except usb.core.USBError as e:
                    self.handle_exception(str(e))
                else:
                    self.payload_rate = self.channel.clock_frequency / ((1 + bit_timing.prop_seg + bit_timing.phase_seg1 + bit_timing.phase_seg2) * bit_timing.brp)

    @Slot(bool, bool, bool, bool, bool, bool)
    def start(self, fd: bool, loopback: bool, listen_only: bool, triple_sample: bool, one_shot: bool, bit_error_reporting: bool) -> None:
        with self.state_transition_mutex:
            if self.state == CandleManagerState.Configuration:
                try:
                    self.channel.open(fd, loopback, listen_only, triple_sample, one_shot, bit_error_reporting)
                except usb.core.USBError as e:
                    self.handle_exception(str(e))
                else:
                    self.transition(CandleManagerState.Running)

    @Slot()
    def stop(self) -> None:
        with self.state_transition_mutex:
            if self.state == CandleManagerState.Running:
                self.channel.close()
                self.transition(CandleManagerState.Configuration)

    @Slot(bool)
    def set_termination(self, state: bool) -> None:
        with self.state_transition_mutex:
            if self.state == CandleManagerState.Configuration:
                try:
                    self.channel.termination = state
                except usb.core.USBError as e:
                    self.handle_exception(str(e))

    @Slot(GSHostFrame)
    def send_message(self, frame: GSHostFrame) -> None:
        with self.state_transition_mutex:
            if self.state == CandleManagerState.Running:
                try:
                    self.channel.write(frame)
                except TimeoutError as e:
                    try:
                        self.channel.close()
                    except usb.core.USBError:
                        pass
                    self.handle_exception(str(e))

    def transition(self, to_state: CandleManagerState) -> None:
        if self.state != to_state:
            from_state = self.state
            self.state = to_state
            self.stateTransition.emit(from_state, self.state)

    def handle_exception(self, error: str) -> None:
        self.interface = None
        self.channel = None
        self.arbitration_rate = None
        self.payload_rate = None
        self.transition(CandleManagerState.DeviceSelection)
        self.exceptionOccurred.emit(error)

    def update_history(self, frame) -> None:
        if frame.header.is_error_frame:
            # Ignore error frame.
            return

        if self.arbitration_rate is None:
            return

        if frame.header.is_fd and frame.header.is_bitrate_switch and self.payload_rate is None:
            return

        # SOF RTR/RRS IDE r0/FDF ACK EOF IFS
        arbitration_bits = 16

        # ID
        if frame.header.is_extended_id:
            arbitration_bits += 29
        else:
            arbitration_bits += 11

        # res BRS
        if frame.header.is_fd:
            arbitration_bits += 2

        # payload
        if frame.header.is_fd:
            if frame.header.data_length > 16:
                # ESI DLC SBC 21-bit-CRC
                payload_bits = 31 + frame.header.data_length * 8
            else:
                # ESI DLC SBC 17-bit-CRC
                payload_bits = 27 + frame.header.data_length * 8
        else:
            payload_bits = 20 + frame.header.data_length * 8

        if frame.header.is_fd and frame.header.is_bitrate_switch:
            # BRS
            transfer_time = arbitration_bits / self.arbitration_rate + payload_bits / self.payload_rate
        else:
            transfer_time = (arbitration_bits + payload_bits) / self.arbitration_rate

        with self.transfer_time_mutex:
            self.total_transfer_time += transfer_time

    @Slot()
    def calculate_bus_load(self) -> None:
        if self.transfer_elapsed_timer.elapsed() == 0:
            return

        with self.transfer_time_mutex:
            bus_load = 1e3 * self.total_transfer_time / self.transfer_elapsed_timer.elapsed()
            self.total_transfer_time = 0
            self.busLoad.emit(round(100 * bus_load))
        self.transfer_elapsed_timer.restart()

    @Slot()
    def polling(self) -> None:
        with self.state_transition_mutex:
            if self.state == CandleManagerState.Running:
                try:
                    elapsed_timer = QElapsedTimer()
                    elapsed_timer.start()
                    while elapsed_timer.elapsed() < self.polling_timer.interval():
                        self.interface.polling(1)
                        while (frame := self.channel.read()) is not None:
                            self.update_history(frame)
                            self.messageReceived.emit(frame)
                except usb.core.USBTimeoutError:
                    pass
                except usb.core.USBError as e:
                    if e.errno != 32:   # Ignore pipe error.
                        try:
                            self.channel.close()
                        except usb.core.USBError:
                            pass
                        self.handle_exception(str(e))

    @Slot()
    def cleanup(self) -> None:
        if self.channel is not None:
            try:
                self.channel.close()
            except usb.core.USBError:
                pass


class InputPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self.grid_layout = QGridLayout(self)
        for i in range(8):
            self.grid_layout.addWidget(QLabel(str(i + 1)), 0, i + 1)
        for i in range(8):
            self.grid_layout.addWidget(QLabel(str(i + 1)), i + 1, 0)
        previous_line_edit: Optional[QLineEdit] = None
        for i in range(8):
            for j in range(8):
                line_edit = QLineEdit()
                line_edit.setInputMask('hh')
                line_edit.setFixedWidth(24)
                line_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
                line_edit.setText('00')
                line_edit.mousePressEvent = partial(self.get_focus, line_edit=line_edit)    # type: ignore[method-assign]
                if previous_line_edit:
                    previous_line_edit.textChanged.connect(partial(self.focus_next, next_line_edit=line_edit))
                previous_line_edit = line_edit
                self.grid_layout.addWidget(line_edit, i + 1, j + 1)
        self.setLayout(self.grid_layout)

    @staticmethod
    def get_focus(_event: QFocusEvent, line_edit: QLineEdit) -> None:
        if line_edit.isEnabled():
            line_edit.selectAll()

    @staticmethod
    def focus_next(text: str, next_line_edit: QLineEdit) -> None:
        if len(text) >= 2:
            if next_line_edit.isEnabled():
                next_line_edit.setFocus()
                next_line_edit.selectAll()

    @Slot(int)
    def set_dlc(self, dlc: int) -> None:
        for i in range(64):
            row = i // 8
            column = i % 8
            line_edit: QLineEdit = cast(QLineEdit, self.grid_layout.itemAtPosition(row + 1, column + 1).widget())
            if i < DLC2LEN[dlc]:
                line_edit.setEnabled(True)
            else:
                line_edit.setEnabled(False)

    @Slot()
    def random(self) -> None:
        for i in range(64):
            row = i // 8
            column = i % 8
            line_edit: QLineEdit = cast(QLineEdit, self.grid_layout.itemAtPosition(row + 1, column + 1).widget())
            if line_edit.isEnabled():
                line_edit.setText(f'{randrange(0, 256):02X}')

    def data(self) -> bytes:
        data: List[int] = []
        if self.isEnabled():
            for i in range(64):
                row = i // 8
                column = i % 8
                line_edit: QLineEdit = cast(QLineEdit, self.grid_layout.itemAtPosition(row + 1, column + 1).widget())
                if line_edit.isEnabled():
                    data.append(int(line_edit.text(), 16))
        return bytes(data)


class MessageTableModel(QAbstractTableModel):
    rowInserted = Signal(int, int)
    exportFinished = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.mutex = QMutexLocker(QMutex())
        self.header = ('Timestamp', 'CAN ID', 'Rx/Tx', 'Type', 'Length', 'Data')
        self.message_buffer: List[GSHostFrame] = []
        self.message_pending: List[GSHostFrame] = []
        self.monospace_font = QFont('Monospace', 10)
        self.monospace_font.setStyleHint(QFont.StyleHint.TypeWriter)
        self.monospace_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.flush_timer = QTimer()     # Do not set parent, timers cannot be stopped from another thread.
        self.flush_timer.timeout.connect(self.flush_message)
        self.flush_timer.setInterval(50)
        self.flush_timer.start()

    @Slot(str)
    def export(self, file_path: str) -> None:
        with open(file_path, 'w', newline='') as csv_file:
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(self.header)
            for i in range(self.rowCount()):
                csv_writer.writerow([self.data(self.index(i, j)) for j in range(self.columnCount())])
        self.exportFinished.emit()

    @Slot(GSHostFrame)
    def handle_message(self, message: GSHostFrame) -> None:
        with self.mutex:
            self.message_pending.append(message)

    @Slot()
    def flush_message(self) -> None:
        with self.mutex:
            if self.message_pending:
                first_row = len(self.message_buffer)
                last_row = len(self.message_buffer) + len(self.message_pending) - 1
                self.beginInsertRows(QModelIndex(), first_row, last_row)
                self.message_buffer.extend(self.message_pending)
                self.message_pending.clear()
                self.endInsertRows()
                self.rowInserted.emit(first_row, last_row)

    @Slot()
    def clear_message(self) -> None:
        with self.mutex:
            self.beginResetModel()
            self.message_pending.clear()
            self.message_buffer.clear()
            self.endResetModel()

    def rowCount(self, parent: Any = None) -> int:
        return len(self.message_buffer)

    def columnCount(self, parent: Any = None) -> int:
        return len(self.header)

    def data(self, index: Union[QModelIndex, QPersistentModelIndex], role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            message = self.message_buffer[index.row()]
            column = index.column()
            if column == 0:
                return str(message.timestamp)
            if column == 1:
                return f'0x{message.header.arbitration_id:08X}' if message.header.is_extended_id else f'0x{message.header.arbitration_id:03X}'
            if column == 2:
                return 'Rx' if message.header.is_rx else 'Tx'
            if column == 3:
                if message.header.is_error_frame:
                    return 'Error'
                if message.header.is_remote_frame:
                    return 'Remote'
                if message.header.is_fd:
                    fd_flags = ['FD']
                    if message.header.is_bitrate_switch:
                        fd_flags.append('BRS')
                    if message.header.is_error_state_indicator:
                        fd_flags.append('ESI')
                    return ' '.join(fd_flags)
                else:
                    return 'Data'
            if column == 4:
                return str(message.header.data_length)
            if column == 5:
                if message.header.is_error_frame:
                    error_flags = []
                    if message.header.can_id & (1 << 0):
                        error_flags.append('TX timeout')
                    if message.header.can_id & (1 << 1):
                        error_flags.append(f'arbitration lost in bit {message.data[0]}')
                    if message.header.can_id & (1 << 2):
                        error_flags.append('controller problems')
                        if message.data[1] & (1 << 0):
                            error_flags.append('RX buffer overflow')
                        if message.data[1] & (1 << 1):
                            error_flags.append('TX buffer overflow')
                        if message.data[1] & (1 << 2):
                            error_flags.append('reached warning level for RX errors')
                        if message.data[1] & (1 << 3):
                            error_flags.append('reached warning level for TX errors')
                        if message.data[1] & (1 << 4):
                            error_flags.append('reached error passive status RX')
                        if message.data[1] & (1 << 5):
                            error_flags.append('reached error passive status TX')
                        if message.data[1] & (1 << 6):
                            error_flags.append('recovered to error active state')
                    if message.header.can_id & (1 << 3):
                        error_flags.append('protocol violations')
                        if message.data[2] & (1 << 0):
                            error_flags.append('single bit error')
                        if message.data[2] & (1 << 1):
                            error_flags.append('frame format error')
                        if message.data[2] & (1 << 2):
                            error_flags.append('bit stuffing error')
                        if message.data[2] & (1 << 3):
                            error_flags.append('unable to send dominant bit')
                        if message.data[2] & (1 << 4):
                            error_flags.append('unable to send recessive bit')
                        if message.data[2] & (1 << 5):
                            error_flags.append('bus overload')
                        if message.data[2] & (1 << 6):
                            error_flags.append('active error announcement')
                        if message.data[2] & (1 << 7):
                            error_flags.append('error occurred on transmission')
                        if message.data[3] == 0x03:
                            error_flags.append('start of frame')
                        if message.data[3] == 0x02:
                            error_flags.append('ID bits 28 - 21 (SFF: 10 - 3)')
                        if message.data[3] == 0x06:
                            error_flags.append('ID bits 20 - 18 (SFF: 2 - 0 )')
                        if message.data[3] == 0x04:
                            error_flags.append('substitute RTR (SFF: RTR)')
                        if message.data[3] == 0x05:
                            error_flags.append('identifier extension')
                        if message.data[3] == 0x07:
                            error_flags.append('ID bits 17-13')
                        if message.data[3] == 0x0F:
                            error_flags.append('ID bits 12-5')
                        if message.data[3] == 0x0E:
                            error_flags.append('ID bits 4-0')
                        if message.data[3] == 0x0C:
                            error_flags.append('RTR')
                        if message.data[3] == 0x0D:
                            error_flags.append('reserved bit 1')
                        if message.data[3] == 0x09:
                            error_flags.append('reserved bit 0')
                        if message.data[3] == 0x0B:
                            error_flags.append('data length code')
                        if message.data[3] == 0x0A:
                            error_flags.append('data section')
                        if message.data[3] == 0x08:
                            error_flags.append('CRC sequence')
                        if message.data[3] == 0x18:
                            error_flags.append('CRC delimiter')
                        if message.data[3] == 0x19:
                            error_flags.append('ACK slot')
                        if message.data[3] == 0x1B:
                            error_flags.append('ACK delimiter')
                        if message.data[3] == 0x1A:
                            error_flags.append('end of frame')
                        if message.data[3] == 0x12:
                            error_flags.append('intermission')
                    if message.header.can_id & (1 << 4):
                        error_flags.append('transceiver status')
                        if message.data[4] == 0x04:
                            error_flags.append('CANH no wire')
                        if message.data[4] == 0x05:
                            error_flags.append('CANH short to BAT')
                        if message.data[4] == 0x06:
                            error_flags.append('CANH short to VCC')
                        if message.data[4] == 0x07:
                            error_flags.append('CANH short to GND')
                        if message.data[4] == 0x40:
                            error_flags.append('CANL no wire')
                        if message.data[4] == 0x50:
                            error_flags.append('CANL short to BAT')
                        if message.data[4] == 0x60:
                            error_flags.append('CANL short to VCC')
                        if message.data[4] == 0x70:
                            error_flags.append('CANL short to GND')
                        if message.data[4] == 0x80:
                            error_flags.append('CANL short to CANH')
                    if message.header.can_id & (1 << 5):
                        error_flags.append('received no ACK on transmission')
                    if message.header.can_id & (1 << 6):
                        error_flags.append('bus off')
                    if message.header.can_id & (1 << 7):
                        error_flags.append('bus error')
                    if message.header.can_id & (1 << 8):
                        error_flags.append('controller restarted')
                    error_flags.append(f'TX error count: {message.data[6]}')
                    error_flags.append(f'RX error count: {message.data[7]}')
                    return ' '.join(f'{i:02X}' for i in message.data) + ' (' + ', '.join(error_flags) + ')'
                else:
                    return ' '.join(f'{i:02X}' for i in message.data)
        if role == Qt.ItemDataRole.FontRole:
            return self.monospace_font
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return self.header[section]
            elif orientation == Qt.Orientation.Vertical:
                return str(section + 1)
        return None


class BitTimingDialog(QDialog):
    setBitTiming = Signal(GSDeviceBitTiming)
    setDataBitTiming = Signal(GSDeviceBitTiming)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Bit Timing Configuration')
        self.channel_info = GSDeviceBTConstExtended(GSCANFeature(-1), -1, -1, -1, -1, -1, -1, -1, -1, -1)
        self.bit_timing: Optional[Union[BitTiming, BitTimingFd]] = None

        vbox_layout = QVBoxLayout()
        hbox_layout1 = QHBoxLayout()
        self.frequency_label = QLabel('Clock Frequency: unknown')
        self.enable_fd_checkbox = QCheckBox('Enable FD')
        self.enable_fd_checkbox.setChecked(True)
        hbox_layout1.addWidget(self.frequency_label)
        hbox_layout1.addWidget(self.enable_fd_checkbox)
        hbox_layout2 = QHBoxLayout()
        nominal_group_box = QGroupBox('Nominal Bit Rate')
        grid_layout1 = QGridLayout()
        self.nominal_bitrate_combox = QComboBox()
        self.nominal_bitrate_combox.addItems(['1000', '800', '500', '250', '125', '100', '83.333', '50', '20', '10'])
        self.nominal_bitrate_combox.setEditable(True)
        self.nominal_sample_point_combox = QComboBox()
        self.nominal_sample_point_combox.addItems(['87.5', '75', '62.5', '50'])
        self.nominal_sample_point_combox.setEditable(True)
        grid_layout1.addWidget(QLabel('Bit Rate [kbit/s]:'), 0, 0)
        grid_layout1.addWidget(self.nominal_bitrate_combox, 0, 1)
        grid_layout1.addWidget(QLabel('Sample Point [%]:'), 1, 0)
        grid_layout1.addWidget(self.nominal_sample_point_combox, 1, 1)
        nominal_group_box.setLayout(grid_layout1)
        data_group_box = QGroupBox('Data Bit Rate')
        grid_layout2 = QGridLayout()
        self.data_bitrate_combox = QComboBox()
        self.data_bitrate_combox.addItems(['15000', '12000', '8000', '5000', '2000', '1000'])
        self.data_bitrate_combox.setEditable(True)
        self.data_sample_point_combox = QComboBox()
        self.data_sample_point_combox.addItems(['87.5', '75', '62.5', '50'])
        self.data_sample_point_combox.setEditable(True)
        grid_layout2.addWidget(QLabel('Bit Rate [kbit/s]:'), 0, 0)
        grid_layout2.addWidget(self.data_bitrate_combox, 0, 1)
        grid_layout2.addWidget(QLabel('Sample Point [%]:'), 1, 0)
        grid_layout2.addWidget(self.data_sample_point_combox, 1, 1)
        data_group_box.setLayout(grid_layout2)
        hbox_layout2.addWidget(nominal_group_box)
        hbox_layout2.addWidget(data_group_box)
        result_group_box = QGroupBox('Solution')
        vbox_layout1 = QVBoxLayout()
        self.bit_timing_table = QTableWidget()
        self.bit_timing_table.setColumnCount(6)
        self.bit_timing_table.setRowCount(2)
        self.bit_timing_table.setHorizontalHeaderLabels(['Prescaler', 'TSEG1', 'TSEG2', 'SJW', 'tq', 'Nq'])
        self.bit_timing_table.setVerticalHeaderLabels(['Nominal', 'Data'])
        self.bit_timing_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.bit_timing_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.bit_timing_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_label = QLabel()
        vbox_layout1.addWidget(self.bit_timing_table)
        vbox_layout1.addWidget(self.result_label)
        result_group_box.setLayout(vbox_layout1)
        self.ok_button = QPushButton('OK')
        vbox_layout.addLayout(hbox_layout1)
        vbox_layout.addLayout(hbox_layout2)
        vbox_layout.addWidget(result_group_box)
        vbox_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        vbox_layout.addWidget(self.ok_button)
        self.setLayout(vbox_layout)
        self.nominal_bitrate_combox.currentIndexChanged.connect(self.calculate_bit_timing)
        self.nominal_sample_point_combox.currentIndexChanged.connect(self.calculate_bit_timing)
        self.data_bitrate_combox.currentIndexChanged.connect(self.calculate_bit_timing)
        self.data_sample_point_combox.currentIndexChanged.connect(self.calculate_bit_timing)
        self.enable_fd_checkbox.toggled.connect(self.calculate_bit_timing)
        self.ok_button.clicked.connect(self.set_bit_timing)

    def reset_calculate(self) -> None:
        self.bit_timing_table.setItem(0, 0, QTableWidgetItem('-'))
        self.bit_timing_table.setItem(0, 1, QTableWidgetItem('-'))
        self.bit_timing_table.setItem(0, 2, QTableWidgetItem('-'))
        self.bit_timing_table.setItem(0, 3, QTableWidgetItem('-'))
        self.bit_timing_table.setItem(0, 4, QTableWidgetItem('-'))
        self.bit_timing_table.setItem(0, 5, QTableWidgetItem('-'))
        self.bit_timing_table.setItem(1, 0, QTableWidgetItem('-'))
        self.bit_timing_table.setItem(1, 1, QTableWidgetItem('-'))
        self.bit_timing_table.setItem(1, 2, QTableWidgetItem('-'))
        self.bit_timing_table.setItem(1, 3, QTableWidgetItem('-'))
        self.bit_timing_table.setItem(1, 4, QTableWidgetItem('-'))
        self.bit_timing_table.setItem(1, 5, QTableWidgetItem('-'))

    @Slot()
    @Slot(int)
    @Slot(bool)
    def calculate_bit_timing(self, *_args, **_kwargs) -> None:
        self.frequency_label.setText(f'Clock Frequency: {round(self.channel_info.fclk_can / 1e6)} MHz')
        if self.enable_fd_checkbox.isEnabled() and self.enable_fd_checkbox.isChecked():
            self.data_bitrate_combox.setEnabled(True)
            self.data_sample_point_combox.setEnabled(True)
            try:
                self.bit_timing = BitTimingFd.from_sample_point(
                    f_clock=self.channel_info.fclk_can,
                    nom_bitrate=round(float(self.nominal_bitrate_combox.currentText()) * 1e3),
                    nom_sample_point=float(self.nominal_sample_point_combox.currentText()),
                    data_bitrate=round(float(self.data_bitrate_combox.currentText()) * 1e3),
                    data_sample_point=float(self.data_sample_point_combox.currentText())
                )
            except ValueError:
                self.reset_calculate()
                self.ok_button.setEnabled(False)
                self.result_label.setText('Cannot find a satisfactory solution.')
            else:
                self.bit_timing_table.setItem(0, 0, QTableWidgetItem(str(self.bit_timing.nom_brp)))
                self.bit_timing_table.setItem(0, 1, QTableWidgetItem(str(self.bit_timing.nom_tseg1)))
                self.bit_timing_table.setItem(0, 2, QTableWidgetItem(str(self.bit_timing.nom_tseg2)))
                self.bit_timing_table.setItem(0, 3, QTableWidgetItem(str(self.bit_timing.nom_sjw)))
                self.bit_timing_table.setItem(0, 4, QTableWidgetItem(f'{self.bit_timing.nom_tq} ns'))
                self.bit_timing_table.setItem(0, 5, QTableWidgetItem(str(self.bit_timing.nbt)))
                self.bit_timing_table.setItem(1, 0, QTableWidgetItem(str(self.bit_timing.data_brp)))
                self.bit_timing_table.setItem(1, 1, QTableWidgetItem(str(self.bit_timing.data_tseg1)))
                self.bit_timing_table.setItem(1, 2, QTableWidgetItem(str(self.bit_timing.data_tseg2)))
                self.bit_timing_table.setItem(1, 3, QTableWidgetItem(str(self.bit_timing.data_sjw)))
                self.bit_timing_table.setItem(1, 4, QTableWidgetItem(f'{self.bit_timing.data_tq} ns'))
                self.bit_timing_table.setItem(1, 5, QTableWidgetItem(str(self.bit_timing.dbt)))
                self.result_label.setText(f'Nominal Bit Rate {self.bit_timing.nom_bitrate / 1e3} kbit/s\tNominal Sample Point {self.bit_timing.nom_sample_point}%\nData Bit Rate {self.bit_timing.data_bitrate / 1e3} kbit/s\tData Sample Point {self.bit_timing.data_sample_point}%')
                self.ok_button.setEnabled(True)
        else:
            self.data_bitrate_combox.setEnabled(False)
            self.data_sample_point_combox.setEnabled(False)
            try:
                self.bit_timing = BitTiming.from_sample_point(
                    f_clock=self.channel_info.fclk_can,
                    bitrate=round(float(self.nominal_bitrate_combox.currentText()) * 1e3),
                    sample_point=float(self.nominal_sample_point_combox.currentText())
                )
            except ValueError:
                self.reset_calculate()
                self.ok_button.setEnabled(False)
                self.result_label.setText('Cannot find a satisfactory solution.')
            else:
                self.bit_timing_table.setItem(0, 0, QTableWidgetItem(str(self.bit_timing.brp)))
                self.bit_timing_table.setItem(0, 1, QTableWidgetItem(str(self.bit_timing.tseg1)))
                self.bit_timing_table.setItem(0, 2, QTableWidgetItem(str(self.bit_timing.tseg2)))
                self.bit_timing_table.setItem(0, 3, QTableWidgetItem(str(self.bit_timing.sjw)))
                self.bit_timing_table.setItem(0, 4, QTableWidgetItem(f'{self.bit_timing.tq} ns'))
                self.bit_timing_table.setItem(0, 5, QTableWidgetItem(str(self.bit_timing.nbt)))
                self.bit_timing_table.setItem(1, 0, QTableWidgetItem('-'))
                self.bit_timing_table.setItem(1, 1, QTableWidgetItem('-'))
                self.bit_timing_table.setItem(1, 2, QTableWidgetItem('-'))
                self.bit_timing_table.setItem(1, 3, QTableWidgetItem('-'))
                self.bit_timing_table.setItem(1, 4, QTableWidgetItem('-'))
                self.bit_timing_table.setItem(1, 5, QTableWidgetItem('-'))
                self.result_label.setText(f'Nominal Bit Rate {self.bit_timing.bitrate / 1e3} kbit/s\tNominal Sample Point {self.bit_timing.sample_point}%')
                self.ok_button.setEnabled(True)

    @Slot(GSDeviceBTConstExtended)
    def update_channel_info(self, info: GSDeviceBTConstExtended) -> None:
        self.channel_info = info
        self.enable_fd_checkbox.setEnabled(bool(self.channel_info.feature & GSCANFeature.FD))
        self.calculate_bit_timing()

    @Slot()
    def set_bit_timing(self) -> None:
        if self.bit_timing is not None:
            if isinstance(self.bit_timing, BitTiming):
                self.setBitTiming.emit(GSDeviceBitTiming(1, self.bit_timing.tseg1 - 1, self.bit_timing.tseg2, self.bit_timing.sjw, self.bit_timing.brp))
            if isinstance(self.bit_timing, BitTimingFd):
                self.setBitTiming.emit(
                    GSDeviceBitTiming(1, self.bit_timing.nom_tseg1 - 1, self.bit_timing.nom_tseg2, self.bit_timing.nom_sjw, self.bit_timing.nom_brp))
                self.setDataBitTiming.emit(GSDeviceBitTiming(1, self.bit_timing.data_tseg1 - 1, self.bit_timing.data_tseg2, self.bit_timing.data_sjw, self.bit_timing.data_brp))
            self.accept()


class MainWindow(QWidget):
    export = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Candle Viewer')
        self.resize(1280, 720)

        # Setup UI.
        vbox_layout = QVBoxLayout(self)
        hbox_layout1 = QHBoxLayout()
        self.scan_button = QPushButton('Scan')
        self.device_selector = QComboBox()
        self.device_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.channel_selector = QComboBox()
        self.channel_selector.setEnabled(False)
        self.bit_timing_button = QPushButton('Set BitTiming')
        self.bit_timing_button.setEnabled(False)
        self.start_button = QPushButton('Start')
        self.start_button.setCheckable(True)
        self.start_button.setEnabled(False)
        self.version_label = QLabel()
        hbox_layout1.addWidget(self.scan_button)
        hbox_layout1.addWidget(QLabel('Device:'))
        hbox_layout1.addWidget(self.device_selector)
        hbox_layout1.addWidget(QLabel('Channel:'))
        hbox_layout1.addWidget(self.channel_selector)
        hbox_layout1.addWidget(self.bit_timing_button)
        hbox_layout1.addWidget(self.start_button)
        hbox_layout1.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        hbox_layout1.addWidget(self.version_label)
        hbox_layout2 = QHBoxLayout()
        self.fd_checkbox = QCheckBox('FD')
        self.fd_checkbox.setEnabled(False)
        self.loopback_checkbox = QCheckBox('Loopback')
        self.loopback_checkbox.setEnabled(False)
        self.listen_only_checkbox = QCheckBox('Listen Only')
        self.listen_only_checkbox.setEnabled(False)
        self.triple_sample_checkbox = QCheckBox('Triple Sample')
        self.triple_sample_checkbox.setEnabled(False)
        self.one_shot_checkbox = QCheckBox('One Shot')
        self.one_shot_checkbox.setEnabled(False)
        self.bit_error_reporting_checkbox = QCheckBox('Bit Error Reporting')
        self.bit_error_reporting_checkbox.setEnabled(False)
        self.termination_checkbox = QCheckBox('Termination')
        self.termination_checkbox.setEnabled(False)
        self.auto_scroll_checkbox = QCheckBox('Auto Scroll')
        self.auto_scroll_checkbox.setChecked(True)
        clear_button = QPushButton('Clear')
        export_button = QPushButton('Export')
        hbox_layout2.addWidget(self.fd_checkbox)
        hbox_layout2.addWidget(self.loopback_checkbox)
        hbox_layout2.addWidget(self.listen_only_checkbox)
        hbox_layout2.addWidget(self.triple_sample_checkbox)
        hbox_layout2.addWidget(self.one_shot_checkbox)
        hbox_layout2.addWidget(self.bit_error_reporting_checkbox)
        hbox_layout2.addWidget(self.termination_checkbox)
        hbox_layout2.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        hbox_layout2.addWidget(self.auto_scroll_checkbox)
        hbox_layout2.addWidget(clear_button)
        hbox_layout2.addWidget(export_button)
        self.message_viewer = QTableView()
        self.message_viewer.horizontalHeader().setStretchLastSection(True)
        hbox_layout3 = QHBoxLayout()
        vbox_layout1 = QVBoxLayout()
        self.send_id_spin_box = QSpinBox()
        self.send_id_spin_box.setDisplayIntegerBase(16)
        font = QFont()
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        self.send_id_spin_box.setFont(font)
        self.send_id_spin_box.setEnabled(False)
        self.send_dlc_selector = QComboBox()
        self.send_dlc_selector.setEnabled(False)
        self.send_eff_checkbox = QCheckBox('EFF')
        self.send_eff_checkbox.setEnabled(False)
        self.send_rtr_checkbox = QCheckBox('RTR')
        self.send_rtr_checkbox.setEnabled(False)
        self.send_err_checkbox = QCheckBox('ERR')
        self.send_err_checkbox.setEnabled(False)
        self.send_fd_checkbox = QCheckBox('FD')
        self.send_fd_checkbox.setEnabled(False)
        self.send_brs_checkbox = QCheckBox('BRS')
        self.send_brs_checkbox.setEnabled(False)
        self.send_esi_checkbox = QCheckBox('ESI')
        self.send_esi_checkbox.setEnabled(False)
        vbox_layout1.addWidget(QLabel('CAN ID'))
        vbox_layout1.addWidget(self.send_id_spin_box)
        vbox_layout1.addWidget(QLabel('DLC'))
        vbox_layout1.addWidget(self.send_dlc_selector)
        vbox_layout1.addWidget(self.send_eff_checkbox)
        vbox_layout1.addWidget(self.send_rtr_checkbox)
        vbox_layout1.addWidget(self.send_err_checkbox)
        vbox_layout1.addWidget(self.send_fd_checkbox)
        vbox_layout1.addWidget(self.send_brs_checkbox)
        vbox_layout1.addWidget(self.send_esi_checkbox)
        vbox_layout1.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        self.input_panel = InputPanel()
        self.input_panel.setEnabled(False)
        vbox_layout2 = QVBoxLayout()
        self.send_once_button = QPushButton('Send Once')
        self.send_once_button.setEnabled(False)
        self.send_repeat_button = QPushButton('Send Repeat')
        self.send_repeat_button.setEnabled(False)
        self.send_repeat_button.setCheckable(True)
        self.cycle_time_spin_box = QSpinBox()
        self.cycle_time_spin_box.setSuffix(' ms')
        self.cycle_time_spin_box.setMinimum(1)
        self.cycle_time_spin_box.setMaximum(100000)
        self.cycle_time_spin_box.setValue(10)
        self.cycle_time_spin_box.setEnabled(False)
        self.random_data_button = QPushButton('Random Data')
        self.random_data_button.setEnabled(False)
        vbox_layout2.addWidget(self.send_once_button)
        vbox_layout2.addWidget(self.send_repeat_button)
        vbox_layout2.addWidget(QLabel('Cycle Time'))
        vbox_layout2.addWidget(self.cycle_time_spin_box)
        vbox_layout2.addWidget(self.random_data_button)
        vbox_layout2.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        hbox_layout3.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        hbox_layout3.addLayout(vbox_layout1)
        hbox_layout3.addWidget(self.input_panel)
        hbox_layout3.addLayout(vbox_layout2)
        hbox_layout3.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        vbox_layout_3 = QVBoxLayout()
        bus_load_bar = QProgressBar()
        bus_load_bar.setOrientation(Qt.Vertical)
        bus_load_bar.setMaximum(100)
        bus_load_bar.setValue(0)
        bus_load_bar.setTextVisible(False)
        bus_load_label = QLabel('0%')
        vbox_layout_3.addWidget(QLabel('Load'), alignment=Qt.AlignmentFlag.AlignHCenter)
        vbox_layout_3.addWidget(bus_load_bar, alignment=Qt.AlignmentFlag.AlignHCenter)
        vbox_layout_3.addWidget(bus_load_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        hbox_layout3.addLayout(vbox_layout_3)
        vbox_layout.addLayout(hbox_layout1)
        vbox_layout.addLayout(hbox_layout2)
        vbox_layout.addWidget(self.message_viewer, 1)
        vbox_layout.addLayout(hbox_layout3)
        self.setLayout(vbox_layout)

        # Prepare candle manager and polling thread.
        self.polling_thread = QThread(self)
        self.candle_manager = CandleManager()
        self.candle_manager.moveToThread(self.polling_thread)

        # Timer for send message.
        self.send_timer = QTimer(self)
        self.send_timer.setInterval(self.cycle_time_spin_box.value())

        # Message model for better performance.
        self.message_model_thread = QThread(self)
        self.message_model = MessageTableModel()
        self.message_model.moveToThread(self.message_model_thread)
        self.message_viewer.setModel(self.message_model)

        # Dialog for configurate bit timing setting.
        self.bit_timing_dialog = BitTimingDialog(self)

        # Connect signals and slots.
        self.candle_manager.stateTransition.connect(self.handle_state_transition)
        self.scan_button.clicked.connect(self.candle_manager.scan)
        self.candle_manager.scanResult.connect(self.handle_scan_result)
        self.device_selector.currentIndexChanged.connect(self.candle_manager.select_device)
        self.channel_selector.currentIndexChanged.connect(self.candle_manager.select_channel)
        self.candle_manager.messageReceived.connect(self.message_model.handle_message)
        self.candle_manager.exceptionOccurred.connect(self.handle_device_exception)
        self.start_button.toggled.connect(self.handle_start)
        self.bit_timing_dialog.setBitTiming.connect(self.candle_manager.set_bit_timing)
        self.bit_timing_dialog.setDataBitTiming.connect(self.candle_manager.set_data_bit_timing)
        self.candle_manager.channelInfo.connect(self.bit_timing_dialog.update_channel_info)
        self.bit_timing_button.clicked.connect(self.bit_timing_dialog.exec)
        self.send_dlc_selector.currentIndexChanged.connect(self.input_panel.set_dlc)
        self.send_once_button.clicked.connect(self.send_message)
        self.send_fd_checkbox.toggled.connect(self.handle_send_fd_checked)
        self.cycle_time_spin_box.valueChanged.connect(lambda v: self.send_timer.setInterval(v))
        self.send_timer.timeout.connect(self.send_message)
        self.send_repeat_button.toggled.connect(self.send_message_repeat)
        self.send_eff_checkbox.toggled.connect(self.handle_extended_id_checked)
        self.random_data_button.clicked.connect(self.input_panel.random)
        self.message_model.rowInserted.connect(self.handle_row_inserted)
        self.polling_thread.finished.connect(self.candle_manager.cleanup)
        clear_button.clicked.connect(self.message_model.clear_message)
        export_button.clicked.connect(self.handle_export)
        self.termination_checkbox.toggled.connect(self.candle_manager.set_termination)
        self.candle_manager.selectDeviceResult.connect(self.handle_select_device_result)
        self.export.connect(self.message_model.export)
        self.message_model.exportFinished.connect(self.handle_export_finished)
        self.candle_manager.busLoad.connect(bus_load_bar.setValue)
        self.candle_manager.busLoad.connect(lambda x: bus_load_label.setText(f'{x}%'))

        # Start thread and timer.
        self.polling_thread.start()
        self.message_model_thread.start()

    def send_message_repeat(self, checked: bool) -> None:
        if checked:
            self.send_timer.start()
        else:
            self.send_timer.stop()

    @Slot()
    def send_message(self) -> None:
        data = self.input_panel.data()
        header = GSHostFrameHeader(0, self.send_id_spin_box.value(), self.send_dlc_selector.currentIndex(), self.channel_selector.currentIndex(), GSCANFlag(0))
        header.is_extended_id = self.send_eff_checkbox.isChecked()
        header.is_remote_frame = self.send_rtr_checkbox.isChecked()
        header.is_error_frame = self.send_err_checkbox.isChecked()
        header.is_fd = self.send_fd_checkbox.isEnabled() and self.send_fd_checkbox.isChecked()
        header.is_bitrate_switch = self.send_brs_checkbox.isEnabled() and self.send_brs_checkbox.isChecked()
        header.is_error_state_indicator = self.send_esi_checkbox.isEnabled() and self.send_esi_checkbox.isChecked()
        self.candle_manager.send_message(GSHostFrame(header, data, 0))

    @Slot(int, int)
    def handle_row_inserted(self, first_row: int, last_row: int) -> None:
        for row in range(first_row, last_row + 1):
            self.message_viewer.resizeRowToContents(row)
        if self.auto_scroll_checkbox.isChecked():
            self.message_viewer.scrollToBottom()

    @Slot(bool)
    def handle_extended_id_checked(self, checked: bool) -> None:
        if checked:
            self.send_id_spin_box.setMaximum((1 << 29) - 1)
        else:
            self.send_id_spin_box.setMaximum((1 << 11) - 1)

    @Slot(bool)
    def handle_send_fd_checked(self, checked: bool) -> None:
        self.send_dlc_selector.clear()
        self.send_dlc_selector.addItems([str(i) for i in DLC2LEN[:9]])
        if checked:
            self.send_dlc_selector.addItems([str(i) for i in DLC2LEN[9:]])

    @Slot(CandleManagerState)
    def handle_state_transition(self, _from_state: CandleManagerState, to_state: CandleManagerState) -> None:
        if to_state == CandleManagerState.DeviceSelection:
            self.device_selector.setEnabled(False)
            self.channel_selector.setEnabled(False)
            self.bit_timing_button.setEnabled(False)
            self.start_button.setEnabled(False)
            self.fd_checkbox.setEnabled(False)
            self.loopback_checkbox.setEnabled(False)
            self.listen_only_checkbox.setEnabled(False)
            self.triple_sample_checkbox.setEnabled(False)
            self.one_shot_checkbox.setEnabled(False)
            self.bit_error_reporting_checkbox.setEnabled(False)
            self.termination_checkbox.setEnabled(False)
            self.send_id_spin_box.setEnabled(False)
            self.send_dlc_selector.setEnabled(False)
            self.send_eff_checkbox.setEnabled(False)
            self.send_rtr_checkbox.setEnabled(False)
            self.send_err_checkbox.setEnabled(False)
            self.send_fd_checkbox.setEnabled(False)
            self.send_brs_checkbox.setEnabled(False)
            self.send_esi_checkbox.setEnabled(False)
            self.input_panel.setEnabled(False)
            self.send_once_button.setEnabled(False)
            self.send_repeat_button.setEnabled(False)
            self.send_repeat_button.setChecked(False)
            self.cycle_time_spin_box.setEnabled(False)
            self.random_data_button.setEnabled(False)
            self.version_label.clear()
        if to_state == CandleManagerState.ChannelSelection:
            self.device_selector.setEnabled(True)
            self.channel_selector.setEnabled(True)
            self.bit_timing_button.setEnabled(False)
            self.start_button.setEnabled(False)
            self.fd_checkbox.setEnabled(False)
            self.loopback_checkbox.setEnabled(False)
            self.listen_only_checkbox.setEnabled(False)
            self.triple_sample_checkbox.setEnabled(False)
            self.one_shot_checkbox.setEnabled(False)
            self.bit_error_reporting_checkbox.setEnabled(False)
            self.termination_checkbox.setEnabled(False)
            self.send_id_spin_box.setEnabled(False)
            self.send_dlc_selector.setEnabled(False)
            self.send_eff_checkbox.setEnabled(False)
            self.send_rtr_checkbox.setEnabled(False)
            self.send_err_checkbox.setEnabled(False)
            self.send_fd_checkbox.setEnabled(False)
            self.send_brs_checkbox.setEnabled(False)
            self.send_esi_checkbox.setEnabled(False)
            self.input_panel.setEnabled(False)
            self.send_once_button.setEnabled(False)
            self.send_repeat_button.setEnabled(False)
            self.send_repeat_button.setChecked(False)
            self.cycle_time_spin_box.setEnabled(False)
            self.random_data_button.setEnabled(False)
        if to_state == CandleManagerState.Configuration:
            self.device_selector.setEnabled(True)
            self.channel_selector.setEnabled(True)
            self.bit_timing_button.setEnabled(True)
            self.start_button.setEnabled(True)
            try:
                self.fd_checkbox.setEnabled(self.candle_manager.channel.is_fd_supported)
                self.loopback_checkbox.setEnabled(self.candle_manager.channel.is_loop_back_supported)
                self.listen_only_checkbox.setEnabled(self.candle_manager.channel.is_listen_only_supported)
                self.triple_sample_checkbox.setEnabled(self.candle_manager.channel.is_triple_sample_supported)
                self.one_shot_checkbox.setEnabled(self.candle_manager.channel.is_one_shot_supported)
                self.bit_error_reporting_checkbox.setEnabled(self.candle_manager.channel.is_bit_error_reporting_supported)
                self.termination_checkbox.setEnabled(self.candle_manager.channel.is_termination_supported)
                if self.candle_manager.channel.is_termination_supported:
                    self.termination_checkbox.setChecked(self.candle_manager.channel.termination)
            except AttributeError:
                pass
            self.send_id_spin_box.setEnabled(False)
            self.send_dlc_selector.setEnabled(False)
            self.send_eff_checkbox.setEnabled(False)
            self.send_rtr_checkbox.setEnabled(False)
            self.send_err_checkbox.setEnabled(False)
            self.send_fd_checkbox.setEnabled(False)
            self.send_brs_checkbox.setEnabled(False)
            self.send_esi_checkbox.setEnabled(False)
            self.input_panel.setEnabled(False)
            self.send_once_button.setEnabled(False)
            self.send_repeat_button.setEnabled(False)
            self.send_repeat_button.setChecked(False)
            self.cycle_time_spin_box.setEnabled(False)
            self.random_data_button.setEnabled(False)
            self.start_button.setChecked(False)
            try:
                self.fd_checkbox.setChecked(self.candle_manager.channel.is_fd_supported)
            except AttributeError:
                pass
        if to_state == CandleManagerState.Running:
            self.device_selector.setEnabled(False)
            self.channel_selector.setEnabled(False)
            self.bit_timing_button.setEnabled(False)
            self.start_button.setEnabled(True)
            self.fd_checkbox.setEnabled(False)
            self.loopback_checkbox.setEnabled(False)
            self.listen_only_checkbox.setEnabled(False)
            self.triple_sample_checkbox.setEnabled(False)
            self.one_shot_checkbox.setEnabled(False)
            self.bit_error_reporting_checkbox.setEnabled(False)
            self.termination_checkbox.setEnabled(False)
            self.send_id_spin_box.setEnabled(True)
            self.send_dlc_selector.setEnabled(True)
            self.send_eff_checkbox.setEnabled(True)
            self.send_rtr_checkbox.setEnabled(True)
            self.send_err_checkbox.setEnabled(True)
            self.send_fd_checkbox.setEnabled(self.candle_manager.channel.is_fd_supported)
            self.send_brs_checkbox.setEnabled(self.candle_manager.channel.is_fd_supported)
            self.send_esi_checkbox.setEnabled(self.candle_manager.channel.is_fd_supported)
            self.input_panel.setEnabled(True)
            self.send_once_button.setEnabled(True)
            self.send_repeat_button.setEnabled(True)
            self.send_repeat_button.setChecked(False)
            self.cycle_time_spin_box.setEnabled(True)
            self.random_data_button.setEnabled(True)
            self.handle_send_fd_checked(self.send_fd_checkbox.isChecked())
            self.handle_extended_id_checked(self.send_eff_checkbox.isChecked())

    @Slot(list)
    def handle_scan_result(self, result: List[CandleDevice]) -> None:
        self.device_selector.clear()
        self.device_selector.addItems([str(i) for i in result])

    @Slot(int, int, int)
    def handle_select_device_result(self, hw_version, sw_version, channel_num) -> None:
        self.channel_selector.clear()
        self.channel_selector.addItems([str(i) for i in range(channel_num)])
        self.version_label.setText(f'HW: {hw_version}, SW: {sw_version}')

    @Slot(bool)
    def handle_start(self, start: bool) -> None:
        if start:
            self.candle_manager.start(
                self.fd_checkbox.isChecked() if self.fd_checkbox.isEnabled() else False,
                self.loopback_checkbox.isChecked() if self.loopback_checkbox.isEnabled() else False,
                self.listen_only_checkbox.isChecked() if self.listen_only_checkbox.isEnabled() else False,
                self.triple_sample_checkbox.isChecked() if self.triple_sample_checkbox.isEnabled() else False,
                self.one_shot_checkbox.isChecked() if self.one_shot_checkbox.isEnabled() else False,
                self.bit_error_reporting_checkbox.isChecked() if self.bit_error_reporting_checkbox.isEnabled() else False
            )
        else:
            self.candle_manager.stop()

    @Slot(str)
    def handle_device_exception(self, error: str) -> None:
        message_box = QMessageBox(self)
        message_box.setText(error)
        message_box.open()

    @Slot()
    def handle_export(self) -> None:
        file_name = QFileDialog.getSaveFileName(self, filter="CSV (*.csv)")[0]
        if not file_name.endswith('.csv'):
            file_name += '.csv'
        self.export.emit(file_name)

    @Slot()
    def handle_export_finished(self) -> None:
        message_box = QMessageBox(self)
        message_box.setText('Export Finished')
        message_box.open()

    def closeEvent(self, event: QCloseEvent):
        self.polling_thread.requestInterruption()
        self.message_model_thread.requestInterruption()
        self.polling_thread.quit()
        self.message_model_thread.quit()
        self.polling_thread.wait()
        self.message_model_thread.wait()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setApplicationName("candle_viewer")
    app.setApplicationVersion(__version__)

    parser = QCommandLineParser()
    parser.setApplicationDescription("Candle Viewer")
    parser.addHelpOption()
    parser.addVersionOption()
    parser.process(app)

    main_window = MainWindow()
    main_window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
