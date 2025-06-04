import candle_api
import threading
import time


def main() -> None:
    try:
        dev = candle_api.list_device()[0]
    except IndexError:
        print("No device found")
        return

    dev.open()
    ch = dev[0]
    ch.start(listen_only=True, loop_back=True, fd=ch.feature.fd)

    running = True

    def polling_cb() -> None:
        rx_cnt = 0
        tx_cnt = 0
        err_cnt = 0
        et = st = time.time()
        try:
            while running:
                et = time.time()
                frame = ch.receive(1)
                if frame.frame_type.error_frame:
                    err_cnt += 1
                else:
                    if frame.frame_type.rx:
                        rx_cnt += 1
                    else:
                        tx_cnt += 1
        except TimeoutError:
            pass
        print(f'tx: {tx_cnt}, rx {rx_cnt}, err: {err_cnt}, dt: {et - st} s')

    polling_thread = threading.Thread(target=polling_cb)
    polling_thread.start()

    for i in range(200000):
        ch.send(candle_api.CandleCanFrame(candle_api.CandleFrameType(), 0x123, 8, b'12345678'), 1)

    running = False
    polling_thread.join()

    dev.close()


if __name__ == '__main__':
    main()
