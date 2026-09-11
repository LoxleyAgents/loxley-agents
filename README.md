# Loxley agents

Six rule-based agents that read Robinhood Chain and decide whether to buy a
newly launched token. They trade on paper. Nothing here signs a transaction,
reads a key, or moves funds.

```bash
export RPC_URL=https://your-node.example    # any Robinhood Chain RPC, chain id 4663
python3 scan.py --blocks 4000 --limit 10
```

Standard library only. No install step, no API key, no indexer, no explorer.

## What it actually does

1. Reads `TokenLaunched` from the pons factory to find new tokens.
2. Works out where each one trades — its bonding curve, or the Uniswap v4
   pool manager once it has graduated — and computes volume, price, and how far
   the last print sits below the window's high.
3. Runs six sets of thresholds over that and returns either an entry or one of
   six refusals.
4. Records entries in `book.json` and closes them on take-profit, stop-loss or
   max hold.

## The agents

| agent | min volume | dip | take profit | stop loss | max hold |
|---|---|---|---|---|---|
| Friar Tuck | 600 | 8% | 14% | 10% | 1440m |
| Maid Marian | 350 | 10% | 20% | 8% | 180m |
| The Sheriff | 700 | 18% | 26% | 9% | 240m |
| Robin Hood | 250 | 12% | 35% | 15% | 360m |
| Little John | 400 | 22% | 40% | 18% | 720m |
| Will Scarlet | 80 | 15% | 35% | 12% | 30m |

A refusal is only ever one of these six, and every one is a statement about our
own thresholds:

`below volume floor` · `outside mcap range` · `dip not deep enough` ·
`listed too long ago` · `already in position` · `daily cap reached`

None of them is a claim about a token, its deployer, or anyone's intent. This
code reads public swap logs, which cannot tell you those things.

## What is not real here

Read this part before you read anything else.

- **The agents are not trading.** There is no live deployment behind this
  repository. An entry is a row in a JSON file.
- **Fills are assumed.** A real fill would face slippage, the snipe tax, and
  the creator fee. This model ignores all three, so paper results are better
  than reality by an amount this code does not measure.
- **The thresholds are defaults, not findings.** They were chosen for the
  characters, not fitted to data, and what they do depends entirely on which
  pools you happen to scan. On thin native-quoted pools every agent refuses
  everything for `below volume floor` — four tokens screened, twenty-four
  refusals, no entries. On a token quoted in USDG with 5,552 of volume and a
  48% dip, all six entered on those same defaults. Both outputs are correct.
  If the floors do not suit the market you are looking at, move them in
  `agents.json` (see `agents.json.example`) rather than editing the code.
- **Volume units do not compare.** Pools quote in native, GOOGL, NVDA, SGOV,
  DJT and others. A floor of `250` means 250 *of whatever that pool is quoted
  in*, so it only means something against tokens sharing a quote. There is no
  price feed here to convert between them.
- **Any performance figure shown on our website is a simulation** and is not
  produced by this code.
- **`--blocks` is a window, not history.** Volume and dip are measured over
  that window only, and a node will refuse a range that matches too many logs.

## How the chain was read

There is no published ABI in this repository because none was used. Event
signatures were resolved against openchain, and every field order was then
checked against the chain itself rather than assumed:

- `CurveBuy` carries `[quote in, token out, creator fee, protocol fee]`,
  `CurveSell` carries `[token in, quote out, …]` — the sell is **not** the
  mirror of the buy. Both orders were confirmed by matching each amount to the
  token's own `Transfer` in the same transaction.
- The creator fee runs 1% of the quote leg and the protocol fee 3%, except on
  trades that also emit `SnipeTaxCharged`.
- A Uniswap v4 `Swap` names a `poolId` and never a token, so a token is tied to
  its pool the only way the chain allows: a swap and a transfer of that token
  in one transaction, where a swap leg equals the amount that actually moved.
  That settles pool ownership and currency order together. Assuming either one
  produced wrong prices by nine orders of magnitude in testing.
- Most tokens never graduate — roughly 55 launches per 2,000 blocks against 34
  graduations per 40,000 — so a scanner that only reads v4 is blind to most of
  the chain.

If pons redeploys its factory these addresses go stale and the scan goes quiet.
That is deliberate. The alternative is guessing.

## Layout

```
loxley/rpc.py      JSON-RPC over the standard library, with log-range paging
loxley/erc20.py    name / symbol / decimals / supply, straight from eth_call
loxley/market.py   launches, pool resolution, volume and price in both phases
loxley/rules.py    the six agents and the six refusals
loxley/paper.py    positions, exits, journal
scan.py            the entry point
```

## Licence

MIT.
