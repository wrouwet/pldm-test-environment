"""PLDM Platform (DSP0248) Type 2: the real sensors/effecter on this
board.

- Numeric Sensor 0x0001 = MCXN947 on-die temperature (real analog read)
- State Sensor  0x0002 = SW2 push-button (Presence state set 13)
- State Effecter 0x0003 = on-board green LED (OEM device-status set 32768)

The LED round-trip and the plain state-sensor read are non-interactive
real assertions. The button-toggle and LED-visual tests need a human at
the board and only run with PLDM_INTERACTIVE=1 in the environment.
"""

import os

import pytest

import pldm
from pldm_helpers import assert_cc, next_inst_id, not_implemented, send_pldm_command, walk_pdrs
from config import (
    COMPOSITE_COUNT,
    LED_OFF,
    LED_ON,
    NUMERIC_SENSOR_ID,
    NUMERIC_SENSOR_UNIT_DEGC,
    NUMERIC_SENSOR_UNIT_VOLTS,
    STATE_EFFECTER_ID,
    STATE_SENSOR_ID,
    SW2_NOT_PRESENT,
    SW2_PRESENT,
    VOLTAGE_MAX_MV,
    VOLTAGE_MIN_MV,
)

INTERACTIVE = os.environ.get("PLDM_INTERACTIVE") == "1"
_needs_human = pytest.mark.skipif(
    not INTERACTIVE, reason="set PLDM_INTERACTIVE=1 and be at the board to run this"
)


def _find_numeric_sensor_pdr(bridge):
    for r in walk_pdrs(bridge):
        if r["pdr_type"] == pldm.PDR_NUMERIC_SENSOR:
            return pldm.parse_numeric_sensor_pdr(r["body"])
    raise AssertionError("no Numeric Sensor PDR in the repository")


def test_numeric_sensor_reading_roundtrips(bridge):
    """GetSensorReading (0x11) on the die-temp sensor: the command
    succeeds, reports an enabled operational state, hands back a
    present reading, and its sensorDataSize matches the Numeric Sensor
    PDR's -- which also validates this suite's Numeric Sensor PDR field
    offsets (sensorDataSize / resolution / offset).
    """
    pdr = _find_numeric_sensor_pdr(bridge)
    print(f"Numeric Sensor PDR: {pdr}")
    assert pdr.get("sensor_id") == NUMERIC_SENSOR_ID, (
        f"Numeric Sensor PDR sensor_id 0x{pdr.get('sensor_id'):04x} != expected "
        f"0x{NUMERIC_SENSOR_ID:04x} -- PDR field offsets likely wrong"
    )
    # Unit is in transition: degC(2) on the old build, Volts(5) once
    # sensor 0x0001 is repointed to the LPADC single-ended read.
    assert pdr.get("base_unit") in (NUMERIC_SENSOR_UNIT_DEGC, NUMERIC_SENSOR_UNIT_VOLTS), (
        f"unexpected baseUnit {pdr.get('base_unit')} (expected 2=degC or 5=Volts)"
    )

    d = send_pldm_command(bridge, pldm.build_get_sensor_reading(NUMERIC_SENSOR_ID, inst_id=next_inst_id()))
    assert_cc(d, pldm.CC_SUCCESS)
    r = pldm.parse_get_sensor_reading(d["data"])
    print(f"GetSensorReading: {r}")

    assert pdr["sensor_data_size"] == r["sensor_data_size"], (
        f"PDR sensorDataSize {pdr['sensor_data_size']} != GetSensorReading's "
        f"{r['sensor_data_size']} -- PDR field offsets are off"
    )
    assert r["operational_state"] == 0, f"sensor operational state {r['operational_state']} != enabled(0)"
    assert r["present_reading_raw"] is not None, "no present reading in the response"


@not_implemented(
    "sensor 0x0001 is being repointed from the (broken) MCXN947 on-die temp "
    "sensor to a plain LPADC single-ended voltage read reporting millivolts "
    "(baseUnit=Volts, unitModifier=-3). Confirmed w/ firmware peer 2026-08-27: "
    "the old degC path was a real firmware bug (nxp,lpadc-temp40 + generic "
    "adc_read don't drive the LPADC internal temp conversion). Stays xfail "
    "until the async-events batch flash lands the new voltage PDR; then this "
    "becomes a real mV sanity check and the marker comes off."
)
def test_numeric_sensor_reading_scaled(bridge):
    """GetSensorReading scaled via the Numeric Sensor PDR, sanity-checked
    against a plausible window. Post-repoint: sensor 0x0001 reports
    millivolts, so the value should land in 0..full-scale ADC range.
    """
    pdr = _find_numeric_sensor_pdr(bridge)
    d = send_pldm_command(bridge, pldm.build_get_sensor_reading(NUMERIC_SENSOR_ID, inst_id=next_inst_id()))
    assert_cc(d, pldm.CC_SUCCESS)
    r = pldm.parse_get_sensor_reading(d["data"])
    value = pldm.scale_reading(
        r["present_reading_raw"], pdr.get("resolution", 1.0),
        pdr.get("offset", 0.0), pdr.get("unit_modifier", 0),
    )
    unit = {2: "degC", 5: "V"}.get(pdr.get("base_unit"), f"unit{pdr.get('base_unit')}")
    print(f"scaled reading = {value:.3f} {unit}  (raw {r['present_reading_raw']}, "
          f"m={pdr.get('resolution')} b={pdr.get('offset')} 10^{pdr.get('unit_modifier')})")
    # After the repoint the value is millivolts (unitModifier -3 => base
    # unit already applied), so expect it in the ADC's mV full-scale range.
    assert VOLTAGE_MIN_MV <= value * 1000 <= VOLTAGE_MAX_MV, (
        f"scaled reading {value} {unit} outside the plausible "
        f"{VOLTAGE_MIN_MV}-{VOLTAGE_MAX_MV} mV window"
    )


def test_state_sensor_sw2_reads_a_valid_presence_state(bridge):
    """GetStateSensorReadings (0x21) on SW2. Whatever the button is doing,
    the present state must be a valid Presence value (1 present /
    2 not-present) and the composite count must be 1.
    """
    d = send_pldm_command(bridge, pldm.build_get_state_sensor_readings(STATE_SENSOR_ID, inst_id=next_inst_id()))
    assert_cc(d, pldm.CC_SUCCESS)
    s = pldm.parse_get_state_sensor_readings(d["data"])
    print(f"SW2 state sensor: {s}")
    assert s["composite_sensor_count"] == COMPOSITE_COUNT
    present = s["sensors"][0]["present_state"]
    assert present in (SW2_PRESENT, SW2_NOT_PRESENT), (
        f"SW2 present_state {present} isn't a valid Presence state (1 or 2)"
    )
    print("SW2 currently:", "PRESSED" if present == SW2_PRESENT else "released")


@_needs_human
def test_state_sensor_sw2_toggle(bridge):
    """Interactive: hold SW2 -> Presence goes 'present'; release ->
    'not-present'. Proves the state sensor tracks a real physical input.
    """
    input("\n>>> PRESS AND HOLD the SW2 button, then press Enter (keep holding)... ")
    d = send_pldm_command(bridge, pldm.build_get_state_sensor_readings(STATE_SENSOR_ID, inst_id=next_inst_id()))
    assert_cc(d, pldm.CC_SUCCESS)
    held = pldm.parse_get_state_sensor_readings(d["data"])["sensors"][0]["present_state"]
    assert held == SW2_PRESENT, f"expected Presence=present(1) while held, got {held}"

    input(">>> RELEASE SW2, then press Enter... ")
    d = send_pldm_command(bridge, pldm.build_get_state_sensor_readings(STATE_SENSOR_ID, inst_id=next_inst_id()))
    assert_cc(d, pldm.CC_SUCCESS)
    released = pldm.parse_get_state_sensor_readings(d["data"])["sensors"][0]["present_state"]
    assert released == SW2_NOT_PRESENT, f"expected Presence=not-present(2) after release, got {released}"


def test_state_effecter_led_set_and_readback(bridge):
    """SetStateEffecterStates (0x39) / GetStateEffecterStates (0x3A) on
    the green LED: drive it on, read it back as on; drive it off, read it
    back as off; restore whatever it was. Non-interactive -- the firmware
    reports the effecter's own state, no human needed for the assertion
    (the LED visibly changing is a bonus, see the interactive test).
    """
    baseline = send_pldm_command(bridge, pldm.build_get_state_effecter_states(STATE_EFFECTER_ID, inst_id=next_inst_id()))
    assert_cc(baseline, pldm.CC_SUCCESS)
    b = pldm.parse_get_state_effecter_states(baseline["data"])
    print(f"LED effecter baseline: {b}")
    assert b["composite_effecter_count"] == COMPOSITE_COUNT
    original_state = b["effecters"][0]["present_state"]

    try:
        for want in (LED_ON, LED_OFF):
            s = send_pldm_command(bridge, pldm.build_set_state_effecter_states(
                STATE_EFFECTER_ID, [(1, want)], inst_id=next_inst_id()))
            assert_cc(s, pldm.CC_SUCCESS, f"SetStateEffecterStates({want})")
            g = send_pldm_command(bridge, pldm.build_get_state_effecter_states(
                STATE_EFFECTER_ID, inst_id=next_inst_id()))
            assert_cc(g, pldm.CC_SUCCESS)
            got = pldm.parse_get_state_effecter_states(g["data"])["effecters"][0]["present_state"]
            print(f"set LED -> {want} ({'on' if want == LED_ON else 'off'}); read back {got}")
            assert got == want, f"set LED to {want} but GetStateEffecterStates reports {got}"
    finally:
        if original_state in (LED_ON, LED_OFF):
            send_pldm_command(bridge, pldm.build_set_state_effecter_states(
                STATE_EFFECTER_ID, [(1, original_state)], inst_id=next_inst_id()))


@_needs_human
def test_state_effecter_led_visual(bridge):
    """Interactive: drive the LED and have a human confirm the green LED
    on the board actually changes.
    """
    send_pldm_command(bridge, pldm.build_set_state_effecter_states(
        STATE_EFFECTER_ID, [(1, LED_ON)], inst_id=next_inst_id()))
    assert input("\n>>> Is the green LED ON now? [y/n] ").strip().lower() == "y", "human said LED is not on"

    send_pldm_command(bridge, pldm.build_set_state_effecter_states(
        STATE_EFFECTER_ID, [(1, LED_OFF)], inst_id=next_inst_id()))
    assert input(">>> Is the green LED OFF now? [y/n] ").strip().lower() == "y", "human said LED is not off"
