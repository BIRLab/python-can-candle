<div align="center">

# python-can-candle

![PyPI - Version](https://img.shields.io/pypi/v/python-can-candle)
![Python Version from PEP 621 TOML](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fchinaheyu%2Fpython-can-candle%2Fmain%2Fpyproject.toml)
![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/chinaheyu/python-can-candle/publish-to-pypi.yml)

</div>

CAN driver for Geschwister Schneider USB/CAN devices.

## Feature

Support **multichannel** and **can fd**.

## Installation

```shell
pip install python-can-candle
```

If your system does not have any usb backend installed, you can borrow a libusb1 backend from [libusb](https://pypi.org/project/libusb/).

```shell
pip install python-can-candle[libusb]
```

We also provide a handy GUI tool, which you can install with the following command.

```shell
pip install python-can-candle[viewer]
```

Once installed you can launch it via `candle_viewer` or `python -m candle.candle_viewer` and you will see the following window.

![Candle Viewer](https://raw.githubusercontent.com/BIRLab/python-can-candle/main/assets/viewer.png)

(unstable) Installing a backend written in C can improve performance.

```shell
pip install "candle_api @ git+https://github.com/BIRLab/candle_api.git@main"
```

## Example

### Using with python-can (recommended)

This library implements the [plugin interface](https://python-can.readthedocs.io/en/stable/plugin-interface.html) in [python-can](https://pypi.org/project/python-can/), aiming to replace the [gs_usb](https://python-can.readthedocs.io/en/stable/interfaces/gs_usb.html) interface within it.

```python
import can
from candle import CandleBus

bus: CandleBus  # This line is added to provide type hints.

# Create a CandleBus instance in the python-can API.
with can.Bus(interface='candle', channel=0, fd=True, bitrate=1000000, data_bitrate=5000000) as bus:
    # Note that bus is an instance of CandleBus.
    assert isinstance(bus, CandleBus)

    # Send normal can message without data.
    bus.send(can.Message(arbitration_id=1, is_extended_id=False))

    # Send normal can message with extended id
    bus.send(can.Message(arbitration_id=2, is_extended_id=True))

    # Send normal can message with data.
    bus.send(can.Message(arbitration_id=3, is_extended_id=False, data=[i for i in range(8)]))

    # Send can fd message.
    if bus.is_fd_supported:
        bus.send(can.Message(arbitration_id=4, is_extended_id=False, is_fd=True, bitrate_switch=True,
                             error_state_indicator=True, data=[i for i in range(64)]))

    # Read messages from bus.
    for message in bus:
        print(message)
```

### Using candle-api directly

Using the API directly can be very cumbersome. However, we still provide a simple example for developers to refer to.

```python
from candle.candle_api import CandleDevice, GSHostFrame, GSHostFrameHeader, GSCANFlag, GSCANIDFlag

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

# Send a normal frame.
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

# Send a can fd frame with extended id.
channel.write(
    GSHostFrame(
        header=GSHostFrameHeader(
            echo_id=0,
            can_id=1 | GSCANIDFlag.EFF,
            can_dlc=15,
            channel=channel.index,
            flags=GSCANFlag.FD | GSCANFlag.BRS | GSCANFlag.ESI
        ),
        data=bytes([i for i in range(64)])
    )
)

try:
    while True:
        # Polling message.
        device.polling()
    
        # Receive a frame.
        frame = channel.read()
        if frame is not None:
            print(frame.data)
except KeyboardInterrupt:
    pass

# Close the channel.
channel.close()
```

## Reference

- [linux gs_usb driver](https://github.com/torvalds/linux/blob/master/drivers/net/can/usb/gs_usb.c)
- [python gs_usb driver](https://github.com/jxltom/gs_usb)
- [candleLight firmware](https://github.com/candle-usb/candleLight_fw)
