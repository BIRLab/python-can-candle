import can
import time
import random
import sys
from collections import defaultdict

class FrameCounter(can.Listener):
    """A can.Listener that counts frames received and sent, with per-channel stats."""
    def __init__(self):
        super().__init__()
        self.rx_cnt = defaultdict(int)
        self.tx_cnt = defaultdict(int)
        self.err_cnt = 0
        self.st = time.time()

    def on_message_received(self, msg: can.Message):
        print(f'\033[2K\r{msg}', end='', flush=True)
        if msg.is_error_frame:
            self.err_cnt += 1
            return

        ch = msg.channel
        if msg.is_rx:
            self.rx_cnt[ch] += 1
        else:
            # TX echoes are not always enabled, but we count them if they appear.
            # The main count comes from the sending side.
            self.tx_cnt[ch] += 1

    def stop(self):
        total_tx = sum(self.tx_cnt.values())
        total_rx = sum(self.rx_cnt.values())
        print(f'\ntx: {total_tx}, rx: {total_rx}, err: {self.err_cnt}, dt: {time.time() - self.st:.2f} s')
        if self.rx_cnt:
            print('RX counts per channel:')
            for ch, count in sorted(self.rx_cnt.items()):
                print(f'  - Channel {ch}: {count}')
        if self.tx_cnt:
            print('TX counts (from echoes) per channel:')
            for ch, count in sorted(self.tx_cnt.items()):
                print(f'  - Channel {ch}: {count}')


def run_stress_test(channels_to_test: list, num_messages: int = 10000):
    """Runs a stress test on a given list of channels."""
    frame_counter = FrameCounter()
    
    if not channels_to_test:
        print("No channels provided for stress test.")
        return

    try:
        # Note: `can.Bus` will select the `CandleBus` based on `interface='candle'`.
        # The modified `CandleBus` accepts a list of channels.
        # Loopback is enabled to receive the frames we send.
        with can.Bus(interface='candle', channel=channels_to_test, fd=True, loop_back=True, bitrate=1000000) as bus:
            notifier = can.Notifier(bus, [frame_counter])
            
            # Extract integer channel numbers for sending
            channel_numbers = [int(ch.split(':')[1]) for ch in channels_to_test]

            print(f"Sending {num_messages} messages across {len(channel_numbers)} channels...")
            for i in range(num_messages):
                # Round-robin send to each channel
                target_channel = channel_numbers[i % len(channel_numbers)]
                msg = can.Message(
                    arbitration_id=random.randrange(0, 1 << 11),
                    is_extended_id=False,
                    data=random.randbytes(random.randint(1, 8)),
                    is_fd=True,
                    channel=target_channel  # Set target channel for CandleBus to route it
                )
                bus.send(msg)

            print("\nWaiting for messages to be received...")
            time.sleep(2)  # Give some time for all messages to be received via loopback
            notifier.stop()
            frame_counter.stop()
            
            # Verification
            total_sent = num_messages
            total_received = sum(frame_counter.rx_cnt.values())
            if total_received >= total_sent:
                print(f"PASS: Sent {total_sent} and received {total_received} messages.")
            else:
                print(f"FAIL: Sent {total_sent} but only received {total_received} messages.")

    except Exception as e:
        print(f"FAIL: An error occurred during the stress test: {e}")


def main():
    """
    Detects available candle devices and runs multi-channel stress tests.
    """
    print("Detecting available candle devices...")
    try:
        # Use the static method from CandleBus to find channels
        available_configs = can.detect_available_configs(interfaces='candle')
    except Exception as e:
        print(f"Could not detect candle channels: {e}")
        sys.exit(1)

    if not available_configs:
        print("No candle devices found.")
        sys.exit(0)

    channels_by_device = defaultdict(list)
    for config in available_configs:
        serial, _ = config['channel'].split(':')
        channels_by_device[serial].append(config['channel'])

    print("Found candle devices and channels:")
    for serial, channels in channels_by_device.items():
        print(f"  - Device {serial}: Channels {[ch.split(':')[1] for ch in channels]}")

    # --- Test 1: Single device with multiple channels ---
    print("\n--- Test 1: Single device, multiple channels ---")
    tested_single_multi = False
    for serial, channels in channels_by_device.items():
        if len(channels) > 1:
            print(f"\n=> Testing device {serial} with all its channels: {channels}")
            run_stress_test(channels)
            tested_single_multi = True
            break  # Only test the first one found
    if not tested_single_multi:
        print("Skipped: No single device with multiple channels found.")

    # --- Test 2: All channels of each device sequentially ---
    print("\n--- Test 2: Each device's channels tested individually ---")
    if len(channels_by_device) > 1:
        for serial, channels in channels_by_device.items():
            print(f"\n=> Testing device {serial} with its channels: {channels}")
            run_stress_test(channels)
    else:
        print("Skipped: Fewer than two devices found, or already tested above.")

    # --- Test 3: Invalid multi-device configuration ---
    print("\n--- Test 3: Invalid multi-device configuration ---")
    if len(channels_by_device) > 1:
        # Take one channel from the first two devices
        channels_from_different_devices = [
            list(channels_by_device.values())[0][0],
            list(channels_by_device.values())[1][0]
        ]
        print(f"\n=> Attempting to initialize bus with channels from different devices: {channels_from_different_devices}")
        try:
            with can.Bus(interface='candle', channel=channels_from_different_devices):
                print("FAIL: can.CanInitializationError was NOT raised when it should have been.")
        except can.CanInitializationError as e:
            print(f"PASS: Caught expected error: {e}")
        except Exception as e:
            print(f"FAIL: Caught unexpected error: {e}")
    else:
        print("Skipped: Fewer than two devices found.")


if __name__ == '__main__':
    main()
