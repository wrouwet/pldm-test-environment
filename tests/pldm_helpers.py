"""Shared plumbing for every PLDM test -- the request/response round trip
over MCTP, plus the not_implemented() backlog marker.

Mirrors mctp-test-environment/tests/mctp_helpers.py deliberately: same
DSP0237 SMBus wrapper, same SMBus-PEC-on-captured-response check, same
"responder becomes bus master and writes back" capture, same
fragment-reassembly-by-msg_tag. The only difference is the message body
is PLDM (MCTP message type 0x01) instead of MCTP Control.
"""

import itertools
import time

import pytest

import mctp
import pldm
from bridge import BridgeError
from config import MCTP_TARGET_ADDR, OUR_EID, OUR_I2C_ADDR, TARGET_EID

# A few ms between transactions. The MCTP endpoint (0x10) shares one
# LPI2C target instance with IPMB (0x20) since the 2026-08-27
# consolidation; zero-gap back-to-back load occasionally outruns it
# (missed ACK / response). Harmless but noisy; a real BMC paces sideband
# polling. Peer-diagnosed 2026-08-28.
_BUS_PACE_S = 0.008

# Whole-transaction retries on a transient bus glitch (listen timeout,
# PEC mismatch from a stale capture). Peer-confirmed these don't corrupt
# state, so one automatic re-send absorbs the noise without changing any
# test's semantics.
_TX_RETRIES = 3
_TX_RETRY_GAP_S = 0.05

# PLDM instance ID (5-bit) matches a response to its request, same role
# as IPMB seq / MCTP Control inst_id. Shared across every test module.
_next_inst_id = itertools.count()
_next_msg_tag = itertools.count()


def next_inst_id():
    return next(_next_inst_id) % 32


def next_msg_tag():
    return next(_next_msg_tag) % 8


def _verify_and_strip_pec(raw):
    if len(raw) < 1:
        raise ValueError("captured frame too short for a PEC byte")
    data, pec_received = raw[:-1], raw[-1]
    pec = mctp.smbus_pec_byte(0, (OUR_I2C_ADDR << 1) | 0)
    pec = mctp.smbus_pec_buf(pec, data)
    if pec != pec_received:
        raise ValueError(f"SMBus PEC mismatch: expected 0x{pec:02x}, got 0x{pec_received:02x}")
    return data


def send_pldm_command(bridge, message_body, max_fragments=16, max_drain=3):
    """Send one PLDM request (message_body = a pldm.build_*() result, i.e.
    the msg-type/IC byte onward) and return the decoded response dict
    from pldm.parse_response(). Reassembles a multi-packet response.

    inst_id matching: the message_body already carries an inst_id in its
    header; this reuses it to validate the response. Callers should build
    each request with a fresh next_inst_id().
    """
    last_exc = None
    for _tx in range(_TX_RETRIES + 1):
        time.sleep(_BUS_PACE_S if _tx == 0 else _TX_RETRY_GAP_S)
        try:
            return _send_pldm_once(bridge, message_body, max_fragments, max_drain)
        except (BridgeError, ValueError, AssertionError) as exc:
            last_exc = exc
            print(f"transient bus glitch ({exc}); re-sending "
                  f"(attempt {_tx + 2}/{_TX_RETRIES + 1})")
    raise AssertionError(f"PLDM command failed after {_TX_RETRIES + 1} attempts: {last_exc}")


def _send_pldm_once(bridge, message_body, max_fragments, max_drain):
    req_inst_id = message_body[1] & 0x1F
    tag = next_msg_tag()
    transport = mctp.build_transport_header(
        TARGET_EID, OUR_EID, msg_tag=tag, tag_owner=1, som=1, eom=1, pkt_seq=0
    )
    payload = transport + bytes(message_body)
    wrapper = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, payload)
    print(f"request (wrapper + MCTP + PLDM): {(wrapper + payload).hex(' ')}")
    bridge.smbus_write(MCTP_TARGET_ADDR, wrapper + payload)

    body = bytearray()
    fragments = 0
    drains = 0
    while fragments < max_fragments:
        raw = bridge.listen(OUR_I2C_ADDR)
        print(f"captured: {raw.hex(' ')}")
        try:
            after_pec = _verify_and_strip_pec(raw)
            _, packet = mctp.parse_smbus_block_wrapper(after_pec)
            hdr = mctp.parse_transport_header(packet)
        except ValueError as exc:
            if fragments == 0:
                drains += 1
                if drains > max_drain:
                    raise AssertionError(f"only malformed frames after {max_drain} tries ({exc})")
                print(f"discarding malformed frame ({exc}); still listening...")
                continue
            raise AssertionError(f"malformed fragment {fragments + 1} ({exc})")
        chunk = packet[4:]
        if fragments == 0:
            if hdr["msg_tag"] != tag or hdr["tag_owner"] != 0:
                drains += 1
                if drains > max_drain:
                    raise AssertionError(f"never saw a fragment for msg_tag={tag}")
                print(f"discarding stale fragment (msg_tag={hdr['msg_tag']}); still listening...")
                continue
            if not hdr["som"]:
                raise AssertionError(f"first matching fragment isn't SOM: {hdr}")
        elif hdr["msg_tag"] != tag:
            raise AssertionError(f"fragment {fragments + 1} msg_tag={hdr['msg_tag']} != {tag}")
        body += chunk
        fragments += 1
        if hdr["eom"]:
            break
    else:
        raise AssertionError(f"no EOM after {max_fragments} fragments")

    decoded = pldm.parse_response(bytes(body))
    print(f"decoded: {decoded}")
    assert decoded["inst_id"] == req_inst_id, (
        f"response inst_id {decoded['inst_id']} != request's {req_inst_id}"
    )
    return decoded


def catch_async_pldm_event(bridge, timeout_s=90):
    """Sit as an armed I2C target at OUR_I2C_ADDR and capture the first
    inbound PLDM PlatformEventMessage (0x0A) the target pushes to us,
    then send it a completion ack. Returns pldm.parse_platform_event_
    message(...).

    Bridge listen() is one-shot (arms ~4s then returns), so this loops
    it back-to-back -- the caller must arrange for the target to fire
    repeatedly across the window so one push lands while we're armed.
    """
    import time
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            raw = bridge.listen(OUR_I2C_ADDR)
        except Exception:
            continue
        if not raw:
            continue
        try:
            data, pec = raw[:-1], raw[-1]
            exp = mctp.smbus_pec_buf(mctp.smbus_pec_byte(0, (OUR_I2C_ADDR << 1) | 0), data)
            assert exp == pec, f"PEC {exp:02x} != {pec:02x}"
            _, packet = mctp.parse_smbus_block_wrapper(data)
            hdr = mctp.parse_transport_header(packet)
            evt = pldm.parse_platform_event_message(packet[4:])
        except (ValueError, AssertionError):
            continue
        if evt.get("cmd") != pldm.CMD_PLATFORM_EVENT_MESSAGE:
            continue
        print(f"caught PlatformEventMessage: mctp={hdr}  evt={evt}")
        try:
            resp = pldm.build_platform_event_message_response(evt["inst_id"])
            th = mctp.build_transport_header(hdr["src_eid"], hdr["dest_eid"],
                                             msg_tag=hdr["msg_tag"], tag_owner=0, som=1, eom=1)
            wrapper = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, th + resp)
            bridge.smbus_write(MCTP_TARGET_ADDR, wrapper + th + resp)
        except Exception as exc:
            print(f"(ack send failed, non-fatal: {exc})")
        return evt
    raise AssertionError(f"no PlatformEventMessage within {timeout_s}s "
                         f"(is the target firing, and was SetEventReceiver re-sent since the last reboot?)")


def assert_cc(decoded, expected, note=""):
    actual = decoded["completion_code"]
    suffix = f" ({note})" if note else ""
    assert actual == expected, (
        f"expected completion code 0x{expected:02x}{suffix}, got "
        f"0x{actual:02x} ({decoded['cc_name']})"
    )


def walk_pdrs(bridge):
    """Walk the whole PDR repository via GetPDR's next_record_handle
    chain (starting at record_handle 0). Returns a list of
    pldm.parse_pdr_common_header() dicts in repository order. Assumes
    each record fits in one GetPDR response (true for this platform's
    small repo); a real transfer-handle continuation would loop on
    next_data_transfer_handle too.
    """
    from pldm_helpers import next_inst_id as _nid  # local import keeps module import order simple
    records = []
    handle = 0
    for _ in range(64):  # generous cap vs the 4-record repo
        decoded = send_pldm_command(bridge, pldm.build_get_pdr(handle, inst_id=_nid()))
        assert_cc(decoded, pldm.CC_SUCCESS, f"GetPDR handle {handle}")
        pdr = pldm.parse_get_pdr_response(decoded["data"])
        hdr = pldm.parse_pdr_common_header(pdr["record"])
        hdr["_next"] = pdr["next_record_handle"]
        hdr["_transfer_flag"] = pdr["transfer_flag"]
        records.append(hdr)
        if pdr["next_record_handle"] == 0:
            break
        handle = pdr["next_record_handle"]
    return records


def not_implemented(reason):
    """xfail(strict=True), identical mechanism to the sibling suites: the
    moment real support lands and the test passes, the run FAILS loudly
    (XPASS) forcing this back to a normal test. Always give a `reason`
    saying what's missing and how it was confirmed.
    """
    return pytest.mark.xfail(reason=reason, strict=True)
