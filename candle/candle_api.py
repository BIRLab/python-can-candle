from __future__ import annotations

from weakref import WeakSet
import usb
import usb.backend
from enum import IntEnum, IntFlag
from struct import Struct
from dataclasses import dataclass, astuple
from typing import Optional, Generator


backend: Optional[usb.backend.IBackend] = None


# Using libusb1 backend from libusb package.
try:
    from libusb._platform import DLL_PATH
    from usb.backend.libusb1 import get_backend
    backend = get_backend(find_library=lambda x: DLL_PATH)
except ImportError:
    pass


def bit(x: int) -> int:
    return 1 << x


@dataclass
class DeviceIdentifier:
    idVendor: int
    idProduct: int


device_identifiers: list[DeviceIdentifier] = [
    DeviceIdentifier(0x1d50, 0x606f),
    DeviceIdentifier(0x1209, 0x2323),
    DeviceIdentifier(0x1cd2, 0x606f),
    DeviceIdentifier(0x16d0, 0x10b8),
    DeviceIdentifier(0x16d0, 0x0f30)
]


class GSUsbRequest(IntEnum):
    HOST_FORMAT = 0
    BITTIMING = 1
    MODE = 2
    BERR = 3
    BT_CONST = 4
    DEVICE_CONFIG = 5
    TIMESTAMP = 6
    IDENTIFY = 7
    GET_USER_ID = 8
    SET_USER_ID = 9
    DATA_BITTIMING = 10
    BT_CONST_EXT = 11
    SET_TERMINATION = 12
    GET_TERMINATION = 13
    GET_STATE = 14


class GSDeviceModeControl(IntEnum):
    RESET = 0
    START = 1


class GSCANState(IntEnum):
    ERROR_ACTIVE = 0
    ERROR_WARNING = 1
    ERROR_PASSIVE = 2
    BUS_OFF = 3
    STOPPED = 4
    SLEEPING = 5


class GSCANIdentifyMode(IntEnum):
    OFF = 0
    ON = 1


class GSCANTerminationState(IntEnum):
    OFF = 0
    ON = 1


class GSTerminationConstance(IntEnum):
    DISABLED = 0
    ENABLED = 120


class GSCANFeature(IntFlag):
    LISTEN_ONLY = bit(0)
    LOOP_BACK = bit(1)
    TRIPLE_SAMPLE = bit(2)
    ONE_SHOT = bit(3)
    HW_TIMESTAMP = bit(4)
    IDENTIFY = bit(5)
    USER_ID = bit(6)
    PAD_PKTS_TO_MAX_PKT_SIZE = bit(7)
    FD = bit(8)
    REQ_USB_QUIRK_LPC546XX = bit(9)
    BT_CONST_EXT = bit(10)
    TERMINATION = bit(11)
    BERR_REPORTING = bit(12)
    GET_STATE = bit(13)
    QUIRK_BREQ_CANTACT_PRO = bit(31)


class GSCANMode(IntFlag):
    NORMAL = bit(0)
    LISTEN_ONLY = bit(0)
    LOOP_BACK = bit(1)
    TRIPLE_SAMPLE = bit(2)
    ONE_SHOT = bit(3)
    HW_TIMESTAMP = bit(4)
    PAD_PKTS_TO_MAX_PKT_SIZE = bit(7)
    FD = bit(8)
    BERR_REPORTING = bit(12)


class GSCANFlag(IntFlag):
    OVERFLOW = bit(0)
    FD = bit(1)
    BRS = bit(2)
    ESI = bit(3)


class GSCANIDFlag(IntFlag):
    EFF = bit(31)
    RTR = bit(30)
    ERR = bit(29)
    SFF_MASK = bit(11) - 1
    EFF_MASK = bit(29) - 1
    ERR_MASK = bit(29) - 1


@dataclass
class GSHostConfig:
    byte_order: int


@dataclass
class GSDeviceConfig:
    icount: int
    sw_version: int
    hw_version: int


@dataclass
class GSDeviceBTConst:
    feature: int
    fclk_can: int
    tseg1_min: int
    tseg1_max: int
    tseg2_min: int
    tseg2_max: int
    sjw_max: int
    brp_min: int
    brp_max: int
    brp_inc: int


@dataclass
class GSDeviceBTConstExtended(GSDeviceBTConst):
    dtseg1_min: int = -1
    dtseg1_max: int = -1
    dtseg2_min: int = -1
    dtseg2_max: int = -1
    dsjw_max: int = -1
    dbrp_min: int = -1
    dbrp_max: int = -1
    dbrp_inc: int = -1


@dataclass
class GSDeviceMode:
    mode: int
    flags: GSCANMode


@dataclass
class GSHostFrameHeader:
    echo_id: int
    can_id: int
    can_dlc: int
    channel: int
    flags: GSCANFlag

    @property
    def arbitration_id(self) -> int:
        if self.is_extended_id:
            return self.can_id & GSCANIDFlag.EFF_MASK
        return self.can_id & GSCANIDFlag.SFF_MASK

    @property
    def data_length(self) -> int:
        return DLC2LEN[self.can_dlc]

    @data_length.setter
    def data_length(self, value: int) -> None:
        self.can_dlc = DLC2LEN.index(value)

    @property
    def is_rx(self) -> bool:
        return self.echo_id == 0xFFFFFFFF

    @property
    def is_fd(self) -> bool:
        return bool(self.flags & GSCANFlag.FD)

    @is_fd.setter
    def is_fd(self, value: bool) -> None:
        if value:
            self.flags |= GSCANFlag.FD
        else:
            self.flags &= ~GSCANFlag.FD

    @property
    def is_bitrate_switch(self) -> bool:
        return bool(self.flags & GSCANFlag.BRS)

    @is_bitrate_switch.setter
    def is_bitrate_switch(self, value: bool) -> None:
        if value:
            self.flags |= GSCANFlag.BRS
        else:
            self.flags &= ~GSCANFlag.BRS

    @property
    def is_error_state_indicator(self) -> bool:
        return bool(self.flags & GSCANFlag.ESI)

    @is_error_state_indicator.setter
    def is_error_state_indicator(self, value: bool) -> None:
        if value:
            self.flags |= GSCANFlag.ESI
        else:
            self.flags &= ~GSCANFlag.ESI

    @property
    def is_extended_id(self) -> bool:
        return bool(self.can_id & GSCANIDFlag.EFF)

    @is_extended_id.setter
    def is_extended_id(self, value: bool) -> None:
        if value:
            self.can_id |= GSCANIDFlag.EFF
        else:
            self.can_id &= ~GSCANIDFlag.EFF

    @property
    def is_remote_frame(self) -> bool:
        return bool(self.can_id & GSCANIDFlag.RTR)

    @is_remote_frame.setter
    def is_remote_frame(self, value: bool) -> None:
        if value:
            self.can_id |= GSCANIDFlag.RTR
        else:
            self.can_id &= ~GSCANIDFlag.RTR

    @property
    def is_error_frame(self) -> bool:
        return bool(self.can_id & GSCANIDFlag.ERR)

    @is_error_frame.setter
    def is_error_frame(self, value: bool) -> None:
        if value:
            self.can_id |= GSCANIDFlag.ERR
        else:
            self.can_id &= ~GSCANIDFlag.ERR


@dataclass
class GSDeviceBitTiming:
    prop_seg: int
    phase_seg1: int
    phase_seg2: int
    sjw: int
    brp: int


@dataclass
class GSDeviceTerminationState:
    state: GSCANTerminationState


@dataclass
class GSDeviceState:
    state: GSCANState
    rxerr: int
    txerr: int


gs_host_config_struct = Struct('<I')
gs_device_config_struct = Struct('<3xB2I')
gs_device_bt_const_struct = Struct('<10I')
gs_device_bt_const_extended_struct = Struct('<18I')
gs_device_mode_struct = Struct('<2I')
gs_host_frame_header_struct = Struct('<2I3Bx')
gs_device_bit_timing_struct = Struct('<5I')
gs_device_termination_state_struct = Struct('<I')
gs_device_state_struct = Struct('<3I')

DLC2LEN: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)


class GSHostFrame:
    def __init__(self, header: GSHostFrameHeader, data: bytes, timestamp_us: int = 0) -> None:
        self.header = header
        self.data = data
        self.timestamp_us = timestamp_us

    @property
    def timestamp(self) -> float:
        return self.timestamp_us / 1e6

    def pack(self, is_quirk_device: bool) -> bytes:
        frame = gs_host_frame_header_struct.pack(*astuple(self.header))

        if self.header.is_fd:
            frame += self.data.ljust(64, b'\0')
        else:
            frame += self.data.ljust(8, b'\0')

        if is_quirk_device:
            frame += b'\0'

        return frame

    @classmethod
    def unpack(cls, frame: bytes, is_hardware_timestamp: bool) -> GSHostFrame:
        header = GSHostFrameHeader(*gs_host_frame_header_struct.unpack(frame[:gs_host_frame_header_struct.size]))
        data = frame[gs_host_frame_header_struct.size:gs_host_frame_header_struct.size + header.data_length]
        gs_host_frame = cls(header, data)
        if is_hardware_timestamp:
            gs_host_frame.timestamp_us = int.from_bytes(frame[-4:], 'little', signed=False)
        return gs_host_frame


class CandleChannel:
    def __init__(self, parent: CandleInterface, usb_device: usb.core.Device, channel: int, endpoint_in: int, endpoint_out: int) -> None:
        self._parent = parent
        self._usb_device = usb_device
        self._channel = channel
        self._endpoint_in = endpoint_in
        self._endpoint_out = endpoint_out

        self._bt_const: GSDeviceBTConstExtended = GSDeviceBTConstExtended(
            *gs_device_bt_const_struct.unpack(
                self._usb_device.ctrl_transfer(
                    usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_INTERFACE,
                    GSUsbRequest.BT_CONST,
                    channel,
                    0,
                    gs_device_bt_const_struct.size,
                    1000
                )
            )
        )

        if self.is_fd_supported:
            self._bt_const = GSDeviceBTConstExtended(
                *gs_device_bt_const_extended_struct.unpack(
                    self._usb_device.ctrl_transfer(
                        usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_INTERFACE,
                        GSUsbRequest.BT_CONST_EXT,
                        channel,
                        0,
                        gs_device_bt_const_extended_struct.size,
                        1000
                    )
                )
            )

        self._flags: GSCANMode = GSCANMode.NORMAL

    @property
    def index(self) -> int:
        return self._channel

    @property
    def is_fd_supported(self) -> bool:
        return bool(self._bt_const.feature & GSCANFeature.FD)

    @property
    def is_listen_only_supported(self) -> bool:
        return bool(self._bt_const.feature & GSCANFeature.LISTEN_ONLY)

    @property
    def is_loop_back_supported(self) -> bool:
        return bool(self._bt_const.feature & GSCANFeature.LOOP_BACK)

    @property
    def is_triple_sample_supported(self) -> bool:
        return bool(self._bt_const.feature & GSCANFeature.TRIPLE_SAMPLE)

    @property
    def is_one_shot_supported(self) -> bool:
        return bool(self._bt_const.feature & GSCANFeature.ONE_SHOT)

    @property
    def is_hardware_timestamp_supported(self) -> bool:
        return bool(self._bt_const.feature & GSCANFeature.HW_TIMESTAMP)

    @property
    def is_bit_error_reporting_supported(self) -> bool:
        return bool(self._bt_const.feature & GSCANFeature.BERR_REPORTING)

    @property
    def is_get_state_supported(self) -> bool:
        return bool(self._bt_const.feature & GSCANFeature.GET_STATE)

    @property
    def is_quirk(self) -> bool:
        return bool(self._bt_const.feature & GSCANFeature.REQ_USB_QUIRK_LPC546XX)

    @property
    def clock_frequency(self) -> int:
        return self._bt_const.fclk_can

    @property
    def tseg1_min(self) -> int:
        return self._bt_const.tseg1_min

    @property
    def tseg1_max(self) -> int:
        return self._bt_const.tseg1_max

    @property
    def tseg2_min(self) -> int:
        return self._bt_const.tseg2_min

    @property
    def tseg2_max(self) -> int:
        return self._bt_const.tseg2_max

    @property
    def sjw_max(self) -> int:
        return self._bt_const.sjw_max

    @property
    def brp_min(self) -> int:
        return self._bt_const.brp_min

    @property
    def brp_max(self) -> int:
        return self._bt_const.brp_max

    @property
    def brp_inc(self) -> int:
        return self._bt_const.brp_inc

    @property
    def dtseg1_min(self) -> int:
        return self._bt_const.dtseg1_min

    @property
    def dtseg1_max(self) -> int:
        return self._bt_const.dtseg1_max

    @property
    def dtseg2_min(self) -> int:
        return self._bt_const.dtseg2_min

    @property
    def dtseg2_max(self) -> int:
        return self._bt_const.dtseg2_max

    @property
    def dsjw_max(self) -> int:
        return self._bt_const.dsjw_max

    @property
    def dbrp_min(self) -> int:
        return self._bt_const.dbrp_min

    @property
    def dbrp_max(self) -> int:
        return self._bt_const.dbrp_max

    @property
    def dbrp_inc(self) -> int:
        return self._bt_const.dbrp_inc

    def open(self, fd: bool = False, loopback: bool = False, listen_only: bool = False, triple_sample: bool = False, one_shot: bool = False, bit_error_reporting: bool = False) -> None:
        flags: GSCANMode = GSCANMode.NORMAL
        if loopback:
            assert self.is_loop_back_supported
            flags |= GSCANMode.LOOP_BACK
        if listen_only:
            assert self.is_listen_only_supported
            flags |= GSCANMode.LISTEN_ONLY
        if triple_sample:
            assert self.is_triple_sample_supported
            flags |= GSCANMode.TRIPLE_SAMPLE
        if one_shot:
            assert self.is_one_shot_supported
            flags |= GSCANMode.ONE_SHOT
        if bit_error_reporting:
            assert self.is_bit_error_reporting_supported
            flags |= GSCANMode.BERR_REPORTING
        if fd:
            assert self.is_fd_supported
            flags |= GSCANMode.FD

        if self.is_hardware_timestamp_supported:
            flags |= GSCANMode.HW_TIMESTAMP

        self._flags = flags
        self._usb_device.ctrl_transfer(
            usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_INTERFACE,
            GSUsbRequest.MODE,
            self._channel,
            0,
            gs_device_mode_struct.pack(*astuple(GSDeviceMode(GSDeviceModeControl.START, self._flags))),
            1000
        )

    def close(self) -> None:
        self._usb_device.ctrl_transfer(
            usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_INTERFACE,
            GSUsbRequest.MODE,
            self._channel,
            0,
            gs_device_mode_struct.pack(*astuple(GSDeviceMode(GSDeviceModeControl.RESET, GSCANMode.NORMAL))),
            1000
        )

    def reconfigure(self, fd: bool = False, loopback: bool = False, listen_only: bool = False, triple_sample: bool = False, one_shot: bool = False, bit_error_reporting: bool = False) -> None:
        self.close()
        self.open(fd, loopback, listen_only, triple_sample, one_shot, bit_error_reporting)

    def set_bit_timing(self, prop_seg: int, phase_seg1: int, phase_seg2: int, sjw: int, brp: int) -> None:
        dbt = GSDeviceBitTiming(
            prop_seg=prop_seg,
            phase_seg1=phase_seg1,
            phase_seg2=phase_seg2,
            sjw=sjw,
            brp=brp
        )
        self._usb_device.ctrl_transfer(
            usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_INTERFACE,
            GSUsbRequest.BITTIMING,
            self._channel,
            0,
            gs_device_bit_timing_struct.pack(*astuple(dbt)),
            1000
        )

    def set_data_bit_timing(self, dprop_seg: int, dphase_seg1: int, dphase_seg2: int, dsjw: int, dbrp: int) -> None:
        dbt = GSDeviceBitTiming(
            prop_seg=dprop_seg,
            phase_seg1=dphase_seg1,
            phase_seg2=dphase_seg2,
            sjw=dsjw,
            brp=dbrp
        )
        req: GSUsbRequest = GSUsbRequest.DATA_BITTIMING
        if self._bt_const.feature & GSCANFeature.QUIRK_BREQ_CANTACT_PRO:
            # CANtact Pro original firmware:
            # BREQ DATA_BITTIMING overlaps with GET_USER_ID
            req = GSUsbRequest.GET_USER_ID
        self._usb_device.ctrl_transfer(
            usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_INTERFACE,
            req,
            self._channel,
            0,
            gs_device_bit_timing_struct.pack(*astuple(dbt)),
            1000
        )

    @property
    def termination(self) -> bool:
        termination_state: GSDeviceTerminationState = GSDeviceTerminationState(
            *gs_device_termination_state_struct.unpack(
                self._usb_device.ctrl_transfer(
                    usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_INTERFACE,
                    GSUsbRequest.GET_TERMINATION,
                    self._channel,
                    0,
                    gs_device_termination_state_struct.size,
                    1000
                )
            )
        )
        return termination_state.state == GSCANTerminationState.ON

    @termination.setter
    def termination(self, value: bool) -> None:
        termination_state: GSDeviceTerminationState = GSDeviceTerminationState(
            state=GSCANTerminationState.OFF
        )
        if value:
            termination_state.state = GSCANTerminationState.ON
        self._usb_device.ctrl_transfer(
            usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_INTERFACE,
            GSUsbRequest.SET_TERMINATION,
            self._channel,
            0,
            gs_device_bit_timing_struct.pack(*astuple(termination_state)),
            1000
        )

    @property
    def state(self) -> GSDeviceState:
        return GSDeviceState(
            *gs_device_state_struct.unpack(
                self._usb_device.ctrl_transfer(
                    usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_INTERFACE,
                    GSUsbRequest.GET_STATE,
                    self._channel,
                    0,
                    gs_device_state_struct.size,
                    1000
                )
            )
        )

    @property
    def software_version(self) -> int:
        return self._parent.software_version

    @property
    def hardware_version(self) -> int:
        return self._parent.hardware_version

    def read(self, timeout_ms: Optional[int] = None) -> GSHostFrame:
        rx_size = gs_host_frame_header_struct.size

        if self.is_fd_supported:
            rx_size += 64
        else:
            rx_size += 8

        if self.is_hardware_timestamp_supported:
            rx_size += 4

        raw_frame = bytes(self._usb_device.read(self._endpoint_in, rx_size, timeout_ms))
        return GSHostFrame.unpack(raw_frame, self.is_hardware_timestamp_supported)

    def write(self, host_frame: GSHostFrame, timeout_ms: Optional[int] = None) -> None:

        # If the device does not have CAN FD enabled, but somehow receives a CAN FD Frame, drop it.
        if host_frame.header.is_fd and not self._flags & GSCANMode.FD:
            return

        self._usb_device.write(self._endpoint_out, host_frame.pack(self.is_quirk), timeout_ms)


class CandleInterface:
    def __init__(self, parent: CandleDevice, usb_device: usb.core.Device, interface_number: int) -> None:
        self._parent = parent
        self._usb_device = usb_device
        self._interface_number = interface_number

        self._usb_device.ctrl_transfer(
            usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_INTERFACE,
            GSUsbRequest.HOST_FORMAT,
            1,
            self._interface_number,
            gs_host_config_struct.pack(*astuple(GSHostConfig(0x0000beef))),
            1000
        )

        self._device_configuration: GSDeviceConfig = GSDeviceConfig(
            *gs_device_config_struct.unpack(
                self._usb_device.ctrl_transfer(
                    usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_INTERFACE,
                    GSUsbRequest.DEVICE_CONFIG,
                    1,
                    self._interface_number,
                    gs_device_config_struct.size,
                    1000
                )
            )
        )

    @property
    def software_version(self) -> int:
        return self._device_configuration.sw_version

    @property
    def hardware_version(self) -> int:
        return self._device_configuration.hw_version

    def __len__(self) -> int:
        return self._device_configuration.icount + 1

    def __getitem__(self, channel: int, endpoint_in: Optional[int] = None, endpoint_out: Optional[int] = None) -> CandleChannel:
        # Alternate interface setting is assumed to be 0.
        alternate_setting = 0

        # Find endpoint IN.
        if endpoint_in is None:
            endpoint_in_description: usb.core.Endpoint = usb.util.find_descriptor(
                self._usb_device.get_active_configuration()[self._interface_number, alternate_setting],
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
            )
            if endpoint_in_description is None:
                raise IndexError('Can not find a suitable IN endpoint, maybe specify it manually.')
            endpoint_in = endpoint_in_description.bEndpointAddress

        # Find endpoint OUT.
        if endpoint_out is None:
            endpoint_out_description: usb.core.Endpoint = usb.util.find_descriptor(
                self._usb_device.get_active_configuration()[self._interface_number, alternate_setting],
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
            )
            if endpoint_out_description is None:
                raise IndexError('Can not find a suitable OUT endpoint, maybe specify it manually.')
            endpoint_out = endpoint_out_description.bEndpointAddress

        return CandleChannel(self, self._usb_device, channel, endpoint_in, endpoint_out)


class CandleDevice:
    # Weak reference container for CandleDevice
    devices_ref: WeakSet[CandleDevice] = WeakSet()

    def __init__(self, usb_device: usb.core.Device):
        self._usb_device = usb_device

        # Forward usb descriptions from usb device.
        self.idVendor: int = self._usb_device.idVendor
        self.idProduct: int = self._usb_device.idProduct
        self.manufacturer: str | None = self._usb_device.manufacturer
        self.product: str | None = self._usb_device.product
        self.serial_number: str | None = self._usb_device.serial_number

        # Remove reset function call.
        # https://github.com/ryedwards/gs_usb/commit/9ac2286d6265ff124353ca678093570ef2095348
        # self._usb_device.reset()

    def __del__(self) -> None:
        usb.util.dispose_resources(self._usb_device)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CandleInterface):
            return False
        return self._usb_device == other._usb_device

    def __hash__(self) -> int:
        return hash(self._usb_device)

    def __len__(self) -> int:
        return self._usb_device.get_active_configuration().bNumInterfaces

    def __getitem__(self, interface_number: int) -> CandleInterface:
        try:
            if self._usb_device.is_kernel_driver_active(interface_number):
                self._usb_device.detach_kernel_driver(interface_number)
        except NotImplementedError:
            pass

        return CandleInterface(self, self._usb_device, interface_number)

    def __str__(self) -> str:
        return f'{self.idVendor:04X}:{self.idProduct:04X} - {self.manufacturer} - {self.product} - {self.serial_number}'

    @classmethod
    def scan(cls, vid: Optional[int] = None, pid: Optional[int] = None, manufacture: Optional[str] = None, product: Optional[str] = None, serial_number: Optional[str] = None) -> Generator[CandleDevice, None, None]:
        trait: dict[str, int | str] = {}
        if vid is not None:
            trait['idVendor'] = vid
        if pid is not None:
            trait['idProduct'] = pid
        if manufacture is not None:
            trait['manufacture'] = manufacture
        if product is not None:
            trait['product'] = product
        if serial_number is not None:
            trait['serial_number'] = serial_number

        def matcher(x: CandleDevice) -> bool:
            return all(getattr(x, k) == v for k, v in trait.items())

        # Is there an active device in use?
        for dev in cls.devices_ref:
            if matcher(dev):
                yield dev

        # Find and create device.
        for di in device_identifiers:
            udev = usb.core.find(
                backend=backend,
                idVendor=di.idVendor,
                idProduct=di.idProduct,
                custom_match=matcher
            )
            if udev is not None:
                try:
                    dev = CandleDevice(udev)
                except ValueError:
                    # A permission issue occurs when the device is occupied.
                    continue
                cls.devices_ref.add(dev)
                yield dev
