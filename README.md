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

### Open multiple channels of a single device

This driver now supports opening multiple channels from the same device in a single `CandleBus` instance.

- Pass a list of channels belonging to the same device, e.g. `['SERIAL:0', 'SERIAL:1']`.
- Alternatively, pass a list of indices with `serial_number=SERIAL`.
- A single instance must NOT mix channels from different devices.

```python
import can
from candle import CandleBus

# Option A: list of "serial:idx" strings
bus = CandleBus(channel=['208233AD5003:0', '208233AD5003:1'], fd=True, bitrate=1000000, data_bitrate=5000000, loop_back=True)

# Option B: list of indices with explicit serial number
bus = CandleBus(channel=[0, 1], serial_number='208233AD5003', fd=True, bitrate=1000000, data_bitrate=5000000, loop_back=True)

# Send to a specific channel by setting msg.channel
m0 = can.Message(arbitration_id=0x100, data=b'\x00'*8, is_fd=True)
m0.channel = 0
bus.send(m0)

m1 = can.Message(arbitration_id=0x101, data=b'\x01'*8, is_fd=True)
m1.channel = 1
bus.send(m1)

# Receive frames: msg.channel indicates the source channel
rx = bus.recv(timeout=0.5)
print(rx.channel, hex(rx.arbitration_id))

bus.shutdown()
```

### Notes on multi-channel behavior

- A single `CandleBus` instance manages multiple channels of one device using one device handle.
- `send()` routes frames to the target channel based on `msg.channel` (int, `"SERIAL:idx"`, or `"idx"`).
- `recv()` returns `Message.channel` set to the source channel number.
- When `msg.channel` is not set, `send()` defaults to the first managed channel.
- To test multiple devices simultaneously, create one `CandleBus` per device.

### Backward compatibility

- Existing single-channel usage is unchanged; `channel=0` or `channel='SERIAL:0'` still works.
- `can.detect_available_configs('candle')` continues to report channels as `serial:idx` strings.
- The stress test `python -m candle.stress` (single-channel) remains compatible.

### Performance

The communication layer is implemented based on pybind11 with libusb. You can run the following scripts to evaluate the performance.

For single-channel performance:
```shell
python -m candle.stress
```

For multi-channel and multi-device performance and correctness verification:
```shell
python -m candle.stress_multichannel
```

## Reference

- [linux gs_usb driver](https://github.com/torvalds/linux/blob/master/drivers/net/can/usb/gs_usb.c)
- [python gs_usb driver](https://github.com/jxltom/gs_usb)
- [candleLight firmware](https://github.com/candle-usb/candleLight_fw)
- [candle_api](https://github.com/BIRLab/candle_api)
