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

### Using with python-can

This library implements the [plugin interface](https://python-can.readthedocs.io/en/stable/plugin-interface.html) in [python-can](https://pypi.org/project/python-can/), aiming to replace the [gs_usb](https://python-can.readthedocs.io/en/stable/interfaces/gs_usb.html) interface within it.

```python
import can
from candle import CandleBus

# Create a CandleBus instance in the python-can API.
with can.Bus(interface='candle', channel=0, ignore_config=True) as bus:
    # Bus is an instance of CandleBus.
    assert isinstance(bus, CandleBus)
```

Set `ignore_config=True` is recommended to prevent potential type casts.

### Configurations

You can configure the device by appending the following parameters when creating the `can.Bus`.

- bitrate: int, defaults to 1000000
- sample_point: float, defaults to 87.5
- data_bitrate: int, defaults to 5000000
- data_sample_point: float, defaults to 87.5
- fd: bool, defaults to False
- loop_back: bool, defaults to False
- listen_only: bool, defaults to False
- triple_sample: bool, defaults to False
- one_shot: bool, defaults to False
- bit_error_reporting: bool, defaults to False
- termination: bool or None, defaults to None

For example, create a canfd device with 1M bitrate and 5M data bitrate.

```python
with can.Bus(interface='candle', channel=0, fd=True, bitrate=1000000, data_bitrate=5000000, ignore_config=True) as bus:
    ...
```

### Connect multiple devices

When connecting multiple devices at the same time, you can set channel to `serial_number:channel` to create the specified `can.Bus`.

```python
with can.Bus(interface='candle', channel='208233AD5003:0', ignore_config=True) as bus:
    ...
```

You can also select devices by appending some additional parameters.

- vid: int, vendor ID
- pid: int, product ID
- manufacture: str, manufacture string
- product: str, product string
- serial_number: str, serial number

### Device Discovery

Detect all available channels.

```python
channels = can.detect_available_configs('candle')
print(channels)
```

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