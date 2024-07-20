import time
import can
from candle.candle_bus import CandleBus

# usb backend
import usb.backend.libusb1
from libusb._platform import DLL_PATH
usb.backend.libusb1.get_backend(find_library=lambda x: DLL_PATH)


bus: CandleBus
with can.interface.Bus(interface='candle', channel=0, bitrate=1000000) as bus:
    can.Notifier(bus, [can.Printer()])

    while True:
        msg = can.Message(
            arbitration_id=0x123,
            data=bytes([0, 1, 2, 3, 4, 5, 6, 7]),
            is_extended_id=False
        )

        bus.send(msg)

        time.sleep(0.5)
