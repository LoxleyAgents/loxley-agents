"""Minimal JSON-RPC client for an EVM chain.

Standard library only — cloning this repo does not require pip install.
The endpoint is never hardcoded: set RPC_URL in the environment.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 20

# Public nodes commonly sit behind a CDN that 403s the stock "Python-urllib/x.y"
# agent. Identify the client by name rather than pretending to be a browser.
USER_AGENT = "loxley-agents/0.1 (+https://github.com/LoxleyAgents)"


class RpcError(RuntimeError):
    """The node answered, but with an error object."""


class Rpc:
    def __init__(self, url: str | None = None, timeout: int = DEFAULT_TIMEOUT):
        self.url = url or os.environ.get("RPC_URL", "")
        if not self.url:
            raise SystemExit(
                "No RPC endpoint. Set RPC_URL, for example:\n"
                "  export RPC_URL=https://your-node.example\n"
                "Any Robinhood Chain RPC works; the chain id must be 4663."
            )
        self.timeout = timeout
        self._id = 0

    def call(self, method: str, params: list | None = None, retries: int = 2):
        self._id += 1
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}
        ).encode()
        req = urllib.request.Request(
            self.url,
            data=payload,
            # some public nodes reject the default Python-urllib agent outright,
            # so identify the client honestly instead of leaving it unset
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )

        last = None
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read())
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
                if attempt == retries:
                    raise RuntimeError(f"{method} failed: {exc}") from exc
                time.sleep(1.5 * (attempt + 1))
        else:  # pragma: no cover - loop always breaks or raises
            raise RuntimeError(f"{method} failed: {last}")

        if "error" in body:
            raise RpcError(f"{method}: {body['error']}")
        return body["result"]

    # --- the handful of methods the agents actually use -------------------

    def chain_id(self) -> int:
        return int(self.call("eth_chainId"), 16)

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber"), 16)

    def block(self, number: int, full: bool = False) -> dict:
        return self.call("eth_getBlockByNumber", [hex(number), full])

    def logs(self, address: str | list[str] | None, from_block: int, to_block: int,
             topics: list | None = None) -> list[dict]:
        flt: dict = {"fromBlock": hex(from_block), "toBlock": hex(to_block)}
        if address:
            flt["address"] = address
        if topics:
            flt["topics"] = topics
        return self.call("eth_getLogs", [flt])

    def logs_chunked(self, address: str | list[str] | None, from_block: int,
                     to_block: int, topics: list | None = None,
                     span: int = 400, min_span: int = 25) -> list[dict]:
        """eth_getLogs across a wide range, in pieces the node will accept.

        Robinhood Chain caps a single query at 10,000 logs and the busiest
        contract on it (the Uniswap v4 pool manager) can pass that in a few
        hundred blocks, so any honest scan has to page and back off.
        """
        out: list[dict] = []
        start = from_block
        while start <= to_block:
            step = span
            while True:
                end = min(start + step - 1, to_block)
                try:
                    out.extend(self.logs(address, start, end, topics))
                    break
                except RpcError as exc:
                    if "exceeds" not in str(exc).lower() and "limit" not in str(exc).lower():
                        raise
                    step //= 2
                    if step < min_span:
                        raise RuntimeError(
                            f"blocks {start}-{end} stay over the node's log limit"
                        ) from exc
            start = end + 1
        return out

    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        return self.call("eth_call", [{"to": to, "data": data}, block])
