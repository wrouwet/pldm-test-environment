# PLDM Test Environment

A pytest-based test suite, run from a host PC, for exercising an
[OpenBIC](https://github.com/facebook/OpenBIC) controller's **PLDM**
stack — DSP0240 base messaging/discovery and DSP0248 Platform Monitoring
& Control — carried as MCTP message type `0x01` over I2C, via a
USB-to-I2C bridge.

One of a family of sibling suites that each test the same OpenBIC
controller over the same one I2C bus, named by protocol:

| Repo | Layer |
|---|---|
| `ipmi-test-environment` | IPMI over IPMB |
| `mctp-test-environment` | MCTP transport + Control Protocol |
| `spdm-test-environment` | SPDM (DSP0274) over MCTP |
| **`pldm-test-environment`** (this repo) | PLDM over MCTP |
| `openbic-discovery` | no assertions — reads every layer and prints an inventory |

Same style and philosophy as the siblings: `-v -s` wire-level output by
default, `pldm_helpers.not_implemented()` (`xfail(strict=True)`) to track
firmware gaps as live tests, and **confirm behaviour on the wire, not
from the spec** — completion codes and response layouts here are checked
against real hardware and/or the firmware peer, not guessed.

## What's covered

**Base (`test_pldm_base.py`)** — GetTID, SetTID round-trip (persists as
of fork tip `77c73bd0`), GetPLDMTypes, GetPLDMCommands cross-check (base
+ platform), GetPLDMVersion (1.0.0 for every advertised type; the
GetPLDMVersion-specific `INVALID_PLDM_TYPE` for anything else).

**PDR repository (`test_pldm_platform_pdr.py`)** — GetPDRRepositoryInfo,
a full GetPDR walk of the 4-record chain (Terminus Locator → Numeric
Sensor → State Sensor → State Effecter), chain-termination and
invalid-handle checks.

**Type 2 sensors/effecter (`test_pldm_platform_sensors.py`)** —
- Numeric Sensor `0x0001` = MCXN947 die temperature: GetSensorReading
  scaled to °C via its Numeric Sensor PDR, sanity-windowed, with a
  PDR-vs-reading `sensorDataSize` cross-check.
- State Sensor `0x0002` = SW2 button (Presence set 13): reads a valid
  present/not-present state.
- State Effecter `0x0003` = green LED (OEM device-status set 32768,
  1=off/2=on): SetStateEffecterStates → GetStateEffecterStates
  round-trip, restores original state.

**Interactive tests** (button toggle, LED visual confirm) only run with
`PLDM_INTERACTIVE=1` set and a human at the board; otherwise they skip.

## Requirements

Same as the sibling suites: Linux, Python 3.9+, `dialout` group
membership, and the FRDM-MCXA153 bridge (with SMBus `WS`/`RS`/`XS`
firmware support) on its **"MCU USB"** port, wired to the OpenBIC
target's I2C and powered on. Since the 2026-08-27 bus consolidation the
PLDM/MCTP endpoint (`0x10`) shares one physical bus (`flexcomm2_lpi2c2`)
with IPMB (`0x20`). See `ipmi-test-environment`'s README for the full
hardware/`dialout` walkthrough.

## Run

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/pytest tests/                 # everything (interactive tests skip)
PLDM_INTERACTIVE=1 .venv/bin/pytest tests/   # include button/LED human-in-loop
.venv/bin/pytest tests/test_pldm_base.py -k version
```

`./run_tests.sh` runs the suite and also tees a clean copy to
`test_report.txt` (git-ignored).

## Layout

```
bridge.py            bridge client — vendored from mctp-test-environment (W/R/X/I/L + SMBus WS/RS/XS)
mctp.py              MCTP transport + SMBus PEC + fragmentation — vendored from mctp-test-environment
pldm.py              PLDM base + Platform (DSP0240/DSP0248) request framing and response/PDR parsers
conftest.py          session-scoped bridge fixture
tests/config.py      target addr/EID, sensor/effecter ids, expected PDR map, scaling window
tests/pldm_helpers.py  send_pldm_command() round trip (MCTP wrap + PEC + reassembly), walk_pdrs(), not_implemented()
tests/test_pldm_base.py
tests/test_pldm_platform_pdr.py
tests/test_pldm_platform_sensors.py
```

`bridge.py` / `mctp.py` are vendored copies, not a shared package —
same "keep each repo independently clonable" choice as the siblings.

## Status

First-contact against real hardware: firmware `v4.4.0-3-gf984613ef61c`
(wrouwet/OpenBIC full-board-port `77c73bd0`). Treat any new response
shape as first-contact integration, per this project family's rhythm —
the PDR field offsets for scaling in particular are checked, not
trusted.
