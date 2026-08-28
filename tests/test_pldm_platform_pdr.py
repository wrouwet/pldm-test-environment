"""PLDM Platform (DSP0248): the PDR repository.

This port's repository is 4 records: Terminus Locator, Numeric Sensor
(die temp), State Sensor (SW2 button), State Effecter (green LED).
GetPDR walks them by next_record_handle chain 0 -> 1 -> 2 -> 3 -> 4 -> 0.
"""

import pldm
from pldm_helpers import assert_cc, next_inst_id, send_pldm_command, walk_pdrs
from config import EXPECTED_PDR_RECORD_COUNT, PDR_MAP


def test_get_pdr_repository_info(bridge):
    """GetPDRRepositoryInfo (0x50): reports the 4-record repository."""
    d = send_pldm_command(bridge, pldm.build_request(
        pldm.CMD_GET_PDR_REPOSITORY_INFO, pldm.PLDM_TYPE_PLATFORM, inst_id=next_inst_id()))
    assert_cc(d, pldm.CC_SUCCESS)
    info = pldm.parse_pdr_repository_info(d["data"])
    print(f"PDR repository info: {info}")
    assert info.get("record_count") == EXPECTED_PDR_RECORD_COUNT, (
        f"expected {EXPECTED_PDR_RECORD_COUNT} PDR records, got {info.get('record_count')} "
        f"(raw {d['data'].hex(' ')})"
    )


def test_pdr_walk_yields_the_four_expected_records(bridge):
    """GetPDR (0x51) walked start-to-end yields exactly the 4 records in
    PDR_MAP, each with the expected record handle and PDR type.
    """
    records = walk_pdrs(bridge)
    got = {r["record_handle"]: r["pdr_type"] for r in records}
    print("walked PDRs:")
    for r in records:
        print(f"  handle {r['record_handle']}: {r['pdr_type_name']} "
              f"(type {r['pdr_type']}, {r['data_length']} body bytes)")

    assert len(records) == EXPECTED_PDR_RECORD_COUNT, (
        f"walk returned {len(records)} records, expected {EXPECTED_PDR_RECORD_COUNT}"
    )
    for handle, (pdr_type, desc) in PDR_MAP.items():
        assert handle in got, f"PDR handle {handle} ({desc}) missing from the walk"
        assert got[handle] == pdr_type, (
            f"PDR handle {handle} is type {got[handle]}, expected {pdr_type} ({desc})"
        )


def test_pdr_walk_chain_terminates_at_zero(bridge):
    """The next_record_handle chain is 1 -> 2 -> 3 -> 4 -> 0 (0 = end)."""
    records = walk_pdrs(bridge)
    chain = [(r["record_handle"], r["_next"]) for r in records]
    print(f"next_record_handle chain: {chain}")
    assert chain[-1][1] == 0, f"last record's next handle should be 0, got {chain[-1][1]}"
    for (_, nxt), (nxt_handle, _) in zip(chain, chain[1:]):
        assert nxt == nxt_handle, f"chain break: expected next {nxt_handle}, record said {nxt}"


def test_get_pdr_invalid_record_handle_rejected(bridge):
    """GetPDR (0x51) for a handle past the end of the repository returns
    a genuine error, not a fabricated record.
    """
    d = send_pldm_command(bridge, pldm.build_get_pdr(0xFFFF, inst_id=next_inst_id()))
    print(f"GetPDR(0xFFFF) -> cc 0x{d['completion_code']:02x} ({d['cc_name']})")
    assert d["completion_code"] != pldm.CC_SUCCESS, (
        "GetPDR for an out-of-range record handle unexpectedly succeeded"
    )
