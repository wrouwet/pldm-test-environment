"""PLDM (DMTF DSP0240 base + DSP0248 Platform Monitoring & Control)
message framing over MCTP.

Sibling to ipmi-test-environment / mctp-test-environment / spdm-test-
environment: same host-PC-through-the-FRDM-MCXA153-bridge setup, same
"responder becomes bus master and writes the response back to the
requester" capture pattern, same test style. This one exercises PLDM
carried as MCTP message type 0x01.

PLDM message body layout (what a builder here returns -- the msg-type/IC
byte onward; hand straight to pldm_helpers.send_pldm_command(), which
prepends the MCTP transport header + DSP0237 SMBus wrapper and handles
PEC + fragment reassembly):

    byte0: msg-type/IC byte    -- 0x01 (PLDM), ic=0
    byte1: rq(7) d(6) rsvd(5) instance_id(4:0)
    byte2: hdr_ver(7:6)=00b  pldm_type(5:0)
    byte3: command code
    byte4..: request data
    (response) byte4 = completion code, byte5.. = response data

Header layout confirmed against the peer's OpenBIC source
(common/service/pldm/pldm.h, mcx-n9xx-evk full-board-port): 4-byte
header, `hdr_ver` and `pldm_type` SHARE byte 2 -- pldm_type is NOT the
whole byte.
"""

import struct

MSG_TYPE_PLDM = 0x01

# ----- PLDM types (GetPLDMTypes bitfield ids) -------------------------------
PLDM_TYPE_BASE = 0x00
PLDM_TYPE_SMBIOS = 0x01
PLDM_TYPE_PLATFORM = 0x02
PLDM_TYPE_BIOS = 0x03
PLDM_TYPE_FRU = 0x04
PLDM_TYPE_FWUP = 0x05
PLDM_TYPE_OEM = 0x3F

PLDM_TYPE_NAMES = {
    0x00: "base", 0x01: "SMBIOS", 0x02: "platform", 0x03: "BIOS",
    0x04: "FRU", 0x05: "firmware update", 0x06: "RDE", 0x3F: "OEM",
}

# ----- completion codes ---------------------------------------------------
CC_SUCCESS = 0x00
CC_ERROR = 0x01
CC_ERROR_INVALID_DATA = 0x02
CC_ERROR_INVALID_LENGTH = 0x03
CC_ERROR_NOT_READY = 0x04
CC_ERROR_UNSUPPORTED_PLDM_CMD = 0x05
CC_ERROR_INVALID_PLDM_TYPE = 0x20

# Command-specific completion codes (0x80+) mean different things per
# command -- these names are only unambiguous in the context of the
# command that returned them, so tests assert the raw value with a
# comment rather than relying on a single shared table.
#   GetPLDMVersion (DSP0240): 0x83 = INVALID_PLDM_TYPE_IN_REQUEST_DATA,
#     0x84 = INVALID_PLDM_VERSION_IN_REQUEST_DATA
#   GetPDR (DSP0248): 0x82 = INVALID_RECORD_HANDLE, 0x83 =
#     INVALID_RECORD_CHANGE_NUMBER
#   GetSensorReading / GetStateSensorReadings (DSP0248): 0x01 with an
#     INVALID_SENSOR_ID sense; SetStateEffecterStates: 0x80 =
#     INVALID_STATE_VALUE, 0x82 = UNSUPPORTED_EFFECTERSTATE
GETVER_INVALID_PLDM_TYPE = 0x83  # GetPLDMVersion, confirmed w/ peer 2026-08-27

CC_NAMES = {
    0x00: "SUCCESS", 0x01: "ERROR", 0x02: "INVALID_DATA",
    0x03: "INVALID_LENGTH", 0x04: "NOT_READY",
    0x05: "UNSUPPORTED_PLDM_CMD", 0x20: "INVALID_PLDM_TYPE",
}

# ----- base (type 0x00) commands ----------------------------------------
CMD_SET_TID = 0x01
CMD_GET_TID = 0x02
CMD_GET_PLDM_VERSION = 0x03
CMD_GET_PLDM_TYPES = 0x04
CMD_GET_PLDM_COMMANDS = 0x05

BASE_CMD_NAMES = {
    0x01: "SetTID", 0x02: "GetTID", 0x03: "GetPLDMVersion",
    0x04: "GetPLDMTypes", 0x05: "GetPLDMCommands",
}

# ----- platform (type 0x02, DSP0248) commands -------------------------
CMD_GET_SENSOR_READING = 0x11
CMD_GET_STATE_SENSOR_READINGS = 0x21
CMD_SET_STATE_EFFECTER_STATES = 0x39
CMD_GET_STATE_EFFECTER_STATES = 0x3A
CMD_GET_PDR_REPOSITORY_INFO = 0x50
CMD_GET_PDR = 0x51

PLATFORM_CMD_NAMES = {
    0x11: "GetSensorReading", 0x21: "GetStateSensorReadings",
    0x39: "SetStateEffecterStates", 0x3A: "GetStateEffecterStates",
    0x50: "GetPDRRepositoryInfo", 0x51: "GetPDR",
}

# GetPLDMVersion transfer flags.
XFER_GET_FIRST_PART = 0x01
XFER_FLAG_START = 0x01
XFER_FLAG_MIDDLE = 0x02
XFER_FLAG_END = 0x04
XFER_FLAG_START_AND_END = 0x05

# PDR types (DSP0248 table 79), the subset this suite parses.
PDR_TERMINUS_LOCATOR = 1
PDR_NUMERIC_SENSOR = 2
PDR_STATE_SENSOR = 4
PDR_NUMERIC_EFFECTER = 9
PDR_STATE_EFFECTER = 11
PDR_ENTITY_ASSOCIATION = 15
PDR_FRU_RECORD_SET = 20
PDR_OEM = 127

PDR_TYPE_NAMES = {
    1: "Terminus Locator", 2: "Numeric Sensor", 3: "Numeric Sensor Initialization",
    4: "State Sensor", 5: "State Sensor Initialization", 9: "Numeric Effecter",
    11: "State Effecter", 15: "Entity Association", 20: "FRU Record Set",
    127: "OEM",
}

# sensorDataSize enum (DSP0248): how presentReading / hysteresis etc. are sized.
_SDS_UNPACK = {
    0: ("<B", 1),   # uint8
    1: ("<b", 1),   # sint8
    2: ("<H", 2),   # uint16
    3: ("<h", 2),   # sint16
    4: ("<I", 4),   # uint32
    5: ("<i", 4),   # sint32
}


# =========================================================================
# request framing
# =========================================================================
def build_request(cmd, pldm_type, data=b"", inst_id=0):
    """Build a PLDM request message body (msg-type/IC byte onward)."""
    msg_type_ic = MSG_TYPE_PLDM & 0x7F          # ic = 0
    rq_d_inst = (1 << 7) | (inst_id & 0x1F)     # rq = 1, d = 0
    ver_type = (0 << 6) | (pldm_type & 0x3F)    # hdr_ver 00b
    return bytes([msg_type_ic, rq_d_inst, ver_type, cmd]) + bytes(data)


def parse_response(body):
    """Split a reassembled PLDM response body into header fields +
    completion code + trailing data."""
    if len(body) < 5:
        raise ValueError(f"PLDM response too short: {len(body)} bytes")
    if (body[0] & 0x7F) != MSG_TYPE_PLDM:
        raise ValueError(f"not a PLDM message (msg_type=0x{body[0] & 0x7F:02x})")
    if (body[1] >> 7) & 0x1:
        raise ValueError("rq bit set -- looks like a request, not a response")
    return {
        "inst_id": body[1] & 0x1F,
        "hdr_ver": (body[2] >> 6) & 0x3,
        "pldm_type": body[2] & 0x3F,
        "cmd": body[3],
        "completion_code": body[4],
        "cc_name": CC_NAMES.get(body[4], f"0x{body[4]:02x}"),
        "data": bytes(body[5:]),
    }


# ----- base command request/response helpers ---------------------------
def build_set_tid(tid, inst_id=0):
    return build_request(CMD_SET_TID, PLDM_TYPE_BASE, bytes([tid & 0xFF]), inst_id)


def build_get_tid(inst_id=0):
    return build_request(CMD_GET_TID, PLDM_TYPE_BASE, b"", inst_id)


def build_get_pldm_types(inst_id=0):
    return build_request(CMD_GET_PLDM_TYPES, PLDM_TYPE_BASE, b"", inst_id)


def build_get_pldm_commands(pldm_type, version_ver32=b"\xf1\xf0\xf0\x00", inst_id=0):
    return build_request(CMD_GET_PLDM_COMMANDS, PLDM_TYPE_BASE,
                         bytes([pldm_type & 0xFF]) + bytes(version_ver32), inst_id)


def build_get_pldm_version(pldm_type, inst_id=0):
    data = struct.pack("<I", 0) + bytes([XFER_GET_FIRST_PART, pldm_type & 0xFF])
    return build_request(CMD_GET_PLDM_VERSION, PLDM_TYPE_BASE, data, inst_id)


def decode_bitfield_ids(data, count):
    """Decode a 'supported X' bitfield (GetPLDMTypes: 8 bytes / 64 bits;
    GetPLDMCommands: 32 bytes / 256 bits). Bit N set => id N present."""
    ids = []
    for i in range(count):
        byte_i, bit_i = divmod(i, 8)
        if byte_i < len(data) and (data[byte_i] >> bit_i) & 1:
            ids.append(i)
    return ids


def decode_ver32(b):
    """Decode a 4-byte ver32 (packed BCD: major, minor, update, alpha).
    Returns 'M.m.u' (alpha char appended if present).

    Byte order on this platform is little-endian -- wire bytes are
    [alpha][update][minor][major] (confirmed live 2026-08-27:
    GetPLDMVersion base returned `00 f0 f0 f1` for PLDM 1.0.0).
    """
    if len(b) < 4:
        return b.hex(" ")
    alpha, update, minor, major = b[0], b[1], b[2], b[3]

    def bcd(x):
        if x == 0xFF:
            return None
        hi = (x >> 4) & 0x0F
        lo = x & 0x0F
        return lo if hi == 0x0F else hi * 10 + lo

    parts = [bcd(major), bcd(minor), bcd(update)]
    s = ".".join(str(p) for p in parts if p is not None)
    if alpha not in (0x00, 0xFF):
        s += chr(alpha)
    return s or b.hex(" ")


def parse_get_pldm_version_data(data):
    """GetPLDMVersion response data: NextDataTransferHandle(4) TransferFlag(1)
    then version data (one or more ver32) and, when the transfer ends
    here, a trailing CRC32. Returns list of version strings."""
    if len(data) < 5:
        return []
    flag = data[4]
    vers = data[5:]
    if flag in (XFER_FLAG_END, XFER_FLAG_START_AND_END) and len(vers) >= 4:
        vers = vers[:-4]  # drop trailing CRC32
    out = []
    for i in range(0, len(vers) - 3, 4):
        out.append(decode_ver32(vers[i:i + 4]))
    return out


# =========================================================================
# platform: PDR repository
# =========================================================================
def parse_pdr_repository_info(data):
    """GetPDRRepositoryInfo response data."""
    # data (after CC): repositoryState(1) updateTime(13) OEMUpdateTime(13)
    # recordCount(4) repositorySize(4) largestRecordSize(4)
    # dataTransferHandleTimeout(1) = 40 bytes.
    if len(data) < 40:
        return {"raw": data.hex(" ")}
    return {
        "repository_state": data[0],
        "record_count": struct.unpack_from("<I", data, 27)[0],
        "repository_size": struct.unpack_from("<I", data, 31)[0],
        "largest_record_size": struct.unpack_from("<I", data, 35)[0],
        "data_transfer_handle_timeout": data[39],
        "raw": data.hex(" "),
    }


def build_get_pdr(record_handle, data_transfer_handle=0, transfer_op_flag=XFER_GET_FIRST_PART,
                  request_count=0xFFFF, record_change_number=0, inst_id=0):
    data = (struct.pack("<I", record_handle)
            + struct.pack("<I", data_transfer_handle)
            + bytes([transfer_op_flag])
            + struct.pack("<H", request_count)
            + struct.pack("<H", record_change_number))
    return build_request(CMD_GET_PDR, PLDM_TYPE_PLATFORM, data, inst_id)


def parse_get_pdr_response(data):
    """GetPDR response data: nextRecordHandle(4) nextDataTransferHandle(4)
    transferFlag(1) responseCount(2) recordData(N) [transferCRC(1)]."""
    if len(data) < 11:
        raise ValueError(f"GetPDR response too short: {len(data)} bytes")
    next_handle = struct.unpack_from("<I", data, 0)[0]
    next_xfer = struct.unpack_from("<I", data, 4)[0]
    flag = data[8]
    count = struct.unpack_from("<H", data, 9)[0]
    record = data[11:11 + count]
    return {
        "next_record_handle": next_handle,
        "next_data_transfer_handle": next_xfer,
        "transfer_flag": flag,
        "response_count": count,
        "record": bytes(record),
    }


def parse_pdr_common_header(record):
    """Common PDR header (DSP0248): recordHandle(4) PDRHeaderVersion(1)
    PDRType(1) recordChangeNumber(2) dataLength(2)."""
    if len(record) < 10:
        raise ValueError(f"PDR record too short for common header: {len(record)}")
    return {
        "record_handle": struct.unpack_from("<I", record, 0)[0],
        "header_version": record[4],
        "pdr_type": record[5],
        "pdr_type_name": PDR_TYPE_NAMES.get(record[5], f"type {record[5]}"),
        "record_change_number": struct.unpack_from("<H", record, 6)[0],
        "data_length": struct.unpack_from("<H", record, 8)[0],
        "body": bytes(record[10:]),
    }


def parse_numeric_sensor_pdr(body):
    """Best-effort field extraction from a Numeric Sensor PDR body (the
    bytes after the 10-byte common header). Pulls what's needed to
    address the sensor and scale its reading: sensorID, entityType,
    sensorDataSize, and the resolution(m)/offset(b)/unitModifier scaling
    triple. Layout per DSP0248 table 80; offsets are within `body`."""
    if len(body) < 31:
        return {"raw": body.hex(" ")}
    out = {
        "terminus_handle": struct.unpack_from("<H", body, 0)[0],
        "sensor_id": struct.unpack_from("<H", body, 2)[0],
        "entity_type": struct.unpack_from("<H", body, 4)[0],
        "entity_instance": struct.unpack_from("<H", body, 6)[0],
        "container_id": struct.unpack_from("<H", body, 8)[0],
        "base_unit": body[12],
        "unit_modifier": struct.unpack_from("<b", body, 13)[0],
        "raw": body.hex(" "),
    }
    # DSP0248 table: after containerID (ends @10): sensorInit(1)
    # sensorAuxiliaryNamesPDR(1) baseUnit(1) unitModifier(1) rateUnit(1)
    # baseOEMUnitHandle(1) auxUnit(1) auxUnitModifier(1) auxRateUnit(1)
    # rel(1) auxOEMUnitHandle(1) isLinear(1) sensorDataSize(1)@22
    # resolution(f32)@23 offset(f32)@27 accuracy(2)@31 ...
    # Offset 22 confirmed live 2026-08-27 by cross-checking against
    # GetSensorReading's own sensorDataSize (=5) -- see
    # test_pldm_platform_sensors.py.
    try:
        out["sensor_data_size"] = body[22]
        out["resolution"] = struct.unpack_from("<f", body, 23)[0]
        out["offset"] = struct.unpack_from("<f", body, 27)[0]
    except struct.error:
        pass
    return out


def parse_state_sensor_pdr(body):
    """State Sensor PDR body: terminusHandle(2) sensorID(2) entityType(2)
    entityInstance(2) containerID(2) sensorInit(1) sensorAuxNamesPDR(1)
    compositeSensorCount(1) then per-sensor stateSetID(2) possibleStatesSize(1)
    possibleStates(N)."""
    if len(body) < 14:
        return {"raw": body.hex(" ")}
    out = {
        "sensor_id": struct.unpack_from("<H", body, 2)[0],
        "entity_type": struct.unpack_from("<H", body, 4)[0],
        "composite_sensor_count": body[13],
        "state_sets": [],
    }
    off = 14
    for _ in range(out["composite_sensor_count"]):
        if off + 3 > len(body):
            break
        state_set_id = struct.unpack_from("<H", body, off)[0]
        pss = body[off + 2]
        out["state_sets"].append({"state_set_id": state_set_id, "possible_states": body[off + 3: off + 3 + pss]})
        off += 3 + pss
    return out


def parse_state_effecter_pdr(body):
    """State Effecter PDR body: terminusHandle(2) effecterID(2) entityType(2)
    entityInstance(2) containerID(2) effecterSemanticID(2) effecterInit(1)
    effecterDescriptionPDR(1) compositeEffecterCount(1) then per-effecter
    stateSetID(2) possibleStatesSize(1) possibleStates(N)."""
    if len(body) < 15:
        return {"raw": body.hex(" ")}
    out = {
        "effecter_id": struct.unpack_from("<H", body, 2)[0],
        "entity_type": struct.unpack_from("<H", body, 4)[0],
        "composite_effecter_count": body[14],
        "state_sets": [],
    }
    off = 15
    for _ in range(out["composite_effecter_count"]):
        if off + 3 > len(body):
            break
        state_set_id = struct.unpack_from("<H", body, off)[0]
        pss = body[off + 2]
        out["state_sets"].append({"state_set_id": state_set_id, "possible_states": body[off + 3: off + 3 + pss]})
        off += 3 + pss
    return out


# =========================================================================
# platform: sensor / effecter reads
# =========================================================================
def build_get_sensor_reading(sensor_id, rearm=0, inst_id=0):
    return build_request(CMD_GET_SENSOR_READING, PLDM_TYPE_PLATFORM,
                         struct.pack("<H", sensor_id) + bytes([rearm & 0x01]), inst_id)


def parse_get_sensor_reading(data):
    """GetSensorReading response data: sensorDataSize(1) sensorOperationalState(1)
    sensorEventMessageEnable(1) presentState(1) previousState(1) eventState(1)
    presentReading(sensorDataSize)."""
    if len(data) < 6:
        raise ValueError(f"GetSensorReading response too short: {len(data)}")
    sds = data[0]
    fmt, width = _SDS_UNPACK.get(sds, ("<i", 4))
    raw = None
    if len(data) >= 6 + width:
        raw = struct.unpack_from(fmt, data, 6)[0]
    return {
        "sensor_data_size": sds,
        "operational_state": data[1],
        "event_message_enable": data[2],
        "present_state": data[3],
        "previous_state": data[4],
        "event_state": data[5],
        "present_reading_raw": raw,
    }


def scale_reading(raw, resolution, offset, unit_modifier):
    """DSP0248 numeric conversion: value = (raw * resolution + offset),
    then * 10**unitModifier."""
    if raw is None:
        return None
    val = raw * (resolution if resolution else 1.0) + (offset or 0.0)
    return val * (10 ** unit_modifier)


def build_get_state_sensor_readings(sensor_id, rearm=0, inst_id=0):
    return build_request(CMD_GET_STATE_SENSOR_READINGS, PLDM_TYPE_PLATFORM,
                         struct.pack("<H", sensor_id) + bytes([rearm & 0xFF, 0x00]), inst_id)


def parse_get_state_sensor_readings(data):
    """GetStateSensorReadings response data: compositeSensorCount(1) then
    per sensor: sensorOpState(1) presentState(1) previousState(1)
    eventState(1)."""
    if len(data) < 1:
        raise ValueError("GetStateSensorReadings response empty")
    n = data[0]
    fields = []
    off = 1
    for _ in range(n):
        if off + 4 > len(data):
            break
        fields.append({
            "op_state": data[off], "present_state": data[off + 1],
            "previous_state": data[off + 2], "event_state": data[off + 3],
        })
        off += 4
    return {"composite_sensor_count": n, "sensors": fields}


def build_set_state_effecter_states(effecter_id, states, inst_id=0):
    """`states` = list of (set_request, effecter_state); set_request 1 =
    "requestSet", 0 = "noChange"."""
    data = struct.pack("<H", effecter_id) + bytes([len(states)])
    for set_request, effecter_state in states:
        data += bytes([set_request & 0xFF, effecter_state & 0xFF])
    return build_request(CMD_SET_STATE_EFFECTER_STATES, PLDM_TYPE_PLATFORM, data, inst_id)


def build_get_state_effecter_states(effecter_id, inst_id=0):
    return build_request(CMD_GET_STATE_EFFECTER_STATES, PLDM_TYPE_PLATFORM,
                         struct.pack("<H", effecter_id), inst_id)


def parse_get_state_effecter_states(data):
    """GetStateEffecterStates response data: compositeEffecterCount(1) then
    per effecter: effecterOpState(1) pendingState(1) presentState(1)."""
    if len(data) < 1:
        raise ValueError("GetStateEffecterStates response empty")
    n = data[0]
    fields = []
    off = 1
    for _ in range(n):
        if off + 3 > len(data):
            break
        fields.append({
            "op_state": data[off], "pending_state": data[off + 1], "present_state": data[off + 2],
        })
        off += 3
    return {"composite_effecter_count": n, "effecters": fields}
