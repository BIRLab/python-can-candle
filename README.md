# python-can-candle

CAN driver for Geschwister Schneider USB/CAN devices.

## Feature

Support **multichannel** and **can fd**.

## Installation

```shell
pip install python-can-candle
```

## Example

### Using with python-can (recommended)

This library implements the [plugin interface](https://python-can.readthedocs.io/en/stable/plugin-interface.html) in [python-can](https://pypi.org/project/python-can/), aiming to replace the [gs_usb](https://python-can.readthedocs.io/en/stable/interfaces/gs_usb.html) interface within it.

```python
import can
from candle import CandleBus

bus: CandleBus  # This line is added to provide type hints.

# Create a CandleBus instance in the python-can API.
with can.Bus(interface='candle', channel=0) as bus:

    # Create listener and notifier.
    print_listener = can.Printer()
    notifier = can.Notifier(bus, [print_listener])

    # Send normal can message without data.
    bus.send(can.Message(arbitration_id=1, is_extended_id=False))
    
    # Send normal can message with extended id
    bus.send(can.Message(arbitration_id=2, is_extended_id=True))
    
    # Send normal can message with data.
    bus.send(can.Message(arbitration_id=3, is_extended_id=False, data=[i for i in range(8)]))

    # Send can fd message.
    if bus.is_fd_supported:
        bus.send(can.Message(arbitration_id=3, is_extended_id=False, is_fd=True, bitrate_switch=True, error_state_indicator=True, data=[i for i in range(64)]))

    # Stop notifier.
    notifier.stop()
```

### Using candle-api directly

Using the API directly can be very cumbersome. However, we still provide a simple example for developers to refer to.

```python
from candle.candle_api import CandleDevice, GSHostFrame, GSHostFrameHeader, GSCANFlag

# Scan available devices.
available_devices = list(CandleDevice.scan())

# Select a device.
for i, device in enumerate(available_devices):
    print(f'{i}: {device}')
device = available_devices[int(input('Select a device by index: '))]

# Select a interface.
# Only single interface devices are supported currently.
interface = device[0]

# Select a channel.
for i in range(len(interface)):
    print(f'{i}: channel {i}')
channel = interface[int(input(f'Select a channel by index: '))]

# Set bit timing.
# channel.set_bit_timing(...)
# channel.set_data_bit_timing(...)

# Open the channel.
channel.open(fd=channel.is_fd_supported)

# Send a frame.
channel.write(
    GSHostFrame(
        header=GSHostFrameHeader(
            echo_id=0,
            can_id=1,
            can_dlc=8,
            channel=channel.index,
            flags=GSCANFlag(0)
        ),
        data=bytes([i for i in range(8)])
    )
)

# Receive a frame.
frame = channel.read()
print(frame.data)

# Close the channel.
channel.close()
```

## Reference

https://github.com/torvalds/linux/blob/master/drivers/net/can/usb/gs_usb.c
