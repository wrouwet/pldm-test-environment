import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

from bridge import I2CBridge


@pytest.fixture(scope="session")
def bridge():
    """A connection to the FRDM-MCXA153 USB-to-I2C bridge, shared across tests."""
    b = I2CBridge()
    yield b
    b.close()


@pytest.fixture(autouse=True)
def _bus_settle():
    """Short settle between tests so the shared LPI2C target (IPMB 0x20 +
    the MCTP/PLDM/SPDM endpoint 0x10, one instance since the 2026-08-27
    bus consolidation, no clock-stretch backpressure) drains before the
    next test's first transaction. Removes transient NAK / listen-timeout
    noise. Run these suites SEQUENTIALLY -- never parallel across the one
    bus.
    """
    import time
    time.sleep(0.05)
    yield
