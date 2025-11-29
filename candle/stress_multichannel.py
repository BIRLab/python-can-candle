import can
import time
import random
import candle_api as api


class MultiChannelFrameCounter(can.Listener):
    def __init__(self, channel_count):
        super().__init__()
        self.rx_cnt = [0] * channel_count
        self.tx_cnt = [0] * channel_count
        self.err_cnt = [0] * channel_count
        self.st = time.time()

    def on_message_received(self, msg):
        print(f'\033[2K\r{msg}', end='', flush=True)
        if msg.is_error_frame:
            self.err_cnt[msg.channel] += 1
        elif msg.is_rx:
            self.rx_cnt[msg.channel] += 1
        else:
            self.tx_cnt[msg.channel] += 1

    def stop(self):
        print(f'\ntx: {self.tx_cnt}, rx {self.rx_cnt}, err: {self.err_cnt}, dt: {time.time() - self.st} s')


def main():
    # A hack to get the channel count.
    channel_count = api.list_device()[0].channel_count

    frame_counter = MultiChannelFrameCounter(channel_count)

    with can.Bus(interface='candle', channel=[i for i in range(channel_count)], listen_only=True, loop_back=True, ignore_config=True) as bus:
        notifier = can.Notifier(bus, [frame_counter])

        for i in range(200000):
            bus.send(can.Message(arbitration_id=random.randrange(0, 1 << 11), is_extended_id=False, channel=i % channel_count, data=random.randbytes(8)))

        notifier.stop()


if __name__ == '__main__':
    main()
