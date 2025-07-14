from typing import Optional, Tuple
import can
import candle_api as api


ISO_DLC = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)


class CandleBus(can.bus.BusABC):
    def __init__(self, channel: int, can_filters: Optional[can.typechecking.CanFilters] = None,
                 bitrate: int = 1000000, sample_point: float = 87.5,
                 data_bitrate: int = 5000000, data_sample_point: float = 87.5,
                 fd: bool = False, loop_back: bool = False, listen_only: bool = False,
                 triple_sample: bool = False, one_shot: bool = False, bit_error_reporting: bool = False,
                 termination: Optional[bool] = None, vid: Optional[int] = None, pid: Optional[int] = None,
                 manufacture: Optional[str] = None, product: Optional[str] = None,
                 serial_number: Optional[str] = None, **kwargs) -> None:

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

            self._device = dev
            break
        else:
            raise can.exceptions.CanInitializationError('Device not found!')

        # Open the device.
        self._device.open()

        # Get the channel.
        self._channel_number = channel
        self._channel = self._device[channel]
        self.channel_info = f'[{self._device.serial_number}]: channel {channel}'

        # Reset channel.
        self._channel.reset()

        # Set termination.
        if termination is not None:
            self._channel.set_termination(termination)

        # Set bit timing.
        props_seg = 1
        if self._channel.feature.fd:
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
        self._channel.start(
            hardware_timestamp=self._channel.feature.hardware_timestamp,
            fd=fd,
            loop_back=loop_back,
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
        if timeout is None:
            frame = self._channel.receive_nowait()
        else:
            try:
                frame = self._channel.receive(timeout)
            except TimeoutError:
                frame = None

        if frame is not None:
            msg = can.Message(
                timestamp=frame.timestamp,
                arbitration_id=frame.can_id,
                is_extended_id=frame.frame_type.extended_id,
                is_remote_frame=frame.frame_type.remote_frame,
                is_error_frame=frame.frame_type.error_frame,
                channel=self._channel_number,
                dlc=frame.size,   # https://github.com/hardbyte/python-can/issues/749
                data=bytearray(frame),
                is_fd=frame.frame_type.fd,
                is_rx=frame.frame_type.rx,
                bitrate_switch=frame.frame_type.bitrate_switch,
                error_state_indicator=frame.frame_type.error_state_indicator
            )
            return msg, False
        return None, False

    def send(self, msg: can.Message, timeout: Optional[float] = None) -> None:
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
            ISO_DLC.index(msg.dlc),
            msg.data
        )

        try:
            self._channel.send(frame, timeout)
        except TimeoutError as exc:
            raise can.CanOperationError("The message could not be sent") from exc

    def shutdown(self):
        self._channel.reset()
        super().shutdown()


__all__ = ['CandleBus']
