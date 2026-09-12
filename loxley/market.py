"""What the chain says about a token: launches, pools, volume, price.

Everything here was derived by reading Robinhood Chain, not from an ABI file:
the event signatures were resolved against openchain, and the field order was
settled empirically (see the notes on each constant). If pons redeploys its
factory these addresses change and the scan goes quiet — that is deliberate,
the alternative is guessing.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

from .erc20 import Erc20, decode_address, decode_uint, words
from .rpc import Rpc

# pons v2 factory: emits one TokenLaunched per launch
FACTORY = "0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e"

# TokenLaunched(address,address,address,address,uint256,uint256)
# topics: [sig, token, pool, deployer] — deployer confirmed by matching tx.from
# data:   [quote, 0, threshold] — threshold is denominated in the quote asset
TOKEN_LAUNCHED = "0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607"

# PoolGraduated(address,uint256,uint256,uint256) — subject is the token itself
POOL_GRADUATED = "0x0a44ef75df69c534f43cd6c1aa3ef8983065fe5fe79ef9e79f6494e6f258c259"

# the Uniswap v4 singleton: every graduated token trades through this one address
POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"

# Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)
# keyed by poolId, so a token is tied to its pool by correlating transactions
V4_SWAP = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

WAD = 10 ** 18


def sint(word: str) -> int:
    """Signed ABI values arrive sign-extended across the whole 32-byte word."""
    v = int(word, 16)
    return v - (1 << 256) if v >= (1 << 255) else v


@dataclass
class Launch:
    token: str
    pool: str
    deployer: str
    quote: str
    threshold: float
    block: int


@dataclass
class Market:
    token: str
    trades: int
    buys: int
    sells: int
    quote_volume: float
    first_price: float
    last_price: float
    high: float
    low: float

    @property
    def change_pct(self) -> float:
        if not self.first_price:
            return 0.0
        return (self.last_price / self.first_price - 1) * 100

    @property
    def dip_from_high_pct(self) -> float:
        """How far the last print sits below the window's high."""
        if not self.high:
            return 0.0
        return (1 - self.last_price / self.high) * 100


def recent_launches(rpc: Rpc, blocks: int = 3000, head: int | None = None) -> list[Launch]:
    head = head or rpc.block_number()
    logs = rpc.logs_chunked(FACTORY, head - blocks, head, topics=[TOKEN_LAUNCHED])
    out = []
    for log in logs:
        data = words(log["data"])
        out.append(Launch(
            token=decode_address(log["topics"][1]),
            pool=decode_address(log["topics"][2]),
            deployer=decode_address(log["topics"][3]),
            quote=decode_address(data[0]),
            threshold=decode_uint("0x" + data[2]) / WAD,
            block=int(log["blockNumber"], 16),
        ))
    return out


def _matches(swap_amount: int, transfer_amounts: list[int]) -> bool:
    """Does a swap leg account for the token that moved in the same transaction?

    Compared against each transfer and against their sum, because the creator
    fee arrives as a second transfer: a trade of 10,823,585 shows up as
    108,235 + 10,715,349.
    """
    if swap_amount == 0:
        return False
    candidates = list(transfer_amounts) + [sum(transfer_amounts)]
    return any(abs(swap_amount - c) <= max(1, c // 10 ** 9) for c in candidates)


def resolve_pool(rpc: Rpc, token: str, blocks: int = 300,
                 head: int | None = None) -> tuple[str, bool] | None:
    """Tie a token to its own v4 pool, and work out which side it sits on.

    A v4 Swap names a poolId and never a token, so the only link the chain
    offers is a swap and a transfer of the token in the same transaction. That
    alone is not enough: a routed trade also touches unrelated pools, and on a
    thin token those hops can outnumber its own. So a pool only counts when a
    swap leg equals the token amount that actually moved — which settles pool
    ownership and currency order in one pass, instead of assuming either.

    Returns (poolId, token_is_amount0) or None when nothing matches.
    """
    head = head or rpc.block_number()
    lo = head - blocks

    transfers = rpc.logs_chunked(token, lo, head, topics=[TRANSFER])
    if not transfers:
        return None
    moved: dict[str, list[int]] = collections.defaultdict(list)
    for t in transfers:
        moved[t["transactionHash"]].append(decode_uint(t["data"]))

    tally: collections.Counter = collections.Counter()
    for s in rpc.logs_chunked(POOL_MANAGER, lo, head, topics=[V4_SWAP]):
        amounts = moved.get(s["transactionHash"])
        if not amounts:
            continue
        w = words(s["data"])
        a0, a1 = abs(sint(w[0])), abs(sint(w[1]))
        if _matches(a0, amounts):
            tally[(s["topics"][1], True)] += 1
        elif _matches(a1, amounts):
            tally[(s["topics"][1], False)] += 1

    if not tally:
        return None
    (pool_id, token_is_0), _ = tally.most_common(1)[0]
    return pool_id, token_is_0


def resolve_pools(rpc: Rpc, token: str, blocks: int = 300,
                  head: int | None = None, min_hits: int = 3
                  ) -> list[tuple[str, bool]]:
    """Every v4 pool this token trades in, busiest first.

    One token can have several pools — on a chain where the quote asset is a
    choice, the same token may be quoted against more than one currency. Taking
    only the busiest pool undercounts both volume and the latest price.
    """
    head = head or rpc.block_number()
    lo = head - blocks

    transfers = rpc.logs_chunked(token, lo, head, topics=[TRANSFER])
    if not transfers:
        return []
    moved: dict[str, list[int]] = collections.defaultdict(list)
    for t in transfers:
        moved[t["transactionHash"]].append(decode_uint(t["data"]))

    tally: collections.Counter = collections.Counter()
    for s in rpc.logs_chunked(POOL_MANAGER, lo, head, topics=[V4_SWAP]):
        amounts = moved.get(s["transactionHash"])
        if not amounts:
            continue
        w = words(s["data"])
        a0, a1 = abs(sint(w[0])), abs(sint(w[1]))
        if _matches(a0, amounts):
            tally[(s["topics"][1], True)] += 1
        elif _matches(a1, amounts):
            tally[(s["topics"][1], False)] += 1

    # One pool must not appear twice with opposite sides: a single coincidental
    # match would otherwise be trusted as much as hundreds of real ones, and the
    # wrong side reads the token amount as the quote, inflating volume wildly.
    best: dict[str, tuple[bool, int]] = {}
    for (pool_id, token_is_0), hits in tally.items():
        if pool_id not in best or hits > best[pool_id][1]:
            best[pool_id] = (token_is_0, hits)

    # and a pool needs more than a single chance match to be believed
    keep = [(pid, side) for pid, (side, hits) in best.items() if hits >= min_hits]
    keep.sort(key=lambda k: -best[k[0]][1])
    return keep


def market_stats(rpc: Rpc, token: str, quote: str | None = None,
                 blocks: int = 300, head: int | None = None,
                 resolve_blocks: int = 300) -> Market | None:
    """Volume and price for a token over a window, straight from swap logs.

    Price is the ratio of the two amounts in each swap rather than anything
    derived from sqrtPriceX96, so it needs no assumption about tick maths.

    Two windows, on purpose. Identifying the pool means correlating swaps with
    transfers, which cannot be filtered server-side, so that runs over a narrow
    `resolve_blocks`. Once the poolId is known it goes into the topic filter and
    the node returns only this pool's swaps, which makes a wide `blocks` cheap.
    Filtering those in Python instead pulls every swap on the chain: the pool
    manager is the busiest contract on it.
    """
    head = head or rpc.block_number()
    lo = head - blocks

    # Pool discovery is a correlation scan and cannot be filtered server-side,
    # so it runs on one narrow window near the head. A pool that was busy early
    # and has since gone quiet will be missed — see the note in the README.
    pools = resolve_pools(rpc, token, min(resolve_blocks, blocks), head)
    if not pools:
        return None

    token_dec = Erc20(rpc, token).decimals() or 18
    quote_dec = Erc20(rpc, quote).decimals() if quote and int(quote, 16) else 18

    fills: list[tuple[int, int, float, float, bool]] = []
    # poolId is indexed, so the node does the filtering, one pool at a time
    for pool_id, token_is_0 in pools:
        for s in rpc.logs_chunked(POOL_MANAGER, lo, head,
                                  topics=[V4_SWAP, pool_id], span=20000):
            w = words(s["data"])
            a0, a1 = sint(w[0]), sint(w[1])
            raw_token, raw_quote = (a0, a1) if token_is_0 else (a1, a0)
            if raw_token == 0 or raw_quote == 0:
                continue
            token_amt = raw_token / 10 ** token_dec
            quote_amt = raw_quote / 10 ** (quote_dec or 18)
            fills.append((int(s["blockNumber"], 16), int(s["logIndex"], 16),
                          abs(quote_amt) / abs(token_amt), abs(quote_amt),
                          raw_token < 0))

    if not fills:
        return None

    # pools are read one after another, so put every fill back in chain order
    fills.sort(key=lambda f: (f[0], f[1]))
    prices = [f[2] for f in fills]
    volume = sum(f[3] for f in fills)
    buys = sum(1 for f in fills if f[4])
    sells = len(fills) - buys

    return Market(
        token=token,
        trades=len(prices),
        buys=buys,
        sells=sells,
        quote_volume=volume,
        first_price=prices[0],
        last_price=prices[-1],
        high=max(prices),
        low=min(prices),
    )


# --- the bonding-curve phase -------------------------------------------------
#
# Most tokens never graduate: the factory emitted ~55 launches per 2,000 blocks
# against 34 graduations in 40,000. Until a pool crosses its threshold it trades
# on its own contract and never touches the v4 manager, so a scanner that only
# reads v4 is blind to the large majority of the chain.
#
# CurveBuy(address,address,uint256,uint256,uint256,uint256)
#   data: [quote in, token out, creator fee, protocol fee]
# CurveSell(address,address,uint256,uint256,uint256,uint256)
#   data: [token in, quote out, creator fee, protocol fee]
#
# The sell is NOT the mirror of the buy — the token is data[1] on a buy and
# data[0] on a sell. Both orders were checked against the token's own Transfer
# amounts in the same transaction, which matched exactly every time.
#
# The fee words run 1% and 3% of the quote leg. The 1% is the creator fee and
# it jumps on trades that also emit SnipeTaxCharged.
CURVE_BUY = "0xec36bf571f136799e8dc0b0b8bea4b04d8bd3d43de838aab0d5fc21d4cbfc455"
CURVE_SELL = "0x8113d738abdcb6b38357e9d53a54a7157861a09031b453651f0fe7fe151f59df"
FEES_SWEPT = "0x9f4cd7c4ed99d08a797804560c9c5d71d2cf7e101f2e3b5e7d1ca8a24c370e4f"
SNIPE_TAX_CHARGED = "0x3bc39a5562b28f5fe8f36cecabfbaa12bb969acf05717994709225fc412a9934"


def curve_stats(rpc: Rpc, token: str, pool: str, quote: str | None = None,
                blocks: int = 3000, head: int | None = None,
                from_block: int | None = None, to_block: int | None = None
                ) -> Market | None:
    """Volume and price for a token still trading on its bonding curve.

    Pass `from_block`/`to_block` to read only the span the curve actually
    existed for. Without them the scan runs the whole `blocks` window, which
    for a token that graduated in seconds means thousands of pointless queries.
    """
    head = head or rpc.block_number()
    lo = from_block if from_block is not None else head - blocks
    hi = to_block if to_block is not None else head

    token_dec = Erc20(rpc, token).decimals() or 18
    quote_dec = (Erc20(rpc, quote).decimals() or 18) if quote and int(quote, 16) else 18

    events = rpc.logs_chunked(pool, lo, hi, topics=[[CURVE_BUY, CURVE_SELL]])
    events.sort(key=lambda e: (int(e["blockNumber"], 16), int(e["logIndex"], 16)))

    prices: list[float] = []
    volume = 0.0
    buys = sells = 0
    for e in events:
        w = words(e["data"])
        if len(w) < 2:
            continue
        is_buy = e["topics"][0] == CURVE_BUY
        raw_quote = decode_uint("0x" + (w[0] if is_buy else w[1]))
        raw_token = decode_uint("0x" + (w[1] if is_buy else w[0]))
        if not raw_token or not raw_quote:
            continue

        token_amt = raw_token / 10 ** token_dec
        quote_amt = raw_quote / 10 ** quote_dec
        prices.append(quote_amt / token_amt)
        volume += quote_amt
        if is_buy:
            buys += 1
        else:
            sells += 1

    if not prices:
        return None

    return Market(
        token=token,
        trades=len(prices),
        buys=buys,
        sells=sells,
        quote_volume=volume,
        first_price=prices[0],
        last_price=prices[-1],
        high=max(prices),
        low=min(prices),
    )


def stats_for(rpc: Rpc, launch: Launch, blocks: int = 3000,
              head: int | None = None, v4_blocks: int | None = None
              ) -> tuple[Market | None, str]:
    """Read a token across both phases and add them together.

    A token trades on its bonding curve, graduates, and then trades in the
    Uniswap v4 pool. Both windows can hold real volume for the same token, so
    reading only the first one that answers under-reports badly: PAIREX
    graduated 253 blocks — about 26 seconds — after launch, which left 11,063
    USDG on the curve against roughly six million traded afterwards.

    Returns (market, phase) where phase is "curve", "graduated",
    "curve+graduated" or "quiet".

    The v4 pool manager is the busiest contract on the chain, so its window is
    separate: widen `v4_blocks` deliberately rather than by accident.
    """
    head = head or rpc.block_number()
    if v4_blocks is None:
        v4_blocks = blocks

    # each phase is read only over the span it existed for
    grad = graduated_at(rpc, launch.token, head)
    on_curve = curve_stats(rpc, launch.token, launch.pool, launch.quote,
                           blocks, head,
                           from_block=launch.block,
                           to_block=grad if grad else head)
    v4_from = grad if grad else head - v4_blocks
    on_v4 = market_stats(rpc, launch.token, launch.quote,
                         blocks=max(1, head - v4_from), head=head)

    if on_curve and on_v4:
        return Market(
            token=launch.token,
            trades=on_curve.trades + on_v4.trades,
            buys=on_curve.buys + on_v4.buys,
            sells=on_curve.sells + on_v4.sells,
            quote_volume=on_curve.quote_volume + on_v4.quote_volume,
            # the curve runs first, the pool last
            first_price=on_curve.first_price,
            last_price=on_v4.last_price,
            high=max(on_curve.high, on_v4.high),
            low=min(on_curve.low, on_v4.low),
        ), "curve+graduated"

    if on_curve:
        return on_curve, "curve"
    if on_v4:
        return on_v4, "graduated"
    return None, "quiet"


def graduated_at(rpc: Rpc, token: str, head: int | None = None) -> int | None:
    """Block at which the token left its curve, or None if it never did."""
    head = head or rpc.block_number()
    pad = "0x" + token[2:].lower().rjust(64, "0")
    logs = rpc.logs(FACTORY, 0, head, topics=[POOL_GRADUATED, pad])
    return int(logs[0]["blockNumber"], 16) if logs else None
