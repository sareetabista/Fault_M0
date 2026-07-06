import numpy as np
import time
import csv
from collections import Counter
import chipwhisperer as cw



N_RUNS = 1000

WIDTH = -10
OFFSET = 14
EXT_OFFSET = 64
REPEAT = 1

RESULT_FILE = f"repeat_1000_w{WIDTH}_off{OFFSET}_ext_{EXT_OFFSET}_00010101.csv"

def reset_target(scope):
    scope.io.nrst = "low"
    time.sleep(0.2)
    scope.io.nrst = "high"
    time.sleep(1.0)


def u32(v):
    return np.array([np.uint32(v)], dtype="<u4").tobytes()


def setup_scope(scope):
    scope.default_setup()

    scope.gain.db = 18
    scope.adc.samples = 5000
    scope.adc.offset = 0
    scope.adc.timeout = 2

    scope.clock.adc_src = "clkgen_x1"
    scope.trigger.triggers = "tio4"

    scope.glitch.clk_src = "clkgen"
    scope.glitch.output = "clock_xor"
    scope.glitch.trigger_src = "ext_single"

    scope.glitch.width = WIDTH
    scope.glitch.offset = OFFSET
    scope.glitch.ext_offset = EXT_OFFSET
    scope.glitch.repeat = REPEAT


def classify_fault(clean_hex, output_hex):
    clean_bytes = bytes.fromhex(clean_hex)
    output_bytes = bytes.fromhex(output_hex)

    positions = []
    for i, (c, o) in enumerate(zip(clean_bytes, output_bytes)):
        if c != o:
            positions.append(i)

    return len(positions), positions


def run_once(scope, target, rnd, pkt):
    target.simpleserial_write("p", rnd)
    ack = target.simpleserial_wait_ack(timeout=500)

    if ack != 0:
        reset_target(scope)
        target.flush()
        return "p_ack_failed", ""

    scope.arm()
    target.simpleserial_write("r", pkt)

    ret = scope.capture()

    if ret:
        reset_target(scope)
        target.flush()
        return "capture_timeout", ""

    response = target.simpleserial_read("r", 8, ack=True, timeout=500)

    if response is None:
        reset_target(scope)
        target.flush()
        return "no_response", ""

    return "response", bytes(response).hex()


def measure_clean_output(scope, target, rnd, pkt, trials=5):
    print("[+] Measuring clean output first...")

    scope.io.hs2 = "clkgen"
    reset_target(scope)
    target.flush()

    outputs = []

    for i in range(trials):
        status, output_hex = run_once(scope, target, rnd, pkt)

        if status != "response":
            raise RuntimeError(f"Clean run failed at trial {i}: {status}")

        outputs.append(output_hex)
        print(f"[+] Clean trial {i}: {output_hex}")

    counts = Counter(outputs)

    if len(counts) != 1:
        print("[!] Clean output not stable:")
        for output, count in counts.items():
            print(count, output)
        raise RuntimeError("Clean output changed across trials. Stop here.")

    clean_output = outputs[0]
    print("[+] Final clean output:", clean_output)
    return clean_output


scope = cw.scope()
target = cw.target(scope, cw.targets.SimpleSerial)

try:
    setup_scope(scope)
    target.baud = 38400

    rnd = u32(0x00010101)

    pkt = (
        u32(0x01020304) +
        u32(0x05060708) +
        u32(0x090a0b0c) +
        u32(0x0d0e0f10)
    )

    print("[+] Packet length:", len(pkt))
    print("[+] Packet hex:", pkt.hex())
    print("[+] Randomness:", rnd.hex())

    clean_output = measure_clean_output(scope, target, rnd, pkt, trials=5)

    print("[+] Switching to glitch clock...")
    scope.io.hs2 = "glitch"
    reset_target(scope)
    target.flush()

    print("[+] Glitch setting:")
    print(f"    width      = {WIDTH}")
    print(f"    offset     = {OFFSET}")
    print(f"    ext_offset = {EXT_OFFSET}")
    print(f"    repeat     = {REPEAT}")
    print(f"[+] Running {N_RUNS} trials...")

    status_counter = Counter()
    output_counter = Counter()
    position_counter = Counter()

    with open(RESULT_FILE, "w", newline="") as f:
        writer = csv.writer(f)

        writer.writerow([
            "trial",
            "width",
            "offset",
            "ext_offset",
            "repeat",
            "status",
            "clean_output",
            "output",
            "fault_byte_count",
            "fault_byte_positions_0_indexed"
        ])

        for trial in range(1, N_RUNS + 1):
            status, output_hex = run_once(scope, target, rnd, pkt)

            fault_byte_count = 0
            fault_positions = []

            if status == "response":
                if output_hex == clean_output:
                    final_status = "normal"
                else:
                    final_status = "fault"
                    fault_byte_count, fault_positions = classify_fault(
                        clean_output,
                        output_hex
                    )

                    positions_str = ";".join(str(p) for p in fault_positions)

                    print(
                        f"FAULT trial={trial:04d} | "
                        f"out={output_hex} | "
                        f"bytes={fault_byte_count} | "
                        f"positions={positions_str}"
                    )

                    output_counter[output_hex] += 1
                    position_counter[positions_str] += 1
            else:
                final_status = status

                print(
                    f"{final_status.upper()} trial={trial:04d}"
                )

            positions_str = ";".join(str(p) for p in fault_positions)

            status_counter[final_status] += 1

            writer.writerow([
                trial,
                WIDTH,
                OFFSET,
                EXT_OFFSET,
                REPEAT,
                final_status,
                clean_output,
                output_hex,
                fault_byte_count,
                positions_str
            ])

            if trial % 100 == 0:
                print(f"[+] Completed {trial}/{N_RUNS}")

    print("\n========== SUMMARY ==========")
    print("[+] Clean output:", clean_output)
    print("[+] Setting:")
    print(f"    width      = {WIDTH}")
    print(f"    offset     = {OFFSET}")
    print(f"    ext_offset = {EXT_OFFSET}")
    print(f"    repeat     = {REPEAT}")

    print("\n[+] Status counts:")
    for status, count in status_counter.most_common():
        rate = 100.0 * count / N_RUNS
        print(f"    {status:>16}: {count:>5} / {N_RUNS} = {rate:.2f}%")

    print("\n[+] Top faulty outputs:")
    for output, count in output_counter.most_common(10):
        rate = 100.0 * count / N_RUNS
        print(f"    {count:>5}x ({rate:>6.2f}%)  {output}")

    print("\n[+] Top fault byte positions:")
    for positions, count in position_counter.most_common(10):
        rate = 100.0 * count / N_RUNS
        print(f"    {count:>5}x ({rate:>6.2f}%)  bytes [{positions}]")

    print("\n[+] Results saved to:", RESULT_FILE)

finally:
    target.dis()
    scope.dis()