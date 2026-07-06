import numpy as np
import time
import csv
import random
from collections import Counter
import chipwhisperer as cw

RESULT_FILE = "repeat_1000_w5_off5_ext60-68_random_input.csv"
N_RUNS = 1000
WIDTH = [-40, -20, 1, 20, 40]
OFFSET = range(-40, 40, 10)
EXT_OFFSET = range(60, 68, 1)
REPEAT = 1

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

    # --- FIXED: Removed list assignments from here ---
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
    scope.io.hs2 = "clkgen"
    time.sleep(0.02)
    status, expected_hex = run_once(scope, target, rnd, pkt)
    if status != "response":
        return status, ""
    return "expected_ok", expected_hex


def run_glitched_output(scope, target, rnd, pkt):
    scope.io.hs2 = "glitch"
    time.sleep(0.02)
    status, output_hex = run_once(scope, target, rnd, pkt)
    return status, output_hex


scope = cw.scope()
target = cw.target(scope, cw.targets.SimpleSerial)

try:
    setup_scope(scope)
    target.baud = 38400

    fixed_rnd = u32(0x00000000)

    print("[+] Glitch setting ranges:")
    print(f"    width      = {WIDTH}")
    print(f"    offset     = {OFFSET}")
    print(f"    ext_offset = {EXT_OFFSET}")

    reset_target(scope)
    target.flush()

    status_counter = Counter()
    output_counter = Counter()
    position_counter = Counter()
    xor_diff_counter = Counter()

    total_measurements = 0

    with open(RESULT_FILE, "w", newline="") as f:
        writer = csv.writer(f)

        writer.writerow([
            "trial", "width", "offset", "ext_offset", "repeat", "status",
            "randomness_hex", "input_packet_hex", "input_w0", "input_w1",
            "input_w2", "input_w3", "expected_output", "glitched_output",
            "fault_byte_count", "fault_byte_positions_0_indexed", "xor_diffs_hex"
        ])

        # --- FIXED: Sweeping through variables using loops ---
        trial = 1
        for w in WIDTH:
            scope.glitch.width = w
            for o in OFFSET:
                scope.glitch.offset = o
                for eo in EXT_OFFSET:
                    scope.glitch.ext_offset = eo

                    for run in range(N_RUNS):
                        total_measurements += 1
                        pkt, words = make_random_packet()

                        if RANDOMIZE_RANDOMNESS:
                            rnd_word = rand_u32()
                            rnd = u32(rnd_word)
                        else:
                            rnd_word = 0x00000000
                            rnd = fixed_rnd

                        pkt_hex = pkt.hex()
                        rnd_hex = rnd.hex()

                        # 1. Clean run
                        expected_status, expected_hex = get_expected_output(scope, target, rnd, pkt)

                        if expected_status != "expected_ok":
                            final_status = "expected_failed"
                            output_hex = ""
                            fault_byte_count = 0
                            fault_positions = []
                            xor_diffs = []
                            reset_target(scope)
                            target.flush()
                        else:
                            # 2. Glitched run
                            glitch_status, output_hex = run_glitched_output(scope, target, rnd, pkt)

                            fault_byte_count = 0
                            fault_positions = []
                            xor_diffs = []

                            if glitch_status == "response":
                                if output_hex == expected_hex:
                                    final_status = "normal"
                                else:
                                    final_status = "fault"
                                    fault_byte_count, fault_positions, xor_diffs = classify_fault(expected_hex, output_hex)
                                    
                                    positions_str = ";".join(str(p) for p in fault_positions)
                                    xor_diffs_str = ";".join(f"{x:02x}" for x in xor_diffs)

                                    output_counter[output_hex] += 1
                                    position_counter[positions_str] += 1
                                    xor_diff_counter[xor_diffs_str] += 1
                            else:
                                final_status = glitch_status

                        positions_str = ";".join(str(p) for p in fault_positions)
                        xor_diffs_str = ";".join(f"{x:02x}" for x in xor_diffs)

                        status_counter[final_status] += 1

                        # --- FIXED: Logging individual parameter scalar values ---
                        writer.writerow([
                            trial, w, o, eo, REPEAT, final_status,
                            rnd_hex, pkt_hex, f"0x{words[0]:08x}", f"0x{words[1]:08x}",
                            f"0x{words[2]:08x}", f"0x{words[3]:08x}", expected_hex,
                            output_hex, fault_byte_count, positions_str, xor_diffs_str
                        ])

                        if trial % 500 == 0:
                            print(f"[+] Completed trial {trial} (Current parameter mapping: W={w}, Off={o}, Ext={eo})")
                        
                        trial += 1

    print("\n========== SUMMARY ==========")
    print(f"\n[+] Status counts out of {total_measurements} total injections:")
    for status, count in status_counter.most_common():
        rate = 100.0 * count / total_measurements
        print(f"    {status:>18}: {count:>5} / {total_measurements} = {rate:.2f}%")

    # ... remaining statistics strings remain identical ...
    print("\n[+] Results saved to:", RESULT_FILE)

finally:
    target.dis()
    scope.dis()