import time
from typing import Optional, Tuple, List, Union, TypedDict
from collections.abc import Sequence
import can
from can.util import len2dlc
from can.typechecking import CanFilters, AutoDetectedConfig
import candle_api as api


class ChannelConfig(TypedDict):
    bitrate: int
    sample_point: float
    data_bitrate: int
    data_sample_point: float
    fd: bool
    loop_back: bool
    listen_only: bool
    triple_sample: bool
    one_shot: bool
    bit_error_reporting: bool
    termination: Optional[bool]


def convert_frame(channel: int, frame: api.CandleCanFrame, hardware_timestamp: bool) -> can.Message:
    return can.Message(
        timestamp=frame.timestamp if hardware_timestamp else time.monotonic(),
        arbitration_id=frame.can_id,
        is_extended_id=frame.frame_type.extended_id,
        is_remote_frame=frame.frame_type.remote_frame,
        is_error_frame=frame.frame_type.error_frame,
        channel=channel,
        dlc=frame.size,  # https://github.com/hardbyte/python-can/issues/749
        data=bytearray(frame),
        is_fd=frame.frame_type.fd,
        is_rx=frame.frame_type.rx,
        bitrate_switch=frame.frame_type.bitrate_switch,
        error_state_indicator=frame.frame_type.error_state_indicator
    )


class CandleBus(can.bus.BusABC):
    def __init__(self, channel: Union[int, str, Sequence[int]], can_filters: Optional[CanFilters] = None,
                 bitrate: int = 1000000, sample_point: float = 87.5,
                 data_bitrate: int = 5000000, data_sample_point: float = 87.5,
                 fd: bool = False, loop_back: bool = False, listen_only: bool = False,
                 triple_sample: bool = False, one_shot: bool = False, bit_error_reporting: bool = False,
                 termination: Optional[bool] = None, vid: Optional[int] = None, pid: Optional[int] = None,
                 manufacture: Optional[str] = None, product: Optional[str] = None,
                 serial_number: Optional[str] = None, channel_configs: Optional[dict[int, ChannelConfig]] = None,
                 receive_own_messages: bool = False, **kwargs) -> None:

        # If ignore_config is not set, can.util.cast_from_string may cause unexpected type conversions.
        if manufacture is not None:
            manufacture = str(manufacture)
        if product is not None:
            product = str(product)
        if serial_number is not None:
            serial_number = str(serial_number)

        # Parse channel.
        if isinstance(channel, str):
            serial_number, channel_number = channel.split(':')
            self._channel_numbers = (int(channel_number),)
        elif isinstance(channel, int):
            self._channel_numbers = (channel,)
        elif isinstance(channel, Sequence):
            self._channel_numbers = tuple(channel)
        else:
            raise TypeError("Channel must be of type int, str or Sequence[int]")

        # Find the device.
        self._device = self._find_device(vid, pid, manufacture, product, serial_number)

        # Open the device.
        self._device.open()

        # Get the channel.
        self._channels = {i: self._device[i] for i in self._channel_numbers}
        self._hardware_timestamps = {i: self._channels[i].feature.hardware_timestamp for i in self._channel_numbers}
        self.channel_info = f'{self._device.serial_number}:{self._channel_numbers}'

        # Reset channel.
        [ch.reset() for ch in self._channels.values()]

        # Configure channel.
        default_config = ChannelConfig(
            bitrate=bitrate, sample_point=sample_point, data_bitrate=data_bitrate, data_sample_point=data_sample_point,
            fd=fd, loop_back=loop_back, listen_only=listen_only, triple_sample=triple_sample, one_shot=one_shot,
            bit_error_reporting=bit_error_reporting, termination=termination
        )

        for i, ch in self._channels.items():
            # Get channel configuration.
            cfg: ChannelConfig = default_config.copy()
            if channel_configs is not None:
                cfg |= channel_configs.get(i, {})

            # Set termination.
            if cfg["termination"] is not None:
                ch.set_termination(termination)

            # Set bit timing.
            props_seg = 1
            if cfg["fd"] and ch.feature.fd:
                bit_timing_fd = can.BitTimingFd.from_sample_point(
                    f_clock=ch.clock_frequency,
                    nom_bitrate=cfg["bitrate"],
                    nom_sample_point=cfg["sample_point"],
                    data_bitrate=cfg["data_bitrate"],
                    data_sample_point=cfg["data_sample_point"]
                )

                ch.set_bit_timing(
                    props_seg,
                    bit_timing_fd.nom_tseg1 - props_seg,
                    bit_timing_fd.nom_tseg2,
                    bit_timing_fd.nom_sjw,
                    bit_timing_fd.nom_brp
                )

                ch.set_data_bit_timing(
                    props_seg,
                    bit_timing_fd.data_tseg1 - props_seg,
                    bit_timing_fd.data_tseg2,
                    bit_timing_fd.data_sjw,
                    bit_timing_fd.data_brp
                )
            else:
                bit_timing = can.BitTiming.from_sample_point(
                    f_clock=ch.clock_frequency,
                    bitrate=cfg["bitrate"],
                    sample_point=cfg["sample_point"],
                )

                ch.set_bit_timing(
                    props_seg,
                    bit_timing.tseg1 - props_seg,
                    bit_timing.tseg2,
                    bit_timing.sjw,
                    bit_timing.brp
                )

            # Open the channel.
            ch.start(
                hardware_timestamp=self._hardware_timestamps[i],
                fd=cfg["fd"],
                loop_back=cfg["loop_back"],
                listen_only=cfg["listen_only"],
                triple_sample=cfg["triple_sample"],
                one_shot=cfg["one_shot"],
                bit_error_reporting=cfg["bit_error_reporting"]
            )

        # Receive own messages or not.
        self._receive_own_messages = receive_own_messages

        super().__init__(
            channel=channel,
            can_filters=can_filters,
            **kwargs,
        )

    @staticmethod
    def _find_device(vid: Optional[int] = None, pid: Optional[int] = None, manufacture: Optional[str] = None,
                     product: Optional[str] = None, serial_number: Optional[str] = None) -> api.CandleDevice:
        for dev in api.list_device():
            if vid is not None and dev.vendor_id != vid:
                continue
            if pid is not None and dev.product_id != pid:
                continue
            if manufacture is not None and dev.manufacturer != manufacture:
                continue
            if product is not None and dev.product != product:
                continue
            if serial_number is not None and dev.serial_number != serial_number:
                continue
            return dev
        else:
            raise can.exceptions.CanInitializationError('Device not found!')

    def _recv_internal(self, timeout: Optional[float]) -> Tuple[Optional[can.Message], bool]:
        # Check if there is a frame available.
        for i, ch in self._channels.items():
            frame = ch.receive_nowait()
            if frame is not None:
                if self._receive_own_messages or frame.frame_type.rx:
                    return convert_frame(i, frame, self._hardware_timestamps[i]), False

        polling_start = time.time()
        while True:
            # Calculate polling time
            if timeout is None:
                polling_time = 1.0
            else:
                polling_time = timeout - (time.time() - polling_start)

            # Timeout
            if polling_time < 0.0:
                return None, False

            # Block until a frame is available.
            if not self._device.wait_for_frame(polling_time):
                continue

            # Check if there is a frame available.
            for i, ch in self._channels.items():
                frame = ch.receive_nowait()
                if frame is not None:
                    if self._receive_own_messages or frame.frame_type.rx:
                        return convert_frame(i, frame, self._hardware_timestamps[i]), False

    def send(self, msg: can.Message, timeout: Optional[float] = None) -> None:
        # Parse channel.
        target_channels: Tuple[api.CandleChannel, ...]
        if msg.channel is None:
            target_channels = (self._channels[self._channel_numbers[0]],)
        elif isinstance(msg.channel, str):
            serial_number, channel_number = msg.channel.split(':')
            target_channels = (self._channels[int(channel_number)],)
        elif isinstance(msg.channel, int):
            target_channels = (self._channels[msg.channel],)
        elif isinstance(msg.channel, Sequence):
            target_channels = tuple(self._channels[i] for i in msg.channel)
        else:
            raise TypeError("Channel must be of type int, str or Sequence[int]")

        if timeout is None:
            timeout = 1.0

        frame = api.CandleCanFrame(
            api.CandleFrameType(
                rx=msg.is_rx,
                extended_id=msg.is_extended_id,
                remote_frame=msg.is_remote_frame,
                error_frame=msg.is_error_frame,
                fd=msg.is_fd,
                bitrate_switch=msg.bitrate_switch,
                error_state_indicator=msg.error_state_indicator
            ),
            msg.arbitration_id,
            len2dlc(msg.dlc),
            msg.data
        )

        try:
            for ch in target_channels:
                ch.send(frame, timeout)
        except TimeoutError as exc:
            raise can.CanOperationError("The message could not be sent") from exc

    def shutdown(self):
        [ch.reset() for ch in self._channels.values()]
        super().shutdown()

    @staticmethod
    def _detect_available_configs() -> List[AutoDetectedConfig]:
        return [AutoDetectedConfig(
            interface='candle',
            channel=f'{d.serial_number}:{i}'
        ) for d in api.list_device() for i in range(len(d))]


__all__ = ['CandleBus']
