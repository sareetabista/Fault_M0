import numpy as np
import time
import csv
from collections import Counter
import chipwhisperer as cw


RESULT_FILE = "clock_glitch_results_ext82-84cycle.csv"

# Byte positions are 0-indexed:
# byte 0 = first byte of 8-byte output
# byte 7 = last byte of 8-byte output


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

    # This matched your working clean capture
    scope.clock.adc_src = "clkgen_x1"

    # Trigger from target
    scope.trigger.triggers = "tio4"

    # Clock glitch setup
    scope.glitch.clk_src = "clkgen"
    scope.glitch.output = "clock_xor"
    scope.glitch.trigger_src = "ext_single"

    # Conservative starting point
    scope.glitch.repeat = 1


def classify_fault(clean_hex, output_hex):
    """
    Compare clean output and glitched output byte-by-byte.

    Returns:
        fault_byte_count: number of bytes that changed
        fault_positions: list of changed byte positions, 0-indexed
    """
    clean_bytes = bytes.fromhex(clean_hex)
    output_bytes = bytes.fromhex(output_hex)

    fault_positions = []

    for i, (c, o) in enumerate(zip(clean_bytes, output_bytes)):
        if c != o:
            fault_positions.append(i)

    return len(fault_positions), fault_positions


def send_randomness(target, scope, rnd):
    """
    Send randomness command 'p'.
    Returns True if ACK is successful.
    """
    target.simpleserial_write("p", rnd)
    ack = target.simpleserial_wait_ack(timeout=500)

    if ack != 0:
        reset_target(scope)
        target.flush()
        return False

    return True


def run_target_once(scope, target, rnd, pkt):
    """
    Runs one normal/glitched execution.

    Returns:
        status, output_hex
    """
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

    response = target.simpleserial_read(
        "r",
        8,
        ack=True,
        timeout=500
    )

    if response is None:
        reset_target(scope)
        target.flush()
        return "no_response", ""

    output_hex = bytes(response).hex()
    return "response", output_hex


def measure_clean_output(scope, target, rnd, pkt, trials=3):
    """
    Measure clean output using normal clock.
    Runs multiple times to confirm clean output is stable.
    """
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
        print("[!] Clean output counts:")
        for output, count in counts.items():
            print(f"    {output}: {count}")

        raise RuntimeError("Clean output changed across trials. Do not glitch yet.")

    clean_output = clean_outputs[0]

    print("[+] Final measured clean output:", clean_output)

    return clean_output


scope = cw.scope()
target = cw.target(scope, cw.targets.SimpleSerial)

try:
    setup_scope(scope)

    target.baud = 38400

    rnd = u32(0x00100001)

    pkt = (
        u32(0x01020304) +
        u32(0x05060708) +
        u32(0x090a0b0c) +
        u32(0x0d0e0f10)
    )

    print("[+] Packet length:", len(pkt))
    print("[+] Packet hex:", pkt.hex())
    print("[+] Randomness hex:", rnd.hex())

    # ----------------------------------------------------
    # 1. Automatically measure expected clean output
    # ----------------------------------------------------
    clean_output = measure_clean_output(scope, target, rnd, pkt, trials=3)

    # ----------------------------------------------------
    # 2. Now enable clock glitching
    # ----------------------------------------------------
    print("[+] Switching HS2 to glitched clock...")
    scope.io.hs2 = "glitch"

    reset_target(scope)
    target.flush()

    # Your selected focused search region
    widths = range(-48, 48, 1)
    offsets = range(-48, 48, 1)

    ext_offsets = (
        #list(range(63, 65))     # early AND/store region
        #list(range(58, 75)) +    # strongest computation/leakage region
        list(range(82, 85))    # later XOR/store/return region
        #list(range(94, 105))     # branch/call/return-related region
    )

    repeats = [1]

    total_tests = 0
    normal_count = 0
    fault_count = 0
    no_response_count = 0
    timeout_count = 0
    p_ack_failed_count = 0
    error_count = 0

    fault_outputs = Counter()
    fault_positions_counter = Counter()

    with open(RESULT_FILE, "w", newline="") as f:
        writer = csv.writer(f)

        writer.writerow([
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

        for repeat in repeats:
            scope.glitch.repeat = repeat

            for width in widths:
                scope.glitch.width = width

                for offset in offsets:
                    scope.glitch.offset = offset

                    for ext_offset in ext_offsets:
                        scope.glitch.ext_offset = ext_offset

                        total_tests += 1

                        try:
                            status, output_hex = run_target_once(scope, target, rnd, pkt)

                            fault_byte_count = 0
                            fault_positions = []

                            if status == "response":
                                if output_hex == clean_output:
                                    status = "normal"
                                    normal_count += 1

                                else:
                                    status = "fault"
                                    fault_count += 1

                                    fault_byte_count, fault_positions = classify_fault(
                                        clean_output,
                                        output_hex
                                    )

                                    positions_str = ";".join(str(p) for p in fault_positions)

                                    fault_outputs[output_hex] += 1
                                    fault_positions_counter[positions_str] += 1

                                    print(
                                        f"FAULT | "
                                        f"w={width:>4}, off={offset:>4}, "
                                        f"ext={ext_offset:>4}, rep={repeat} | "
                                        f"out={output_hex} | "
                                        f"bytes_faulted={fault_byte_count} | "
                                        f"positions={positions_str}"
                                    )

                            elif status == "no_response":
                                no_response_count += 1
                                print(
                                    f"NO_RESPONSE | "
                                    f"w={width:>4}, off={offset:>4}, "
                                    f"ext={ext_offset:>4}, rep={repeat}"
                                )

                            elif status == "capture_timeout":
                                timeout_count += 1
                                print(
                                    f"TIMEOUT | "
                                    f"w={width:>4}, off={offset:>4}, "
                                    f"ext={ext_offset:>4}, rep={repeat}"
                                )

                            elif status == "p_ack_failed":
                                p_ack_failed_count += 1
                                print(
                                    f"P_ACK_FAILED | "
                                    f"w={width:>4}, off={offset:>4}, "
                                    f"ext={ext_offset:>4}, rep={repeat}"
                                )

                            positions_str = ";".join(str(p) for p in fault_positions)

                            writer.writerow([
                                width,
                                offset,
                                ext_offset,
                                repeat,
                                status,
                                clean_output,
                                output_hex,
                                fault_byte_count,
                                positions_str
                            ])

                        except Exception as e:
                            error_count += 1

                            print(
                                f"ERROR | "
                                f"w={width:>4}, off={offset:>4}, "
                                f"ext={ext_offset:>4}, rep={repeat} | "
                                f"{e}"
                            )

                            writer.writerow([
                                width,
                                offset,
                                ext_offset,
                                repeat,
                                "error",
                                clean_output,
                                str(e),
                                "",
                                ""
                            ])

                            reset_target(scope)
                            target.flush()

    print("\n========== SUMMARY ==========")
    print("[+] Total tests:", total_tests)
    print("[+] Normal:", normal_count)
    print("[+] Faults:", fault_count)
    print("[+] No response:", no_response_count)
    print("[+] Capture timeout:", timeout_count)
    print("[+] p ACK failed:", p_ack_failed_count)
    print("[+] Errors:", error_count)

    print("\n[+] Top repeated faulty outputs:")
    for output, count in fault_outputs.most_common(10):
        print(f"    {count:>5}x  {output}")

    print("\n[+] Top repeated fault byte positions:")
    for positions, count in fault_positions_counter.most_common(10):
        print(f"    {count:>5}x  bytes [{positions}]")

    print("\n[+] Results saved to:", RESULT_FILE)

finally:
    target.dis()
    scope.dis()