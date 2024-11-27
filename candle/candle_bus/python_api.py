from typing import Optional, Any, TYPE_CHECKING, Tuple
from candle.candle_api import CandleDevice, CandleChannel, GSHostFrame, GSHostFrameHeader, GSDeviceState, GSCANFlag
import can


class CandleBus(can.bus.BusABC):

    # Type hints for wrapped attributes.
    if TYPE_CHECKING:
        index: int
        is_fd_supported: bool
        is_listen_only_supported: bool
        is_loop_back_supported: bool
        is_triple_sample_supported: bool
        is_one_shot_supported: bool
        is_hardware_timestamp_supported: bool
        is_bit_error_reporting_supported: bool
        is_get_state_supported: bool
        is_termination_supported: bool
        is_quirk: bool
        clock_frequency: int
        tseg1_min: int
        tseg1_max: int
        tseg2_min: int
        tseg2_max: int
        sjw_max: int
        brp_min: int
        brp_max: int
        brp_inc: int
        dtseg1_min: int
        dtseg1_max: int
        dtseg2_min: int
        dtseg2_max: int
        dsjw_max: int
        dbrp_min: int
        dbrp_max: int
        dbrp_inc: int
        reconfigure = CandleChannel.reconfigure
        set_bit_timing = CandleChannel.set_bit_timing
        set_data_bit_timing = CandleChannel.set_data_bit_timing
        termination: bool
        software_version: int
        hardware_version: int

    def __init__(self, channel: int, can_filters: Optional[can.typechecking.CanFilters] = None,
                 bitrate: int = 1000000, sample_point: float = 87.5,
                 data_bitrate: int = 5000000, data_sample_point: float = 87.5,
                 fd: bool = False, loop_back: bool = False, listen_only: bool = False,
                 triple_sample: bool = False, one_shot: bool = False, bit_error_reporting: bool = False,
                 termination: Optional[bool] = None, vid: Optional[int] = None, pid: Optional[int] = None,
                 manufacture: Optional[str] = None, product: Optional[str] = None,
                 serial_number: Optional[str] = None, **kwargs) -> None:
        try:
            self._device = next(CandleDevice.scan(vid, pid, manufacture, product, serial_number))
        except StopIteration:
            raise can.exceptions.CanInitializationError('Device not found!')

        # Get the channel.
        self._channel = self._device[0][channel]
        self.channel_info = f'[{self._device}]: channel {self._channel.index}'

        # Reset channel.
        self._channel.close()

        # Set termination.
        if termination is not None:
            self._channel.termination = termination

        # Set bit timing.
        props_seg = 1
        if self._channel.is_fd_supported:
            bit_timing_fd = can.BitTimingFd.from_sample_point(
                f_clock=self._channel.clock_frequency,
                nom_bitrate=bitrate,
                nom_sample_point=sample_point,
                data_bitrate=data_bitrate,
                data_sample_point=data_sample_point
            )

            self._channel.set_bit_timing(
                props_seg,
                bit_timing_fd.nom_tseg1 - props_seg,
                bit_timing_fd.nom_tseg2,
                bit_timing_fd.nom_sjw,
                bit_timing_fd.nom_brp
            )

            self._channel.set_data_bit_timing(
                props_seg,
                bit_timing_fd.data_tseg1 - props_seg,
                bit_timing_fd.data_tseg2,
                bit_timing_fd.data_sjw,
                bit_timing_fd.data_brp
            )
        else:
            bit_timing = can.BitTiming.from_sample_point(
                f_clock=self._channel.clock_frequency,
                bitrate=bitrate,
                sample_point=sample_point,
            )

            self._channel.set_bit_timing(
                props_seg,
                bit_timing.tseg1 - props_seg,
                bit_timing.tseg2,
                bit_timing.sjw,
                bit_timing.brp
            )

        # Open the channel.
        self._channel.open(
            fd=fd,
            loopback=loop_back,
            listen_only=listen_only,
            triple_sample=triple_sample,
            one_shot=one_shot,
            bit_error_reporting=bit_error_reporting
        )

        super().__init__(
            channel=channel,
            can_filters=can_filters,
            **kwargs,
        )

    def _recv_internal(
        self, timeout: Optional[float]
    ) -> Tuple[Optional[can.Message], bool]:
        # Do not set timeout as None or zero here to avoid blocking.
        timeout_ms = round(timeout * 1000) if timeout else 1

        frame = self._channel.read()

        if frame is None:
            self._device.polling(timeout_ms)
            frame = self._channel.read()

        if frame is not None:
            msg = can.Message(
                timestamp=frame.timestamp,
                arbitration_id=frame.header.arbitration_id,
                is_extended_id=frame.header.is_extended_id,
                is_remote_frame=frame.header.is_remote_frame,
                is_error_frame=frame.header.is_error_frame,
                channel=frame.header.channel,
                dlc=frame.header.data_length,   # https://github.com/hardbyte/python-can/issues/749
                data=frame.data,
                is_fd=frame.header.is_fd,
                is_rx=frame.header.is_rx,
                bitrate_switch=frame.header.is_bitrate_switch,
                error_state_indicator=frame.header.is_error_state_indicator
            )
            return msg, False
        return None, False

    def send(self, msg: can.Message, timeout: Optional[float] = None) -> None:
        if not timeout:
            # PyUSB default timeout.
            timeout = 1

        hfh = GSHostFrameHeader(0, msg.arbitration_id, 0, self._channel.index, GSCANFlag(0))

        hfh.data_length = msg.dlc   # https://github.com/hardbyte/python-can/issues/749

        hfh.is_extended_id = msg.is_extended_id
        hfh.is_remote_frame = msg.is_remote_frame
        hfh.is_error_frame = msg.is_error_frame
        hfh.is_fd = msg.is_fd
        hfh.is_bitrate_switch = msg.bitrate_switch
        hfh.is_error_state_indicator = msg.error_state_indicator

        try:
            self._channel.write(GSHostFrame(hfh, msg.data, round(msg.timestamp * 1e6)), round(timeout * 1000))
        except TimeoutError as exc:
            raise can.CanOperationError("The message could not be sent") from exc

    def shutdown(self):
        self._channel.close()
        super().shutdown()

    @property
    def protocol(self) -> can.CanProtocol:
        if self._channel.is_fd_supported:
            return can.CanProtocol.CAN_FD
        return can.CanProtocol.CAN_20

    @property
    def device_state(self) -> GSDeviceState:
        return self._channel.state

    def __getattr__(self, attr: str) -> Any:
        # Hide protected or private method.
        if attr.startswith('_'):
            raise AttributeError(attr)

        # Hide some internal channel method.
        if attr in {'close', 'open', 'read', 'write'}:
            raise AttributeError(attr)

        # Wrap attribute from CandleChannel.
        return getattr(self._channel, attr)
