# Codex working rules for this reference package

This package is a project reference for implementing and reviewing Chan-theory
strategy algorithms on a segment-based centre model. Read `README.md` first,
then read the files in `docs/` in numeric order.

## Language and scope

- Use Chinese for domain explanations, comments, test names, and user-facing
  output unless the surrounding repository already mandates English.
- This package describes structure recognition and strategy signals. It does
  not promise profitability and does not replace position sizing, execution,
  liquidity, slippage, fee, limit-up/limit-down, or contract-roll logic.
- Do not silently expand the scope to stock selection, forecasting, parameter
  optimisation, or live trading.

## Source precedence

When two statements appear to conflict, use this precedence order:

1. Explicit decisions under **Project hard rules** below.
2. Definitions and distinctions in `docs/02_domain_model.md`,
   `docs/03_center_detection.md`, and `docs/04_signal_rules.md`.
3. Machine-readable contracts in `specs/`.
4. Strategy suggestions in `docs/05_strategy_catalog.md`.
5. Summaries of the 108 lessons and the reconstructed discussion.

Never resolve a material ambiguity by guessing. Check
`docs/08_open_questions.md`, expose a configuration option if the ambiguity is
implementation-dependent, or ask the user.

## Project hard rules

1. The standard project path uses **segment-based centres**. A stroke-based
   centre is experimental and must carry a distinct `component_kind`; its
   signals must not be mixed with standard signals.
2. Every structure is level-qualified. A bare `centre`, `trend`, `B1`, `B2`,
   `B3`, `divergence`, or `segment` without `analysis_level` is invalid data.
3. A centre can be confirmed only from three consecutive **completed**
   components with a non-empty overlap.
4. A trend divergence needs at least two same-level, same-direction,
   non-overlapping centres. Without a trend there is no standard trend
   divergence.
5. The final trend leg must make a new directional extreme before a standard
   trend divergence can be confirmed. Otherwise classify only as a weaker
   structure or consolidation divergence.
6. MACD, moving averages, volume, BOLL, fractals, and `Zn` are auxiliary
   measurements or filters. None may bypass the structural prerequisites and
   directly manufacture a B1/B2/B3 signal.
7. A B2 need not be above a B1. If a buy-side B2 is below B1, or a sell-side B2
   is above S1, mark it `weak` and require consolidation-divergence plus
   lower-level completion evidence.
8. B3/S3 must wait for the **first completed retest** after a completed
   departure. This project uses strict outside conditions: B3 retest low
   `> ZG`; S3 retest high `< ZD`. Equality means contact with the closed centre
   and therefore centre extension, not B3/S3.
9. Same-level B1/B2 signals are not regenerated inside an established centre
   oscillation. Trades inside it use lower-level signals. The current centre
   ends only after a confirmed B3/S3 or promotion/expansion rule.
10. Every signal stores both the theoretical endpoint and the observable
    confirmation time. Backtests may trade only at or after `confirmed_at`.
11. Prices used in boundary comparisons are integer minimum-tick units. Do not
    use floating-point epsilon to turn contact into separation.
12. Unfinished segments, movement types, departure legs, retests, and
    divergence legs can emit candidates, never confirmed signals.

## Required event fields

All emitted structure and signal events must include at least:

- `event_id`
- `symbol`
- `analysis_level`
- `component_kind`
- `direction`
- `status` (`candidate`, `confirmed`, `invalidated`)
- `endpoint_time`
- `confirmed_at` (`null` while still only a candidate)
- `signal_price_ticks`
- `executable_price_ticks` when execution is modelled
- `evidence_ids`
- `rule_version`

Use `specs/signal_event.schema.json` as the validation contract.

## No-lookahead discipline

- Process input in timestamp order.
- Freeze the first-three-component centre core `[ZD, ZG]` once confirmed.
- Never use the eventual endpoint of an unfinished component at an earlier
  timestamp.
- A structural endpoint may be back-labelled for analysis, but a strategy may
  not act before `confirmed_at`.
- A MACD area for an unfinished leg is provisional. It can support
  `divergence_candidate`, not `confirmed`.
- Repainting must be represented as candidate invalidation or a new versioned
  event, never by mutating historical confirmed output silently.

## Implementation expectations

Before implementing a strategy:

1. State the analysis level and component kind.
2. State the completed-component rule.
3. Identify the current centre and the exact evidence components.
4. Separate structure, auxiliary indicator, risk filter, and execution logic.
5. Add invariant and boundary tests before performance tests.

At minimum, test:

- three completed components with and without overlap;
- equality at `ZD`/`ZG` versus strict B3/S3 separation;
- an unfinished retest that later re-enters the centre;
- trend divergence with one centre (must reject);
- trend divergence without a new extreme (must reject);
- B2 below B1 with and without consolidation divergence;
- theoretical endpoint earlier than confirmation time;
- integer-tick comparisons at one-tick distance;
- nine-component centre extension promotion;
- indicator agreement without structural prerequisites (must reject).

## File map

- `README.md`: entry point and suggested Codex usage.
- `docs/00_scope_and_provenance.md`: scope and evidence boundaries.
- `docs/01_conversation_digest.md`: discussion evolution and decisions.
- `docs/02_domain_model.md`: terminology and level model.
- `docs/03_center_detection.md`: segment-centre algorithm and state machine.
- `docs/04_signal_rules.md`: B1/B2/B3, divergences, centre oscillation.
- `docs/05_strategy_catalog.md`: executable strategy catalogue.
- `docs/06_indicator_roles.md`: MACD, MA, volume, BOLL, fractals, `Zn`.
- `docs/07_state_machine_and_backtest.md`: event flow and anti-lookahead rules.
- `docs/08_open_questions.md`: choices that still require project decisions.
- `docs/09_chart_notes.md`: notes for the three attached charts.
- `specs/`: machine-readable configuration and schemas.
- `refs/chanlun108_sources.md`: cited lesson index.
- `source/recovered_discussion.md`: reconstructed source discussion.
