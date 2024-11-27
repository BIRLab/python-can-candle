try:
    from candle.candle_bus.c_api import CandleBus
except ImportError:
    from candle.candle_bus.python_api import CandleBus
