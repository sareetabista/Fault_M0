import numpy as np
import time
import chipwhisperer as cw


def reset_target(scope):
    scope.io.nrst = "low"
    time.sleep(0.2)
    scope.io.nrst = "high"
    time.sleep(1.0)


def u32(v):
    return np.array([np.uint32(v)], dtype="<u4").tobytes()


scope = cw.scope()
target = cw.target(scope, cw.targets.SimpleSerial)

try:
    scope.default_setup()

    scope.gain.db = 18
    scope.adc.samples = 5000
    scope.adc.offset = 0
    scope.adc.timeout = 2

    scope.clock.adc_src = "clkgen_x1"
    scope.io.hs2 = "clkgen"

    target.baud = 38400

    reset_target(scope)
    target.flush()

    # p command expects 4 bytes randomness
    rnd = u32(0x00000000)

    print("[+] Sending randomness p...")
    target.simpleserial_write("p", rnd)
    ack = target.simpleserial_wait_ack(timeout=1000)
    print("[+] ACK:", ack)

    # r command expects 16 bytes in the actual ELF
    pkt = (
        u32(0x01020304) +   # x share 0
        u32(0x05060708) +   # x share 1
        u32(0x090a0b0c) +   # y share 0
        u32(0x0d0e0f10)     # y share 1
    )

    print("[+] Packet length:", len(pkt))
    print("[+] Packet hex:", pkt.hex())

    print("[+] Arming scope...")
    scope.arm()

    print("[+] Sending run command r...")
    target.simpleserial_write("r", pkt)

    ret = scope.capture()

    if ret:
        print("[!] Capture timeout")
    else:
        print("[+] Capture successful")
        print("[+] Trigger count:", scope.adc.trig_count)

    response = target.simpleserial_read("r", 8, ack=True, timeout=1000)

    print("[+] Response:", response)

    if response is not None:
        print("[+] Output:", bytes(response).hex())

    trace = scope.get_last_trace()
    print("[+] Trace length:", len(trace))
    print("[+] First 10 samples:", trace[:10])

finally:
    target.dis()
    scope.dis()