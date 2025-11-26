from typing import Optional, Tuple, List, Union
import can
from can.typechecking import CanFilters, AutoDetectedConfig
import candle_api as api
import time

ISO_DLC = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)


class CandleBus(can.bus.BusABC):
    def __init__(self, channel: Union[int, str, List[Union[int, str]]], can_filters: Optional[CanFilters] = None,
                 bitrate: int = 1000000, sample_point: float = 87.5,
                 data_bitrate: int = 5000000, data_sample_point: float = 87.5,
                 fd: bool = False, loop_back: bool = False, listen_only: bool = False,
                 triple_sample: bool = False, one_shot: bool = False, bit_error_reporting: bool = False,
                 termination: Optional[bool] = None, vid: Optional[int] = None, pid: Optional[int] = None,
                 manufacture: Optional[str] = None, product: Optional[str] = None,
                 serial_number: Optional[str] = None, **kwargs) -> None:

        # Extended: allow `channel` to be a list and enforce single-device consistency.
        # Upstream accepted only str/int and opened a single channel per CandleBus instance.

        if manufacture is not None:
            manufacture = str(manufacture)
        if product is not None:
            product = str(product)
        if serial_number is not None:
            serial_number = str(serial_number)

        managed_serial = serial_number
        self._channel_numbers: List[int] = []

        if isinstance(channel, list):
            # New: Parse and validate channels for a single device, record their indices.
            for ch in channel:
                if isinstance(ch, str):
                    sn, chn = ch.split(':')
                    if managed_serial is None:
                        managed_serial = sn
                    elif sn != managed_serial:
                        raise can.exceptions.CanInitializationError('Channels belong to different devices')
                    self._channel_numbers.append(int(chn))
                elif isinstance(ch, int):
                    # New: When using integer channel indices, a serial_number must be provided.
                    if managed_serial is None:
                        raise TypeError("serial_number required when channel list contains int")
                    self._channel_numbers.append(ch)
                else:
                    raise TypeError("channel list items must be str or int")
            self._channel_number = self._channel_numbers[0]
        elif isinstance(channel, str):
            managed_serial, chn = channel.split(':')
            self._channel_number = int(chn)
            self._channel_numbers = [self._channel_number]
        elif isinstance(channel, int):
            self._channel_number = channel
            self._channel_numbers = [self._channel_number]
        else:
            raise TypeError("channel must be of type str, int or list")

        self._device = self._find_device(vid, pid, manufacture, product, managed_serial)
        self._device.open()

        # New: Manage a list of channels from the same device using a single device handle.
        self._channels = [self._device[i] for i in self._channel_numbers]
        if len(self._channel_numbers) == 1:
            self.channel_info = f'{self._device.serial_number}:{self._channel_numbers[0]}'
        else:
            # New: Report multiple channels as a comma-separated string for visibility.
            self.channel_info = ','.join([f'{self._device.serial_number}:{i}' for i in self._channel_numbers])
        self._channel = self._channels[0]
        # New: Round-robin index for fair polling among channels.
        self._round_robin_index = 0

        for ch in self._channels:
            ch.reset()

        if termination is not None:
            for ch in self._channels:
                ch.set_termination(termination)

        props_seg = 1

        def get_cfg(param, idx, default):
            if isinstance(param, (list, tuple)):
                return param[idx] if idx < len(param) else default
            return param

        if fd:
            for i, ch in enumerate(self._channels):
                # Resolve per-channel configs
                br = get_cfg(bitrate, i, 1000000)
                sp = get_cfg(sample_point, i, 87.5)
                dbr = get_cfg(data_bitrate, i, 5000000)
                dsp = get_cfg(data_sample_point, i, 87.5)

                if ch.feature.fd:
                    # New: Configure FD bit timing per channel when supported.
                    bit_timing_fd = can.BitTimingFd.from_sample_point(
                        f_clock=ch.clock_frequency,
                        nom_bitrate=br,
                        nom_sample_point=sp,
                        data_bitrate=dbr,
                        data_sample_point=dsp
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
                    # New: Fallback to classic CAN bit timing for channels without FD.
                    bit_timing = can.BitTiming.from_sample_point(
                        f_clock=ch.clock_frequency,
                        bitrate=br,
                        sample_point=sp,
                    )
                    ch.set_bit_timing(
                        props_seg,
                        bit_timing.tseg1 - props_seg,
                        bit_timing.tseg2,
                        bit_timing.sjw,
                        bit_timing.brp
                    )
        else:
            for i, ch in enumerate(self._channels):
                # Resolve per-channel configs
                br = get_cfg(bitrate, i, 1000000)
                sp = get_cfg(sample_point, i, 87.5)

                # New: Classic CAN bit timing configuration per channel.
                bit_timing = can.BitTiming.from_sample_point(
                    f_clock=ch.clock_frequency,
                    bitrate=br,
                    sample_point=sp,
                )
                ch.set_bit_timing(
                    props_seg,
                    bit_timing.tseg1 - props_seg,
                    bit_timing.tseg2,
                    bit_timing.sjw,
                    bit_timing.brp
                )

        for ch in self._channels:
            # New: Start every managed channel. Upstream started a single channel.
            ch.start(
                hardware_timestamp=ch.feature.hardware_timestamp,
                fd=fd,
                loop_back=loop_back,
                listen_only=listen_only,
                triple_sample=triple_sample,
                one_shot=one_shot,
                bit_error_reporting=bit_error_reporting
            )

        # New: Pass a combined channel_info string for visibility in upper layers.
        super().__init__(
            channel=self.channel_info,
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

    def _recv_internal(
            self, timeout: Optional[float]
    ) -> Tuple[Optional[can.Message], bool]:
        """
        New: Round-robin polling across all managed channels.
        - Non-blocking: try each channel via receive_nowait until a frame emerges.
        - Blocking: iterate within timeout window, sleeping briefly between scans.
        - When a frame is read, set Message.channel to the source channel number.
        """
        frame = None
        src_idx = None

        if timeout is None:
            for n in range(len(self._channels)):
                i = (self._round_robin_index + n) % len(self._channels)
                f = self._channels[i].receive_nowait()
                if f is not None:
                    frame = f
                    src_idx = i
                    self._round_robin_index = (i + 1) % len(self._channels)
                    break
        else:
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                for n in range(len(self._channels)):
                    i = (self._round_robin_index + n) % len(self._channels)
                    try:
                        f = self._channels[i].receive_nowait()
                    except Exception:
                        f = None
                    if f is not None:
                        frame = f
                        src_idx = i
                        self._round_robin_index = (i + 1) % len(self._channels)
                        break
                if frame is not None:
                    break
                time.sleep(0.001)

        if frame is not None:
            msg = can.Message(
                timestamp=frame.timestamp,
                arbitration_id=frame.can_id,
                is_extended_id=frame.frame_type.extended_id,
                is_remote_frame=frame.frame_type.remote_frame,
                is_error_frame=frame.frame_type.error_frame,
                # New: source channel attribution for upper layers.
                channel=self._channel_numbers[src_idx],
                dlc=frame.size,
                data=bytearray(frame),
                is_fd=frame.frame_type.fd,
                is_rx=frame.frame_type.rx,
                bitrate_switch=frame.frame_type.bitrate_switch,
                error_state_indicator=frame.frame_type.error_state_indicator
            )
            return msg, False
        return None, False

    def send(self, msg: can.Message, timeout: Optional[float] = None) -> None:
        """
        New: Route outgoing frames to a specific channel.
        - If msg.channel is an int, use that channel number.
        - If msg.channel is a string ("SERIAL:idx" or "idx"), parse to an int.
        - If msg.channel is None, default to the first managed channel.
        - Raise CanOperationError if target channel is not managed by this instance.
        """
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

        idx = 0
        target = msg.channel
        if target is not None:
            if isinstance(target, int):
                if target in self._channel_numbers:
                    idx = self._channel_numbers.index(target)
                else:
                    raise can.CanOperationError("Target channel not managed")
            elif isinstance(target, str):
                if ':' in target:
                    _, chn = target.split(':')
                    chn_int = int(chn)
                else:
                    try:
                        chn_int = int(target)
                    except ValueError:
                        chn_int = None
                if chn_int is not None and chn_int in self._channel_numbers:
                    idx = self._channel_numbers.index(chn_int)
                else:
                    raise can.CanOperationError("Target channel not managed")

        try:
            self._channels[idx].send(frame, timeout)
        except TimeoutError as exc:
            raise can.CanOperationError("The message could not be sent") from exc

    def shutdown(self):
        """
        New: Reset all managed channels for clean shutdown.
        Upstream reset a single channel only.
        """
        for ch in getattr(self, "_channels", []):
            ch.reset()
        super().shutdown()

    @staticmethod
    def _detect_available_configs() -> List[AutoDetectedConfig]:
        return [AutoDetectedConfig(
            interface='candle',
            channel=f'{d.serial_number}:{i}'
        ) for d in api.list_device() for i in range(len(d))]


__all__ = ['CandleBus']
"""
Multi-channel CandleBus extension for python-can-candle.

Differences from the upstream single-channel implementation:
- Accepts a list of channels for a single device (e.g., ["SERIAL:0", "SERIAL:1"]).
- Opens the device once and manages multiple hardware channels via `self._channels`.
- Configures bit timing and starts each channel individually.
- Implements round-robin polling across all channels in `_recv_internal` and
  sets `Message.channel` to the source channel number.
- Routes `send()` to the target channel based on `msg.channel` (int or "SERIAL:idx").
- Ensures a single `CandleBus` instance only manages channels from the same device.
- Resets all managed channels during `shutdown()` for clean release.

Design rationale:
- Windows/libusb backends typically expose a single device handle that owns all
  endpoints; opening multiple per-device handles concurrently can fail.
- Managing multiple channels under one handle allows parallel traffic on distinct
  hardware channels without resource conflicts, while keeping API compatibility.
"""
