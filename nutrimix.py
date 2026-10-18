#!/usr/bin/env python3
"""nutrimix: offline hydroponic nutrient planner.

Reads one plain-text recipe, solves how many grams of each fertiliser
product hit the target ppm for every growth stage, checks what the
mix actually delivers, plans stock-tank weigh-outs and flags tanks
that will precipitate. Standard library only, no network.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ELEMENTS = ("N", "P", "K", "Ca", "Mg", "S", "Fe", "Mn", "B", "Zn", "Cu", "Mo")
DEFAULT_EC_FACTOR = 640.0
DEFAULT_TOLERANCE = 10.0
# Calcium must never share a tank with sulphate or phosphate sources.
PRECIPITATION_PAIRS = (("Ca", "S"), ("Ca", "P"))

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_KEY_VALUE_RE = re.compile(r"^([A-Za-z]+)=(\S+)$")
_PRICE_RE = re.compile(r"^(\d+(?:\.\d+)?)/kg$")
_CENT = Decimal("0.01")


@dataclass
class ParseError:
    line: int | None
    message: str

    def render(self, path: str) -> str:
        if self.line is None:
            return f"{path}: {self.message}"
        return f"{path}:{self.line}: {self.message}"


@dataclass
class Tank:
    name: str
    factor: float
    volume: float
    line: int


@dataclass
class Product:
    name: str
    tank: str
    pct: dict
    price: Decimal | None
    feeds: str | None
    line: int


@dataclass
class Recipe:
    reservoir: float | None = None
    ec_factor: float = DEFAULT_EC_FACTOR
    uptake: float | None = None
    targets: dict = field(default_factory=dict)
    target_lines: dict = field(default_factory=dict)
    tanks: dict = field(default_factory=dict)
    products: list = field(default_factory=list)
    stages: list = field(default_factory=list)  # (name, factor, line)

    def stage_list(self):
        if self.stages:
            return [(name, factor) for name, factor, _ in self.stages]
        return [("base", 1.0)]


def _positive(token):
    try:
        value = float(token)
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _directive_reservoir(recipe, args, line):
    if len(args) != 2 or args[1] != "L":
        return "expected: reservoir <litres> L"
    volume = _positive(args[0])
    if volume is None:
        return "reservoir volume must be a number above 0"
    if recipe.reservoir is not None:
        return "reservoir is already set"
    recipe.reservoir = volume
    return None


def _directive_ec_factor(recipe, args, line):
    if len(args) != 1 or _positive(args[0]) is None:
        return "expected: ec_factor <ppm per mS/cm, above 0>"
    recipe.ec_factor = _positive(args[0])
    return None


def _directive_uptake(recipe, args, line):
    if len(args) != 1 or _positive(args[0]) is None:
        return "expected: uptake <litres per day, above 0>"
    recipe.uptake = _positive(args[0])
    return None


def _directive_target(recipe, args, line):
    if len(args) != 2:
        return "expected: target <element> <ppm>"
    element, value = args
    if element not in ELEMENTS:
        return f"unknown element {element!r}; known: {', '.join(ELEMENTS)}"
    ppm = _positive(value)
    if ppm is None:
        return f"target for {element} must be a number above 0"
    if element in recipe.targets:
        return f"target {element} already set on line {recipe.target_lines[element]}"
    recipe.targets[element] = ppm
    recipe.target_lines[element] = line
    return None


def _directive_tank(recipe, args, line):
    if len(args) not in (2, 4):
        return "expected: tank <name> <factor>x [<litres> L]"
    name, factor_token = args[0], args[1]
    if not _NAME_RE.match(name):
        return f"invalid tank name {name!r}"
    if not factor_token.endswith("x") or _positive(factor_token[:-1]) is None:
        return "tank factor must look like 100x"
    volume = 1.0
    if len(args) == 4:
        if args[3] != "L" or _positive(args[2]) is None:
            return "tank volume must look like 2 L"
        volume = _positive(args[2])
    if name in recipe.tanks:
        return f"tank {name} is already defined on line {recipe.tanks[name].line}"
    recipe.tanks[name] = Tank(name, _positive(factor_token[:-1]), volume, line)
    return None


def _directive_product(recipe, args, line):
    if len(args) < 3:
        return "expected: product <name> <tank> <Element>=<percent> ..."
    name, tank = args[0], args[1]
    if not _NAME_RE.match(name):
        return f"invalid product name {name!r}"
    pct, price, feeds = {}, None, None
    for token in args[2:]:
        match = _KEY_VALUE_RE.match(token)
        if not match:
            return f"expected key=value, got {token!r}"
        key, value = match.group(1), match.group(2)
        if key == "price":
            price_match = _PRICE_RE.match(value)
            if not price_match:
                return "price must look like price=4.20/kg"
            price = Decimal(price_match.group(1))
        elif key == "feeds":
            if value not in ELEMENTS:
                return f"feeds names an unknown element {value!r}"
            feeds = value
        elif key in ELEMENTS:
            if key in pct:
                return f"element {key} given twice"
            percent = _positive(value)
            if percent is None or percent > 100:
                return f"{key} must be a percentage above 0 and up to 100"
            pct[key] = percent
        else:
            return f"unknown element or option {key!r}"
    if not pct:
        return "product needs at least one element percentage"
    if feeds is not None and feeds not in pct:
        return f"feeds={feeds} but the product contains no {feeds}"
    recipe.products.append(Product(name, tank, pct, price, feeds, line))
    return None


def _directive_stage(recipe, args, line):
    if len(args) != 2 or not _NAME_RE.match(args[0]) or _positive(args[1]) is None:
        return "expected: stage <name> <factor above 0>"
    if any(name == args[0] for name, _, _ in recipe.stages):
        return f"stage {args[0]} is already defined"
    recipe.stages.append((args[0], _positive(args[1]), line))
    return None


_DIRECTIVES = {
    "reservoir": _directive_reservoir,
    "ec_factor": _directive_ec_factor,
    "uptake": _directive_uptake,
    "target": _directive_target,
    "tank": _directive_tank,
    "product": _directive_product,
    "stage": _directive_stage,
}


def _validate(recipe, errors):
    if recipe.reservoir is None:
        errors.append(ParseError(None, "missing 'reservoir <litres> L' line"))
    if not recipe.targets:
        errors.append(ParseError(None, "no 'target' lines declared"))
    if not recipe.products:
        errors.append(ParseError(None, "no 'product' lines declared"))

    seen = {}
    for product in recipe.products:
        if product.name in seen:
            errors.append(ParseError(product.line, f"product {product.name} already defined on line {seen[product.name]}"))
        seen.setdefault(product.name, product.line)
        if product.tank not in recipe.tanks:
            errors.append(ParseError(product.line, f"product {product.name} uses undefined tank {product.tank!r}"))
        if product.feeds is not None and product.feeds not in recipe.targets:
            errors.append(ParseError(product.line, f"feeds={product.feeds} but there is no target for {product.feeds}"))

    for element, line in recipe.target_lines.items():
        if not any(element in product.pct for product in recipe.products):
            errors.append(ParseError(line, f"target {element} has no product that supplies {element}"))


def parse_recipe(text):
    """Return (recipe, errors). Errors are sorted by line number."""
    recipe = Recipe()
    errors = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        body = raw.split("#", 1)[0].strip()
        if not body:
            continue
        words = body.split()
        keyword, args = words[0], words[1:]
        handler = _DIRECTIVES.get(keyword)
        if handler is None:
            errors.append(ParseError(line_no, f"unknown directive {keyword!r}"))
            continue
        problem = handler(recipe, args, line_no)
        if problem:
            errors.append(ParseError(line_no, problem))
    _validate(recipe, errors)
    errors.sort(key=lambda e: (e.line is not None, e.line or 0))
    return recipe, errors


def _cost(grams, price):
    if price is None:
        return None
    exact = Decimal(f"{grams:.6f}") * price / Decimal(1000)
    return exact.quantize(_CENT, rounding=ROUND_HALF_UP)


def find_conflicts(recipe):
    """Describe tanks that combine calcium with sulphate or phosphate."""
    found = []
    for tank in recipe.tanks.values():
        members = [p for p in recipe.products if p.tank == tank.name]
        for source, anion in PRECIPITATION_PAIRS:
            sources = [p.name for p in members if source in p.pct]
            anions = [p.name for p in members if anion in p.pct]
            if sources and anions:
                found.append(
                    f"tank {tank.name} mixes {source} ({', '.join(sources)}) "
                    f"with {anion} ({', '.join(anions)}): precipitation risk, "
                    f"move one of them to another tank"
                )
    return found


def _stage_plan(recipe, name, factor, tolerance):
    volume = recipe.reservoir
    targets = {el: ppm * factor for el, ppm in recipe.targets.items()}
    remaining = dict(targets)
    doses = []
    for product in recipe.products:
        feed = product.feeds or next((el for el in product.pct if el in targets), None)
        grams = 0.0
        if feed is not None and remaining[feed] > 0:
            # 1 g of a product at P percent gives P * 10 mg of that element.
            grams = remaining[feed] * volume / (product.pct[feed] * 10)
        supplied = {el: grams * pct * 10 / volume for el, pct in product.pct.items()}
        for el in targets:
            remaining[el] -= supplied.get(el, 0.0)
        doses.append({
            "product": product.name,
            "tank": product.tank,
            "feeds": feed,
            "grams": grams,
            "cost": _cost(grams, product.price),
            "supplied": supplied,
        })

    achieved = {el: sum(d["supplied"].get(el, 0.0) for d in doses) for el in targets}
    deviation = {el: (achieved[el] - targets[el]) / targets[el] * 100 for el in targets}
    status = {}
    for el, dev in deviation.items():
        if abs(dev) <= tolerance:
            status[el] = "ok"
        else:
            status[el] = "high" if dev > 0 else "low"

    total_ppm = sum(sum(d["supplied"].values()) for d in doses)
    priced = [d["cost"] for d in doses if d["cost"] is not None]
    cost_total = sum(priced, Decimal("0")).quantize(_CENT, rounding=ROUND_HALF_UP)

    tanks = []
    for tank in recipe.tanks.values():
        members = [d for d in doses if d["tank"] == tank.name]
        tanks.append({
            "name": tank.name,
            "factor": tank.factor,
            "volume_l": tank.volume,
            "ml_per_reservoir": volume * 1000 / tank.factor,
            "weigh_g": {d["product"]: d["grams"] * tank.factor * tank.volume / volume for d in members},
        })

    return {
        "name": name,
        "factor": factor,
        "doses": [
            {
                "product": d["product"],
                "tank": d["tank"],
                "feeds": d["feeds"],
                "grams": d["grams"],
                "cost": None if d["cost"] is None else format(d["cost"], "f"),
            }
            for d in doses
        ],
        "targets": targets,
        "achieved": achieved,
        "deviation_pct": deviation,
        "status": status,
        "total_ppm": total_ppm,
        "ec_ms": total_ppm / recipe.ec_factor,
        "cost_total": format(cost_total, "f"),
        "unpriced": [d["product"] for d in doses if d["cost"] is None],
        "tanks": tanks,
    }


def build_plan(recipe, tolerance=DEFAULT_TOLERANCE):
    """Solve every stage and return a JSON-ready plan dictionary."""
    stages = [_stage_plan(recipe, name, factor, tolerance) for name, factor in recipe.stage_list()]
    volume = recipe.reservoir
    steps = [f"Fill the reservoir to about half of {volume:g} L with water."]
    for tank in recipe.tanks.values():
        if not any(p.tank == tank.name for p in recipe.products):
            continue
        ml = volume * 1000 / tank.factor
        steps.append(
            f"Add {ml:.1f} ml of tank {tank.name} ({tank.factor:g}x), stir until clear, "
            f"then add the next tank. Never pour two concentrates together."
        )
    steps.append(f"Top up to {volume:g} L, stir, and check EC before feeding.")
    return {
        "reservoir_l": volume,
        "ec_factor": recipe.ec_factor,
        "uptake_l_per_day": recipe.uptake,
        "refill_days": math.floor(volume / recipe.uptake) if recipe.uptake else None,
        "tolerance_pct": tolerance,
        "conflicts": find_conflicts(recipe),
        "mixing": steps,
        "stages": stages,
    }


def _table(headers, rows):
    columns = [headers] + [[str(cell) for cell in row] for row in rows]
    widths = [max(len(str(cell)) for cell in column) for column in zip(*columns)]
    fmt = "  ".join(f"{{:<{width}}}" for width in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * width for width in widths))]
    lines.extend(fmt.format(*row) for row in columns[1:])
    return lines


def render_text(plan):
    lines = [f"Reservoir {plan['reservoir_l']:g} L | EC factor {plan['ec_factor']:g} | tolerance +/-{plan['tolerance_pct']:g}%"]
    if plan["refill_days"] is not None:
        lines.append(f"Uptake {plan['uptake_l_per_day']:g} L/day: refill every {plan['refill_days']} days")
    for stage in plan["stages"]:
        lines.append("")
        lines.append(f"== Stage {stage['name']} (targets x{stage['factor']:g}) ==")
        dose_rows = [
            (d["product"], d["tank"], d["feeds"] or "-", f"{d['grams']:.2f}", d["cost"] or "-")
            for d in stage["doses"]
        ]
        lines.extend(_table(["Product", "Tank", "Feeds", "Grams", "Cost"], dose_rows))
        lines.append("")
        ppm_rows = [
            (el, f"{stage['targets'][el]:.1f}", f"{stage['achieved'][el]:.1f}",
             f"{stage['deviation_pct'][el]:+.1f}", stage["status"][el])
            for el in sorted(stage["targets"], key=ELEMENTS.index)
        ]
        lines.extend(_table(["Element", "Target", "Achieved", "Dev %", "Status"], ppm_rows))
        lines.append(f"Estimated EC {stage['ec_ms']:.2f} mS/cm (approximate: total ppm / {plan['ec_factor']:g})")
        cost_line = f"Batch cost {stage['cost_total']}"
        if stage["unpriced"]:
            cost_line += f" (excludes unpriced: {', '.join(stage['unpriced'])})"
        lines.append(cost_line)
        lines.append("")
        tank_rows = [
            (t["name"], f"{t['factor']:g}x", f"{t['volume_l']:g} L", f"{t['ml_per_reservoir']:.1f}",
             "; ".join(f"{name} {grams:.2f} g" for name, grams in t["weigh_g"].items()) or "-")
            for t in stage["tanks"]
        ]
        lines.extend(_table(["Tank", "Factor", "Volume", "ml per reservoir", "Weigh out"], tank_rows))
    lines.append("")
    lines.append("Mixing:")
    lines.extend(f"  {i}. {step}" for i, step in enumerate(plan["mixing"], start=1))
    if plan["conflicts"]:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {message}" for message in plan["conflicts"])
    return "\n".join(lines)


def diff_plans(first, second):
    """Compare the first stage of two plans: achieved ppm and grams per product."""
    a, b = first["stages"][0], second["stages"][0]
    elements = sorted(set(a["achieved"]) | set(b["achieved"]), key=ELEMENTS.index)
    ppm_rows = []
    for el in elements:
        left = a["achieved"].get(el)
        right = b["achieved"].get(el)
        delta = "-" if left is None or right is None else f"{right - left:+.1f}"
        ppm_rows.append((el, _fmt(left), _fmt(right), delta))
    grams_a = {d["product"]: d["grams"] for d in a["doses"]}
    grams_b = {d["product"]: d["grams"] for d in b["doses"]}
    names = list(grams_a) + [name for name in grams_b if name not in grams_a]
    gram_rows = []
    for name in names:
        left = grams_a.get(name)
        right = grams_b.get(name)
        delta = "-" if left is None or right is None else f"{right - left:+.2f}"
        gram_rows.append((name, _fmt(left, "{:.2f}"), _fmt(right, "{:.2f}"), delta))
    lines = ["Achieved ppm (first stage):"]
    lines.extend(_table(["Element", "First", "Second", "Change"], ppm_rows))
    lines.append("")
    lines.append("Grams per product:")
    lines.extend(_table(["Product", "First", "Second", "Change"], gram_rows))
    return "\n".join(lines)


def _fmt(value, pattern="{:.1f}"):
    return "-" if value is None else pattern.format(value)


def _problems(plan):
    problems = list(plan["conflicts"])
    for stage in plan["stages"]:
        for el, status in stage["status"].items():
            if status != "ok":
                problems.append(
                    f"stage {stage['name']}: {el} is {stage['deviation_pct'][el]:+.1f}% off target"
                )
    return problems


def _load(path):
    text = Path(path).read_text(encoding="utf-8")
    return parse_recipe(text)


def _cmd_plan(args):
    recipe, errors = _load(args.file)
    if errors:
        for error in errors:
            print(error.render(args.file), file=sys.stderr)
        return 1
    plan = build_plan(recipe, args.tolerance)
    if args.stage:
        plan["stages"] = [s for s in plan["stages"] if s["name"] == args.stage]
        if not plan["stages"]:
            print(f"nutrimix: no stage named {args.stage!r}", file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps(plan, indent=2))
    else:
        print(render_text(plan))
    return 0


def _cmd_check(args):
    recipe, errors = _load(args.file)
    if errors:
        for error in errors:
            print(error.render(args.file), file=sys.stderr)
        return 1
    plan = build_plan(recipe, args.tolerance)
    problems = _problems(plan)
    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        return 1
    print(f"OK {args.file}: {len(plan['stages'])} stage(s) within +/-{args.tolerance:g}% of target, no conflicts")
    return 0


def _cmd_diff(args):
    plans = []
    for path in (args.first, args.second):
        recipe, errors = _load(path)
        if errors:
            for error in errors:
                print(error.render(path), file=sys.stderr)
            return 1
        plans.append(build_plan(recipe))
    print(diff_plans(plans[0], plans[1]))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="nutrimix", description="Offline hydroponic nutrient planner.")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="solve doses, check achieved ppm and print tank weigh-outs")
    plan.add_argument("file")
    plan.add_argument("--stage", help="only show this growth stage")
    plan.add_argument("--json", action="store_true", help="print the plan as JSON")
    plan.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE, help="allowed deviation in percent")
    plan.set_defaults(handler=_cmd_plan)

    check = sub.add_parser("check", help="CI gate: exit 1 on parse errors, conflicts or off-target ppm")
    check.add_argument("file")
    check.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE, help="allowed deviation in percent")
    check.set_defaults(handler=_cmd_check)

    diff = sub.add_parser("diff", help="compare two recipes' achieved ppm and product grams")
    diff.add_argument("first")
    diff.add_argument("second")
    diff.set_defaults(handler=_cmd_diff)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except OSError as exc:
        print(f"nutrimix: cannot read {exc.filename}: {exc.strerror}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
