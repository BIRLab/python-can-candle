import can
import time
import random


class FrameCounter(can.Listener):
    def __init__(self):
        super().__init__()
        self.rx_cnt = 0
        self.tx_cnt = 0
        self.err_cnt = 0
        self.st = time.time()

    def on_message_received(self, msg):
        print(f'\033[2K\r{msg}', end='', flush=True)
        if msg.is_error_frame:
            self.err_cnt += 1
        elif msg.is_rx:
            self.rx_cnt += 1
        else:
            self.tx_cnt += 1

    def stop(self):
        print(f'\ntx: {self.tx_cnt}, rx {self.rx_cnt}, err: {self.err_cnt}, dt: {time.time() - self.st} s')


def main():
    frame_counter = FrameCounter()

    with can.Bus(interface='candle', channel=0, listen_only=True, loop_back=True, ignore_config=True) as bus:
        notifier = can.Notifier(bus, [frame_counter])

        for i in range(200000):
            bus.send(can.Message(arbitration_id=random.randrange(0, 1 << 11), is_extended_id=False, data=random.randbytes(8)))

        notifier.stop()


if __name__ == '__main__':
    main()
