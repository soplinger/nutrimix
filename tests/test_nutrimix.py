import json
from pathlib import Path

import pytest

from nutrimix import build_plan, diff_plans, main, parse_recipe, render_text

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "lettuce.nut"

BASE = """\
reservoir 50 L
tank A 100x 2 L
product CalNit A Ca=19 N=15.5 price=4.20/kg
target Ca 160
"""


def solve(text, tolerance=10.0):
    recipe, errors = parse_recipe(text)
    assert errors == []
    return build_plan(recipe, tolerance)


def test_unknown_directive_reports_its_line():
    _, errors = parse_recipe("reservoir 10 L\nflood 3\n")
    assert (2, "unknown directive 'flood'") in [(e.line, e.message) for e in errors]


def test_missing_reservoir_and_targets_are_file_level_errors():
    _, errors = parse_recipe("tank A 100x\nproduct X A Ca=19\n")
    messages = [e.message for e in errors]
    assert any("reservoir" in m for m in messages)
    assert any("no 'target' lines declared" in m for m in messages)
    assert all(e.line is None for e in errors if "reservoir" in e.message or "target" in e.message)


def test_target_without_a_supplying_product_points_at_target_line():
    text = "reservoir 10 L\ntank A 100x\nproduct CalNit A Ca=19 N=15.5\ntarget K 200\n"
    _, errors = parse_recipe(text)
    assert (4, "target K has no product that supplies K") in [(e.line, e.message) for e in errors]


def test_feeds_must_name_an_element_in_the_product():
    text = "reservoir 10 L\ntank A 100x\ntarget Ca 100\nproduct Bad A Ca=19 feeds=K\n"
    _, errors = parse_recipe(text)
    assert (4, "feeds=K but the product contains no K") in [(e.line, e.message) for e in errors]


def test_bad_percentage_and_price_are_reported_on_their_lines():
    text = "reservoir 10 L\ntank A 100x\ntarget Ca 100\nproduct X A Ca=150\nproduct Y A Ca=10 price=4.2\n"
    _, errors = parse_recipe(text)
    found = {e.line: e.message for e in errors}
    assert found[4] == "Ca must be a percentage above 0 and up to 100"
    assert found[5] == "price must look like price=4.20/kg"


def test_grams_follow_target_divided_by_percentage():
    stage = solve(BASE)["stages"][0]
    assert stage["name"] == "base"
    assert stage["doses"][0]["grams"] == pytest.approx(160 * 50 / 190)
    assert stage["achieved"]["Ca"] == pytest.approx(160)
    assert stage["status"]["Ca"] == "ok"


def test_second_source_carryover_is_counted_and_flagged_low():
    stage = solve(BASE + "target N 150\n")["stages"][0]
    # CalNit brings its own nitrogen, so N lands below the 150 ppm target.
    assert stage["achieved"]["N"] == pytest.approx(160 * 15.5 / 19)
    assert stage["deviation_pct"]["N"] == pytest.approx(-12.98, abs=0.01)
    assert stage["status"]["N"] == "low"


def test_stage_factor_scales_doses():
    stages = solve(BASE + "stage seedling 0.5\nstage vegetative 1\n")["stages"]
    assert [s["name"] for s in stages] == ["seedling", "vegetative"]
    assert stages[0]["doses"][0]["grams"] == pytest.approx(stages[1]["doses"][0]["grams"] / 2)


def test_tank_weigh_out_matches_dilution_ratio():
    plan = solve(BASE)
    grams = plan["stages"][0]["doses"][0]["grams"]
    tank = plan["stages"][0]["tanks"][0]
    # 100x in 50 L is 500 ml; the 2 L tank holds 100 * 2 / 50 = 4x the dose.
    assert tank["ml_per_reservoir"] == pytest.approx(500.0)
    assert tank["weigh_g"]["CalNit"] == pytest.approx(4 * grams)


def test_calcium_and_sulphate_in_one_tank_is_a_precipitation_conflict():
    text = (
        "reservoir 50 L\ntank A 100x 2 L\n"
        "product CalNit A Ca=19 N=15.5\n"
        "product MgSO4 A Mg=9.8 S=13\n"
        "target Ca 160\ntarget Mg 50\ntarget S 60\n"
    )
    plan = solve(text)
    assert len(plan["conflicts"]) == 1
    assert "mixes Ca (CalNit) with S (MgSO4)" in plan["conflicts"][0]


def test_calcium_and_phosphate_in_separate_tanks_is_fine():
    text = (
        "reservoir 50 L\ntank A 100x\ntank B 100x\n"
        "product CalNit A Ca=19 N=15.5\n"
        "product MKP B K=28.7 P=22.7\n"
        "target Ca 160\ntarget P 40\ntarget K 200\n"
    )
    assert solve(text)["conflicts"] == []


def test_cost_is_rounded_to_exact_cents():
    plan = solve(BASE)
    # 42.105263 g at 4.20/kg is 0.176842 money units, which rounds to 0.18.
    assert plan["stages"][0]["doses"][0]["cost"] == "0.18"
    assert plan["stages"][0]["cost_total"] == "0.18"


def test_unpriced_products_are_listed_and_left_out_of_cost():
    plan = solve("reservoir 50 L\ntank A 100x 2 L\nproduct CalNit A Ca=19 N=15.5\ntarget Ca 160\n")
    stage = plan["stages"][0]
    assert stage["unpriced"] == ["CalNit"]
    assert stage["cost_total"] == "0.00"


def test_ec_is_total_ppm_over_ec_factor():
    stage = solve(BASE)["stages"][0]
    total_ppm = 160 + 160 * 15.5 / 19
    assert stage["ec_ms"] == pytest.approx(total_ppm / 640)


def test_refill_days_round_down():
    plan = solve(BASE + "uptake 3\n")
    assert plan["refill_days"] == 16


def test_diff_reports_change_in_achieved_ppm():
    report = diff_plans(solve(BASE), solve(BASE.replace("target Ca 160", "target Ca 200")))
    assert "Ca" in report
    assert "+40.0" in report


def test_render_text_lists_warnings_for_conflicts():
    text = (
        "reservoir 50 L\ntank A 100x 2 L\n"
        "product CalNit A Ca=19 N=15.5\n"
        "product MgSO4 A Mg=9.8 S=13\n"
        "target Ca 160\ntarget Mg 50\ntarget S 60\n"
    )
    output = render_text(solve(text))
    assert "Warnings:" in output
    assert "Mixing:" in output


def test_check_exit_codes_follow_ppm_and_conflicts(tmp_path, capsys):
    good = tmp_path / "good.nut"
    good.write_text(BASE, encoding="utf-8")
    assert main(["check", str(good)]) == 0
    assert capsys.readouterr().out.startswith("OK")

    off = tmp_path / "off.nut"
    off.write_text(BASE + "target N 150\n", encoding="utf-8")
    assert main(["check", str(off)]) == 1
    assert "stage base: N is -13.0% off target" in capsys.readouterr().out


def test_parse_errors_exit_one_with_file_and_line(tmp_path, capsys):
    path = tmp_path / "bad.nut"
    path.write_text("reservoir 10 L\nflood 3\n", encoding="utf-8")
    assert main(["plan", str(path)]) == 1
    assert f"{path}:2: unknown directive 'flood'" in capsys.readouterr().err


def test_plan_json_output_is_machine_readable(tmp_path, capsys):
    path = tmp_path / "base.nut"
    path.write_text(BASE, encoding="utf-8")
    assert main(["plan", "--json", str(path)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["stages"][0]["name"] == "base"
    assert data["stages"][0]["doses"][0]["product"] == "CalNit"


def test_example_recipe_parses_and_is_within_tolerance():
    recipe, errors = parse_recipe(EXAMPLE.read_text(encoding="utf-8"))
    assert errors == []
    plan = build_plan(recipe)
    assert plan["conflicts"] == []
    assert plan["refill_days"] == 20
    for stage in plan["stages"]:
        assert set(stage["status"].values()) == {"ok"}
