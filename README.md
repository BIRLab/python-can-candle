<div align="center">

# python-can-candle

![PyPI - Version](https://img.shields.io/pypi/v/python-can-candle)
![Python Version from PEP 621 TOML](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fchinaheyu%2Fpython-can-candle%2Fmain%2Fpyproject.toml)
![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/chinaheyu/python-can-candle/publish-to-pypi.yml)

</div>

Full featured CAN driver for Geschwister Schneider USB/CAN devices.

Support **Multichannel** and **CAN FD**.

## Installation

```shell
pip install python-can-candle
```

## Example

### Send and Receive

```python
import can
from candle import CandleBus

# Create a CandleBus instance.
with CandleBus(channel=0, fd=True, bitrate=1000000, data_bitrate=5000000) as bus:
    # Send normal can message without data.
    bus.send(can.Message(arbitration_id=1, is_extended_id=False))

    # Send normal can message with extended id
    bus.send(can.Message(arbitration_id=2, is_extended_id=True))

    # Send normal can message with data.
    bus.send(can.Message(arbitration_id=3, is_extended_id=False, data=[i for i in range(8)]))

    # Send can fd message.
    bus.send(can.Message(arbitration_id=4, is_extended_id=False, is_fd=True, bitrate_switch=True, error_state_indicator=True, data=[i for i in range(64)]))

    # Read messages from bus.
    for message in bus:
        print(message)
```

### Using with python-can

This library implements the [plugin interface](https://python-can.readthedocs.io/en/stable/plugin-interface.html) in [python-can](https://pypi.org/project/python-can/), aiming to replace the [gs_usb](https://python-can.readthedocs.io/en/stable/interfaces/gs_usb.html) interface within it.

```python
import can
from candle import CandleBus

# Create a CandleBus instance in the python-can API.
with can.Bus(interface='candle', channel=0, fd=True, bitrate=1000000, data_bitrate=5000000, ignore_config=True) as bus:
    # Bus is an instance of CandleBus.
    assert isinstance(bus, CandleBus)
```

Set `ignore_config=True` is recommended to prevent potential type casts. 

### Performance

The communication layer is implemented based on pybind11 with libusb. You can run the following script to evaluate the performance.

```shell
python -m candle.stress
```

## Reference

- [linux gs_usb driver](https://github.com/torvalds/linux/blob/master/drivers/net/can/usb/gs_usb.c)
- [python gs_usb driver](https://github.com/jxltom/gs_usb)
- [candleLight firmware](https://github.com/candle-usb/candleLight_fw)
- [candle_api](https://github.com/BIRLab/candle_api)