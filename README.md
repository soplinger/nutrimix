# nutrimix

**Offline hydroponic nutrient planner.** Write your reservoir, targets and fertiliser bags in one plain-text file. Get back exact grams per product, stock-tank weigh-outs, what the mix actually delivers in ppm, and a warning when your stock tanks would precipitate.

Standard library only. No network, no accounts, no API keys.

## Features

- **Plain-text recipe format**: one directive per line, `#` comments, nothing else to learn.
- **Line-numbered errors**: `recipe.nut:7: target K has no product that supplies K`, and file-level problems without a line.
- **Exact dose solver**: grams of each product to hit each element's ppm target, solved in file order.
- **Carryover accounting**: every product's contribution to every element is counted, so a nitrogen-heavy calcium source is charged against your nitrogen target.
- **Achieved vs target report**: per-element deviation in percent, flagged `ok`, `high` or `low` against a tolerance you set.
- **Growth stages**: `stage seedling 0.5` scales every target and re-solves the whole plan.
- **Stock tank planning**: concentrate factor (`100x`), tank volume, millilitres per reservoir and weigh-out grams for each product.
- **Precipitation guard**: flags any tank that mixes calcium with sulphate or phosphate sources.
- **Batch cost in exact cents**: per-product and total cost with half-up rounding; unpriced products are listed, not silently free.
- **Approximate EC**: total ppm divided by an adjustable `ec_factor`.
- **Refill runway**: days until the reservoir needs topping up, from your daily uptake.
- **Mixing steps**: a numbered batch sequence generated from your tanks.
- **JSON output** for scripts and dashboards with `plan --json`.
- **CI gate**: `check` exits `1` on parse errors, tank conflicts or off-target ppm.
- **Recipe diff**: `diff` compares achieved ppm and product grams between two recipes.

## Quick start

Save this as `lettuce.nut` (or use the copy in `examples/`):

```text
reservoir 50 L
uptake 2.5
ec_factor 640

target N 180
target Ca 160
target Mg 50
target S 65
target K 200
target P 40
target Fe 2

tank A 100x 2 L
tank B 100x 2 L
tank C 100x 1 L

product CalNit A Ca=19 N=15.5 price=4.20/kg feeds=Ca
product MKP B K=28.7 P=22.7 price=5.40/kg feeds=P
product MgSO4 B Mg=9.8 S=13 price=1.25/kg feeds=Mg
product KNO3 A K=38.2 N=13 price=3.10/kg feeds=K
product ChelFe C Fe=6 price=12.00/kg feeds=Fe

stage seedling 0.5
stage vegetative 1.0
```

Percentages come from your bag label. Products are solved in file order, so put the source that carries the most unwanted ions first.

Then run:

```bash
python3 nutrimix.py plan examples/lettuce.nut
```

The output has a dose table, an achieved-ppm table, the estimated EC, the tank weigh-outs, batch cost, mixing steps and any warnings, for each stage. Add `--stage vegetative` to see one stage, or `--json` for machine-readable output.

## Recipe reference

| Directive | Meaning |
| --- | --- |
| `reservoir <litres> L` | Reservoir volume (required) |
| `uptake <litres per day>` | Daily uptake, for the refill runway |
| `ec_factor <n>` | ppm per mS/cm used for the EC estimate (default 640) |
| `target <element> <ppm>` | Elemental target: N P K Ca Mg S Fe Mn B Zn Cu Mo |
| `tank <name> <factor>x [<litres> L]` | Stock concentrate, e.g. `tank A 100x 2 L` |
| `product <name> <tank> <El>=<pct> ... [price=<n>/kg] [feeds=<El>]` | Fertiliser product and its label percentages |
| `stage <name> <factor>` | Growth stage that scales all targets |

## Quick checks

```bash
python3 nutrimix.py check examples/lettuce.nut     # exit 0 when the mix is clean
python3 nutrimix.py diff examples/lettuce.nut other.nut
```

## Running

Requires Python 3.8 or newer. There is nothing to install for the planner itself.

```bash
python3 nutrimix.py --help
python3 nutrimix.py plan FILE [--stage NAME] [--json] [--tolerance 10]
python3 nutrimix.py check FILE [--tolerance 10]
python3 nutrimix.py diff FIRST SECOND
```

Exit codes: `plan` and `diff` return `0` on success and `1` on parse errors. `check` returns `1` on parse errors, tank conflicts or any element outside tolerance. `2` means the file could not be read.

## Testing

The tests use pytest. Install it once, then run from the project root:

```bash
python3 -m pip install pytest
python3 -m pytest -q
```

The suite covers the parser and its line numbers, the dose solver and carryover, stage scaling, tank weigh-outs, precipitation conflicts, cent-exact costs, EC, refill runway, the diff report, JSON output and the CLI exit codes. It also checks that the bundled example is clean.

## Layout

```text
nutrimix.py            the planner: parser, solver, reports and CLI
examples/lettuce.nut   a worked two-part lettuce recipe
tests/                 pytest suite
```

## Limits

- The solver is a sequential feed-in-order calculation, not a simultaneous optimiser. Where two products share an element, the second one overshoots or undershoots; the report shows you by how much.
- EC is an approximation (`total ppm / ec_factor`), not a meter reading.
- Costs are only as good as the prices you enter.
- Precipitation checks cover calcium with sulphate or phosphate only. Other incompatibilities are your responsibility.
