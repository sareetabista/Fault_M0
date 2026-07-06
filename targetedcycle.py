import numpy as np
import time
import csv
from collections import Counter
import chipwhisperer as cw


# =====================================================
# TARGETED CYCLE SETTINGS
# =====================================================

FIXED_EXT_OFFSET = 64        # fixed cycle to attack, change this to 62, 63, 67, etc.

#WIDTHS = range(-48, 48, 1)   # sweep width from -15 to +15
#OFFSETS = range(-48, 48, 2)  # sweep offset from -15 to +15
WIDTHS = [-40, -20, -10, -2,1,2,10,20,40]
OFFSETS = [-40, -30, -20, -10, 1, 14, 20, 30, 40, 45]

GLITCH_REPEATS = [1]         # ChipWhisperer glitch repeat, not number of trials
TRIALS_PER_SETTING = 50      # actual repeated experiments per width/offset setting

RESULT_FILE = f"targeted_ext_{FIXED_EXT_OFFSET}_width_offset_sweep_00010101.csv"


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


def classify_fault(clean_hex, output_hex):
    clean_bytes = bytes.fromhex(clean_hex)
    output_bytes = bytes.fromhex(output_hex)

    fault_positions = []

    for i, (c, o) in enumerate(zip(clean_bytes, output_bytes)):
        if c != o:
            fault_positions.append(i)

    return len(fault_positions), fault_positions


def send_randomness(target, scope, rnd):
    target.simpleserial_write("p", rnd)
    ack = target.simpleserial_wait_ack(timeout=500)

    if ack != 0:
        reset_target(scope)
        target.flush()
        return False

    return True


def run_target_once(scope, target, rnd, pkt):
    ok = send_randomness(target, scope, rnd)

    if not ok:
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


def measure_clean_output(scope, target, rnd, pkt, trials=3):
    print("[+] Measuring clean output automatically...")

    scope.io.hs2 = "clkgen"

    reset_target(scope)
    target.flush()

    clean_outputs = []

    for i in range(trials):
        status, output_hex = run_target_once(scope, target, rnd, pkt)

        if status != "response":
            raise RuntimeError(f"Clean run failed on trial {i}: status={status}")

        clean_outputs.append(output_hex)
        print(f"[+] Clean trial {i}: {output_hex}")

    counts = Counter(clean_outputs)

    if len(counts) != 1:
        print("[!] Clean output is not stable.")
        for output, count in counts.items():
            print(f"    {output}: {count}")
        raise RuntimeError("Clean output changed across trials.")

    clean_output = clean_outputs[0]
    print("[+] Final measured clean output:", clean_output)

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
    print("[+] Randomness hex:", rnd.hex())

    # =====================================================
    # 1. Measure clean output
    # =====================================================

    clean_output = measure_clean_output(scope, target, rnd, pkt, trials=3)

    # =====================================================
    # 2. Enable glitch clock
    # =====================================================

    print("[+] Switching HS2 to glitched clock...")
    scope.io.hs2 = "glitch"

    reset_target(scope)
    target.flush()

    print("[+] Targeted cycle sweep")
    print(f"    fixed ext_offset = {FIXED_EXT_OFFSET}")
    print(f"    widths           = {list(WIDTHS)}")
    print(f"    offsets          = {list(OFFSETS)}")
    print(f"    glitch repeats   = {GLITCH_REPEATS}")
    print(f"    trials/setting   = {TRIALS_PER_SETTING}")

    total_tests = 0

    overall_status_counter = Counter()
    overall_fault_outputs = Counter()
    overall_fault_positions_counter = Counter()

    with open(RESULT_FILE, "w", newline="") as f:
        writer = csv.writer(f)

        writer.writerow([
            "trial",
            "width",
            "offset",
            "ext_offset",
            "glitch_repeat",
            "status",
            "clean_output",
            "output",
            "fault_byte_count",
            "fault_byte_positions_0_indexed"
        ])

        for glitch_repeat in GLITCH_REPEATS:
            scope.glitch.repeat = glitch_repeat

            for width in WIDTHS:
                scope.glitch.width = width

                for offset in OFFSETS:
                    scope.glitch.offset = offset

                    # fixed cycle
                    scope.glitch.ext_offset = FIXED_EXT_OFFSET

                    setting_status_counter = Counter()
                    setting_fault_outputs = Counter()
                    setting_fault_positions_counter = Counter()

                    for trial in range(1, TRIALS_PER_SETTING + 1):
                        total_tests += 1

                        try:
                            status, output_hex = run_target_once(scope, target, rnd, pkt)

                            fault_byte_count = 0
                            fault_positions = []

                            if status == "response":
                                if output_hex == clean_output:
                                    status = "normal"
                                else:
                                    status = "fault"

                                    fault_byte_count, fault_positions = classify_fault(
                                        clean_output,
                                        output_hex
                                    )

                                    positions_str = ";".join(str(p) for p in fault_positions)

                                    setting_fault_outputs[output_hex] += 1
                                    setting_fault_positions_counter[positions_str] += 1

                                    overall_fault_outputs[output_hex] += 1
                                    overall_fault_positions_counter[positions_str] += 1

                                    print(
                                        f"FAULT | "
                                        f"trial={trial:03d} | "
                                        f"w={width:>4}, off={offset:>4}, "
                                        f"ext={FIXED_EXT_OFFSET:>4}, rep={glitch_repeat} | "
                                        f"out={output_hex} | "
                                        f"bytes={fault_byte_count} | "
                                        f"positions={positions_str}"
                                    )

                            elif status == "no_response":
                                print(
                                    f"NO_RESPONSE | "
                                    f"trial={trial:03d} | "
                                    f"w={width:>4}, off={offset:>4}, "
                                    f"ext={FIXED_EXT_OFFSET:>4}, rep={glitch_repeat}"
                                )

                            elif status == "capture_timeout":
                                print(
                                    f"TIMEOUT | "
                                    f"trial={trial:03d} | "
                                    f"w={width:>4}, off={offset:>4}, "
                                    f"ext={FIXED_EXT_OFFSET:>4}, rep={glitch_repeat}"
                                )

                            elif status == "p_ack_failed":
                                print(
                                    f"P_ACK_FAILED | "
                                    f"trial={trial:03d} | "
                                    f"w={width:>4}, off={offset:>4}, "
                                    f"ext={FIXED_EXT_OFFSET:>4}, rep={glitch_repeat}"
                                )

                            positions_str = ";".join(str(p) for p in fault_positions)

                            setting_status_counter[status] += 1
                            overall_status_counter[status] += 1

                            writer.writerow([
                                trial,
                                width,
                                offset,
                                FIXED_EXT_OFFSET,
                                glitch_repeat,
                                status,
                                clean_output,
                                output_hex,
                                fault_byte_count,
                                positions_str
                            ])

                        except Exception as e:
                            reset_target(scope)
                            target.flush()

                            status = "error"
                            setting_status_counter[status] += 1
                            overall_status_counter[status] += 1

                            print(
                                f"ERROR | "
                                f"trial={trial:03d} | "
                                f"w={width:>4}, off={offset:>4}, "
                                f"ext={FIXED_EXT_OFFSET:>4}, rep={glitch_repeat} | "
                                f"{e}"
                            )

                            writer.writerow([
                                trial,
                                width,
                                offset,
                                FIXED_EXT_OFFSET,
                                glitch_repeat,
                                "error",
                                clean_output,
                                str(e),
                                "",
                                ""
                            ])

                    # Print only settings that produced something interesting
                    if setting_status_counter["fault"] > 0 or setting_status_counter["no_response"] > 0:
                        print("\n[+] Setting summary:")
                        print(
                            f"    width={width}, offset={offset}, "
                            f"ext_offset={FIXED_EXT_OFFSET}, repeat={glitch_repeat}"
                        )

                        for s, c in setting_status_counter.most_common():
                            rate = 100.0 * c / TRIALS_PER_SETTING
                            print(f"    {s:>16}: {c:>4}/{TRIALS_PER_SETTING} = {rate:.2f}%")

                        if setting_fault_outputs:
                            print("    top faulty outputs:")
                            for out, c in setting_fault_outputs.most_common(3):
                                print(f"        {c:>4}x {out}")

                        if setting_fault_positions_counter:
                            print("    top fault byte positions:")
                            for pos, c in setting_fault_positions_counter.most_common(3):
                                print(f"        {c:>4}x bytes [{pos}]")

    print("\n========== OVERALL SUMMARY ==========")
    print("[+] Total tests:", total_tests)

    for s, c in overall_status_counter.most_common():
        rate = 100.0 * c / total_tests
        print(f"    {s:>16}: {c:>5}/{total_tests} = {rate:.2f}%")

    print("\n[+] Top repeated faulty outputs:")
    for output, count in overall_fault_outputs.most_common(10):
        print(f"    {count:>5}x  {output}")

    print("\n[+] Top repeated fault byte positions:")
    for positions, count in overall_fault_positions_counter.most_common(10):
        print(f"    {count:>5}x  bytes [{positions}]")

    print("\n[+] Results saved to:", RESULT_FILE)

finally:
    target.dis()
    scope.dis()