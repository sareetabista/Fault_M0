import numpy as np
import time
import csv
import random
from collections import Counter
import chipwhisperer as cw



N_RUNS = 1000
WIDTH = -10
OFFSET = 14
EXT_OFFSET = 64
REPEAT = 5
RESULT_FILE = f"repeat_1000_randomio_w{WIDTH}_off{OFFSET}_ext_{EXT_OFFSET}_00010101.csv"
# Keep randomness fixed unless you want to test random masks too.
# Change this to True if you want new randomness every trial also.
RANDOMIZE_RANDOMNESS = False


def reset_target(scope):
    scope.io.nrst = "low"
    time.sleep(0.2)
    scope.io.nrst = "high"
    time.sleep(1.0)


def u32(v):
    return np.array([np.uint32(v)], dtype="<u4").tobytes()


def rand_u32():
    return random.getrandbits(32)


def make_random_packet():
    """
    Makes 16 random bytes as four 32-bit little-endian words.

    Input packet structure:
        word0 + word1 + word2 + word3
    """
    w0 = rand_u32()
    w1 = rand_u32()
    w2 = rand_u32()
    w3 = rand_u32()

    pkt = u32(w0) + u32(w1) + u32(w2) + u32(w3)

    return pkt, (w0, w1, w2, w3)


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


def classify_fault(expected_hex, output_hex):
    expected_bytes = bytes.fromhex(expected_hex)
    output_bytes = bytes.fromhex(output_hex)

    positions = []
    xor_diffs = []

    for i, (e, o) in enumerate(zip(expected_bytes, output_bytes)):
        if e != o:
            positions.append(i)
            xor_diffs.append(e ^ o)

    return len(positions), positions, xor_diffs


def run_once(scope, target, rnd, pkt):
    """
    Sends randomness, arms scope, sends input packet, captures trigger,
    and reads 8-byte output.

    Returns:
        status, output_hex
    """
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


def get_expected_output(scope, target, rnd, pkt):
    """
    Runs the target once with clean clock to get expected output
    for this exact input/randomness.
    """
    scope.io.hs2 = "clkgen"
    time.sleep(0.02)

    status, expected_hex = run_once(scope, target, rnd, pkt)

    if status != "response":
        return status, ""

    return "expected_ok", expected_hex


def run_glitched_output(scope, target, rnd, pkt):
    """
    Runs the target once with glitched clock.
    """
    scope.io.hs2 = "glitch"
    time.sleep(0.02)

    status, output_hex = run_once(scope, target, rnd, pkt)

    return status, output_hex


scope = cw.scope()
target = cw.target(scope, cw.targets.SimpleSerial)

try:
    setup_scope(scope)
    target.baud = 38400

    fixed_rnd = u32(0x00010101)

    print("[+] Glitch setting:")
    print(f"    width      = {WIDTH}")
    print(f"    offset     = {OFFSET}")
    print(f"    ext_offset = {EXT_OFFSET}")
    print(f"    repeat     = {REPEAT}")

    print("[+] Randomize input every trial:", True)
    print("[+] Randomize randomness every trial:", RANDOMIZE_RANDOMNESS)
    print(f"[+] Running {N_RUNS} trials...")

    reset_target(scope)
    target.flush()

    status_counter = Counter()
    output_counter = Counter()
    position_counter = Counter()
    xor_diff_counter = Counter()

    with open(RESULT_FILE, "w", newline="") as f:
        writer = csv.writer(f)

        writer.writerow([
            "trial",
            "width",
            "offset",
            "ext_offset",
            "repeat",
            "status",
            "randomness_hex",
            "input_packet_hex",
            "input_w0",
            "input_w1",
            "input_w2",
            "input_w3",
            "expected_output",
            "glitched_output",
            "fault_byte_count",
            "fault_byte_positions_0_indexed",
            "xor_diffs_hex"
        ])

        for trial in range(1, N_RUNS + 1):
            pkt, words = make_random_packet()

            if RANDOMIZE_RANDOMNESS:
                rnd_word = rand_u32()
                rnd = u32(rnd_word)
            else:
                rnd_word = 0x00010101
                rnd = fixed_rnd

            pkt_hex = pkt.hex()
            rnd_hex = rnd.hex()

            # --------------------------------------------------
            # 1. Get expected output using clean clock
            # --------------------------------------------------
            expected_status, expected_hex = get_expected_output(
                scope,
                target,
                rnd,
                pkt
            )

            if expected_status != "expected_ok":
                final_status = "expected_failed"
                output_hex = ""
                fault_byte_count = 0
                fault_positions = []
                xor_diffs = []

                print(
                    f"EXPECTED_FAILED trial={trial:04d} | "
                    f"status={expected_status} | "
                    f"input={pkt_hex} | rnd={rnd_hex}"
                )

                reset_target(scope)
                target.flush()

            else:
                # --------------------------------------------------
                # 2. Run same input with glitch clock
                # --------------------------------------------------
                glitch_status, output_hex = run_glitched_output(
                    scope,
                    target,
                    rnd,
                    pkt
                )

                fault_byte_count = 0
                fault_positions = []
                xor_diffs = []

                if glitch_status == "response":
                    if output_hex == expected_hex:
                        final_status = "normal"
                    else:
                        final_status = "fault"

                        fault_byte_count, fault_positions, xor_diffs = classify_fault(
                            expected_hex,
                            output_hex
                        )

                        positions_str = ";".join(str(p) for p in fault_positions)
                        xor_diffs_str = ";".join(f"{x:02x}" for x in xor_diffs)

                        print(
                            f"FAULT trial={trial:04d} | "
                            f"expected={expected_hex} | "
                            f"out={output_hex} | "
                            f"bytes={fault_byte_count} | "
                            f"positions={positions_str} | "
                            f"xor={xor_diffs_str} | "
                            f"input={pkt_hex} | "
                            f"rnd={rnd_hex}"
                        )

                        output_counter[output_hex] += 1
                        position_counter[positions_str] += 1
                        xor_diff_counter[xor_diffs_str] += 1

                else:
                    final_status = glitch_status

                    print(
                        f"{final_status.upper()} trial={trial:04d} | "
                        f"expected={expected_hex} | "
                        f"input={pkt_hex} | "
                        f"rnd={rnd_hex}"
                    )

            positions_str = ";".join(str(p) for p in fault_positions)
            xor_diffs_str = ";".join(f"{x:02x}" for x in xor_diffs)

            status_counter[final_status] += 1

            writer.writerow([
                trial,
                WIDTH,
                OFFSET,
                EXT_OFFSET,
                REPEAT,
                final_status,
                rnd_hex,
                pkt_hex,
                f"0x{words[0]:08x}",
                f"0x{words[1]:08x}",
                f"0x{words[2]:08x}",
                f"0x{words[3]:08x}",
                expected_hex,
                output_hex,
                fault_byte_count,
                positions_str,
                xor_diffs_str
            ])

            if trial % 100 == 0:
                print(f"[+] Completed {trial}/{N_RUNS}")

    print("\n========== SUMMARY ==========")
    print("[+] Setting:")
    print(f"    width      = {WIDTH}")
    print(f"    offset     = {OFFSET}")
    print(f"    ext_offset = {EXT_OFFSET}")
    print(f"    repeat     = {REPEAT}")

    print("\n[+] Status counts:")
    for status, count in status_counter.most_common():
        rate = 100.0 * count / N_RUNS
        print(f"    {status:>18}: {count:>5} / {N_RUNS} = {rate:.2f}%")

    print("\n[+] Top repeated faulty outputs:")
    for output, count in output_counter.most_common(10):
        rate = 100.0 * count / N_RUNS
        print(f"    {count:>5}x ({rate:>6.2f}%)  {output}")

    print("\n[+] Top fault byte positions:")
    for positions, count in position_counter.most_common(10):
        rate = 100.0 * count / N_RUNS
        print(f"    {count:>5}x ({rate:>6.2f}%)  bytes [{positions}]")

    print("\n[+] Top XOR differences:")
    for xor_diff, count in xor_diff_counter.most_common(10):
        rate = 100.0 * count / N_RUNS
        print(f"    {count:>5}x ({rate:>6.2f}%)  xor [{xor_diff}]")

    print("\n[+] Results saved to:", RESULT_FILE)

finally:
    target.dis()
    scope.dis()