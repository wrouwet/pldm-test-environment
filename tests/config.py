"""Shared constants for the PLDM test suite.

Every platform fact here is confirmed with the peer session developing
this OpenBIC port (meta-facebook/mcx-n9xx-evk, wrouwet/OpenBIC
full-board-port) and/or observed live over the wire -- never guessed.
Firmware baseline: v4.4.0-3-gf984613ef61c (fork tip 77c73bd0, PLDM Type
2 phases 2-3 + base connectivity fixes).
"""

# --- bus / transport (shared with mctp-test-environment) -----------------
# The MCTP endpoint carrying PLDM. Since the 2026-08-27 bus consolidation
# this shares one physical bus (flexcomm2_lpi2c2) with IPMB at 0x20.
MCTP_TARGET_ADDR = 0x10
TARGET_EID = 0x09
OUR_I2C_ADDR = 0x08
OUR_EID = 0x08

# --- PLDM base ----------------------------------------------------------
# GetPLDMTypes should report exactly these (bit ids), 2026-08-27.
EXPECTED_PLDM_TYPES = (0x00, 0x01, 0x02, 0x05, 0x3F)  # base, SMBIOS, platform, fwupd, OEM

# GetPLDMCommands(base) should report exactly these command ids now that
# GetPLDMVersion (0x03) has a real handler.
EXPECTED_BASE_COMMANDS = (0x01, 0x02, 0x03, 0x04, 0x05)  # SetTID/GetTID/GetPLDMVersion/GetPLDMTypes/GetPLDMCommands

# GetPLDMCommands(platform) -- the Type 2 subset this port implements.
EXPECTED_PLATFORM_COMMANDS = (0x11, 0x21, 0x39, 0x3A, 0x50, 0x51)
# GetSensorReading / GetStateSensorReadings / SetStateEffecterStates /
# GetStateEffecterStates / GetPDRRepositoryInfo / GetPDR

# GetPLDMVersion reports PLDM 1.0.0 (ver32 0xF1F0F000) for every advertised
# type, transfer_flag = StartAndEnd, followed by a 4-byte CRC-32.
EXPECTED_PLDM_VERSION = "1.0.0"

# --- PLDM Platform (DSP0248) -- the 4-record PDR repository ---------------
EXPECTED_PDR_RECORD_COUNT = 4
# record_handle -> (pdr_type, description). Chain: 0 -> 1 -> 2 -> 3 -> 4 -> 0.
PDR_MAP = {
    1: (1, "Terminus Locator (TID + MCTP EID 0x09)"),
    2: (2, "Numeric Sensor -- MCXN947 die temperature"),
    3: (4, "State Sensor -- SW2 button, state set 13 (Presence)"),
    4: (11, "State Effecter -- on-board green LED, state set 32768 (OEM device-status)"),
}

# Sensors/effecters are addressed by these ids in the command payload,
# NOT by PDR record handle (DSP0248).
NUMERIC_SENSOR_ID = 0x0001      # die temperature
STATE_SENSOR_ID = 0x0002       # SW2 button
STATE_EFFECTER_ID = 0x0003     # green LED

# State Sensor SW2: state set 13 (Presence). 1 = present (pressed),
# 2 = not-present (released).
PRESENCE_STATE_SET = 13
SW2_PRESENT = 1
SW2_NOT_PRESENT = 2

# State Effecter LED: state set 32768 (OEM device-status). 1 = off, 2 = on.
LED_STATE_SET = 32768
LED_OFF = 1
LED_ON = 2

# All composite sensor/effecter counts on this platform are 1.
COMPOSITE_COUNT = 1

# Die-temp sanity window for the reading test (degrees C) -- generous,
# just enough to catch "scaling is wildly wrong" vs a plausible ambient.
DIE_TEMP_MIN_C = 5.0
DIE_TEMP_MAX_C = 90.0
