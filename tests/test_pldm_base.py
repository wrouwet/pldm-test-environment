"""PLDM base protocol (DSP0240 type 0x00): discovery + TID.

All of these run against real hardware. Read-only except
test_set_tid_persists, which restores the TID it found.
"""

import pldm
from pldm_helpers import assert_cc, next_inst_id, not_implemented, send_pldm_command
from config import (
    EXPECTED_BASE_COMMANDS,
    EXPECTED_PLATFORM_COMMANDS,
    EXPECTED_PLDM_TYPES,
    EXPECTED_PLDM_VERSION,
)


def test_get_tid(bridge):
    """GetTID (0x02). Response is 1 byte, the terminus ID."""
    d = send_pldm_command(bridge, pldm.build_get_tid(next_inst_id()))
    assert_cc(d, pldm.CC_SUCCESS)
    assert len(d["data"]) == 1, f"expected 1 TID byte, got {d['data'].hex(' ')}"
    print(f"TID = {d['data'][0]}")


def test_set_tid_persists(bridge):
    """SetTID (0x01) now actually persists (was a no-op before fork tip
    77c73bd0). Set the TID to a distinct value, confirm GetTID reflects
    it, then restore the original.
    """
    original = send_pldm_command(bridge, pldm.build_get_tid(next_inst_id()))
    assert_cc(original, pldm.CC_SUCCESS)
    orig_tid = original["data"][0]
    probe_tid = 0x42 if orig_tid != 0x42 else 0x43

    try:
        s = send_pldm_command(bridge, pldm.build_set_tid(probe_tid, next_inst_id()))
        assert_cc(s, pldm.CC_SUCCESS, "SetTID")
        after = send_pldm_command(bridge, pldm.build_get_tid(next_inst_id()))
        assert_cc(after, pldm.CC_SUCCESS)
        assert after["data"][0] == probe_tid, (
            f"SetTID({probe_tid}) didn't stick -- GetTID still reports {after['data'][0]}"
        )
    finally:
        restore = send_pldm_command(bridge, pldm.build_set_tid(orig_tid, next_inst_id()))
        assert_cc(restore, pldm.CC_SUCCESS, "restore original TID")


def test_get_pldm_types(bridge):
    """GetPLDMTypes (0x04). Bit N set => PLDM type N supported."""
    d = send_pldm_command(bridge, pldm.build_get_pldm_types(next_inst_id()))
    assert_cc(d, pldm.CC_SUCCESS)
    types = pldm.decode_bitfield_ids(d["data"], 64)
    print(f"supported PLDM types: {[hex(t) for t in types]} "
          f"({', '.join(pldm.PLDM_TYPE_NAMES.get(t, '?') for t in types)})")
    assert set(types) == set(EXPECTED_PLDM_TYPES), (
        f"expected {[hex(t) for t in EXPECTED_PLDM_TYPES]}, got {[hex(t) for t in types]}"
    )


def test_get_pldm_commands_base(bridge):
    """GetPLDMCommands (0x05) for the base type. Cross-checks that the
    advertised command list matches what's really wired -- GetPLDMVersion
    (0x03) is now in it (real handler since fork tip 77c73bd0).
    """
    d = send_pldm_command(bridge, pldm.build_get_pldm_commands(pldm.PLDM_TYPE_BASE, inst_id=next_inst_id()))
    assert_cc(d, pldm.CC_SUCCESS)
    cmds = pldm.decode_bitfield_ids(d["data"], 256)
    print(f"base commands: {[pldm.BASE_CMD_NAMES.get(c, hex(c)) for c in cmds]}")
    assert set(cmds) == set(EXPECTED_BASE_COMMANDS), (
        f"expected {[hex(c) for c in EXPECTED_BASE_COMMANDS]}, got {[hex(c) for c in cmds]}"
    )


def test_get_pldm_commands_platform(bridge):
    """GetPLDMCommands (0x05) for the platform type (0x02) -- the Type 2
    command subset this port implements.
    """
    d = send_pldm_command(bridge, pldm.build_get_pldm_commands(pldm.PLDM_TYPE_PLATFORM, inst_id=next_inst_id()))
    assert_cc(d, pldm.CC_SUCCESS)
    cmds = pldm.decode_bitfield_ids(d["data"], 256)
    print(f"platform commands: {[pldm.PLATFORM_CMD_NAMES.get(c, hex(c)) for c in cmds]}")
    assert set(cmds) == set(EXPECTED_PLATFORM_COMMANDS), (
        f"expected {[hex(c) for c in EXPECTED_PLATFORM_COMMANDS]}, got {[hex(c) for c in cmds]}"
    )


def test_get_pldm_version_base(bridge):
    """GetPLDMVersion (0x03) for the base type. Reports PLDM 1.0.0
    (ver32 0xF1F0F000) + a trailing CRC-32, transfer_flag = StartAndEnd.
    """
    d = send_pldm_command(bridge, pldm.build_get_pldm_version(pldm.PLDM_TYPE_BASE, next_inst_id()))
    assert_cc(d, pldm.CC_SUCCESS)
    assert d["data"][4] == pldm.XFER_FLAG_START_AND_END, (
        f"expected transfer_flag StartAndEnd (0x05), got 0x{d['data'][4]:02x}"
    )
    versions = pldm.parse_get_pldm_version_data(d["data"])
    print(f"base PLDM versions: {versions}")
    assert EXPECTED_PLDM_VERSION in versions, f"expected {EXPECTED_PLDM_VERSION} in {versions}"


def test_get_pldm_version_every_advertised_type(bridge):
    """GetPLDMVersion (0x03) reports 1.0.0 for every type in GetPLDMTypes."""
    for t in EXPECTED_PLDM_TYPES:
        d = send_pldm_command(bridge, pldm.build_get_pldm_version(t, next_inst_id()))
        assert_cc(d, pldm.CC_SUCCESS, f"GetPLDMVersion(type 0x{t:02x})")
        versions = pldm.parse_get_pldm_version_data(d["data"])
        print(f"type 0x{t:02x} -> {versions}")
        assert EXPECTED_PLDM_VERSION in versions, (
            f"type 0x{t:02x}: expected {EXPECTED_PLDM_VERSION}, got {versions}"
        )


def test_get_pldm_version_unadvertised_type_rejected(bridge):
    """GetPLDMVersion (0x03) for a type not in GetPLDMTypes returns the
    GetPLDMVersion-specific INVALID_PLDM_TYPE (0x83), confirmed w/ peer
    2026-08-27. 0x10 is a safely-unassigned type here.
    """
    unadvertised = 0x10
    assert unadvertised not in EXPECTED_PLDM_TYPES
    d = send_pldm_command(bridge, pldm.build_get_pldm_version(unadvertised, next_inst_id()))
    assert_cc(d, pldm.GETVER_INVALID_PLDM_TYPE,
              "GetPLDMVersion INVALID_PLDM_TYPE_IN_REQUEST_DATA")
