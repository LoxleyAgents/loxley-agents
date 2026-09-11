"""Read ERC-20 metadata straight from the contracts.

No explorer, no indexer, no API key: every field here comes from an eth_call
against the node you configured. Selectors are the well-known constants, so
nothing in this module needs a keccak implementation.
"""

from __future__ import annotations

from .rpc import Rpc, RpcError

SEL_NAME = "0x06fdde03"
SEL_SYMBOL = "0x95d89b41"
SEL_DECIMALS = "0x313ce567"
SEL_TOTAL_SUPPLY = "0x18160ddd"


def _clean(word: str) -> str:
    return word[2:] if word.startswith("0x") else word


def decode_uint(raw: str) -> int:
    body = _clean(raw)
    return int(body, 16) if body else 0


def decode_address(word: str) -> str:
    """A 32-byte word holding a left-padded address."""
    return "0x" + _clean(word)[-40:]


def decode_string(raw: str) -> str:
    """Handle both `string` returns and the older fixed bytes32 form."""
    body = _clean(raw)
    if not body:
        return ""

    # bytes32: a single word, trailing zero padding
    if len(body) == 64:
        return bytes.fromhex(body).rstrip(b"\x00").decode("utf-8", "replace").strip()

    # string: offset word, length word, then the payload
    try:
        offset = int(body[0:64], 16) * 2
        length = int(body[offset:offset + 64], 16) * 2
        payload = body[offset + 64:offset + 64 + length]
        return bytes.fromhex(payload).decode("utf-8", "replace").strip()
    except (ValueError, IndexError):
        return ""


def words(raw: str) -> list[str]:
    """Split ABI-encoded return data into 32-byte words."""
    body = _clean(raw)
    return [body[i:i + 64] for i in range(0, len(body), 64)]


class Erc20:
    def __init__(self, rpc: Rpc, address: str):
        self.rpc = rpc
        self.address = address

    def _read(self, selector: str) -> str | None:
        try:
            return self.rpc.eth_call(self.address, selector)
        except (RpcError, RuntimeError):
            return None

    @property
    def is_token(self) -> bool:
        """True when the address answers the mandatory ERC-20 reads."""
        d = self._read(SEL_DECIMALS)
        s = self._read(SEL_SYMBOL)
        return bool(d) and d != "0x" and bool(s) and s != "0x"

    def symbol(self) -> str:
        return decode_string(self._read(SEL_SYMBOL) or "")

    def name(self) -> str:
        return decode_string(self._read(SEL_NAME) or "")

    def decimals(self) -> int:
        return decode_uint(self._read(SEL_DECIMALS) or "0x0")

    def total_supply(self) -> int:
        return decode_uint(self._read(SEL_TOTAL_SUPPLY) or "0x0")

    def describe(self) -> dict:
        dec = self.decimals()
        supply = self.total_supply()
        return {
            "address": self.address,
            "symbol": self.symbol(),
            "name": self.name(),
            "decimals": dec,
            "supply": supply / (10 ** dec) if dec else supply,
        }
