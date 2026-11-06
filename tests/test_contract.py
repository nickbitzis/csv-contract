import json
from pathlib import Path

import pytest

from csvcontract import cli
from csvcontract.contract import (
    ERROR,
    WARN,
    Contract,
    bin_shares,
    check,
    infer,
    infer_type,
    psi,
    quantile_edges,
    read_csv,
    shape,
    shape_regex,
)
from csvcontract.sample import build

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def table(text):
    return read_csv(text.strip() + "\n")


BASE = """id,name,kind,score,when,flag
A-001,alpha,x,1.5,2026-01-01,true
A-002,beta,y,2.5,2026-01-02,false
A-003,gamma,x,3.0,2026-01-03,true
A-004,,y,4.0,2026-01-04,false
"""


def test_type_inference_picks_the_narrowest_type():
    assert infer_type(["1", "-2", "30"]) == "integer"
    assert infer_type(["1", "2.5"]) == "number"
    assert infer_type(["true", "False"]) == "boolean"
    assert infer_type(["2026-01-01"]) == "date"
    assert infer_type(["2026-01-01T10:00:00Z"]) == "datetime"
    assert infer_type(["1", "x"]) == "string"
    assert infer_type(["nan"]) == "string"


def test_shapes_become_patterns():
    assert shape("ORD-004512") == "AAA-999999"
    assert shape_regex("AAA-999999") == r"^[A-Z]{3}\-\d{6}$"
    assert shape_regex("a9") == r"^[a-z]{1}\d{1}$"


def test_infer_records_types_nulls_uniqueness_ranges_and_sets():
    c = {col.name: col for col in infer(*table(BASE)).columns}
    assert c["id"].unique and c["id"].pattern == r"^[A-Z]{1}\-\d{3}$"
    assert c["name"].nullable and c["name"].null_share == 0.25
    assert c["kind"].allowed is None  # 2 distinct of 4 is not clearly a category
    assert (c["score"].type, c["score"].min, c["score"].max) == ("number", 1.5, 4.0)
    assert (c["when"].type, c["when"].min) == ("date", "2026-01-01")
    assert c["flag"].type == "boolean"


def test_contract_round_trips_through_json():
    contract = infer(*table(BASE), source="base.csv")
    again = Contract.from_dict(json.loads(json.dumps(contract.to_dict())))
    assert again.to_dict() == contract.to_dict()


def test_a_file_passes_its_own_contract(tmp_path):
    folder = build(tmp_path)
    header, rows = read_csv((folder / "orders_2026_09.csv").read_text())
    assert check(infer(header, rows), header, rows) == []


def rules(found):
    return {(v.column, v.rule, v.severity) for v in found}


def test_check_catches_each_kind_of_problem():
    contract = infer(*table(BASE))
    header, rows = table("""id,name,kind,score,when,flag,extra
A-001,alpha,x,1.5,2026-01-01,true,1
A-001,beta,y,-2,01/02/2026,maybe,1
B_9,gamma,x,9.0,2026-01-03,true,1
""")
    found = rules(check(contract, header, rows))
    assert ("id", "duplicate", ERROR) in found
    assert ("id", "pattern", ERROR) in found
    assert ("score", "negative", ERROR) in found
    assert ("score", "above-max", WARN) in found
    assert ("when", "type", ERROR) in found
    assert ("flag", "type", ERROR) in found
    assert ("extra", "unexpected-column", WARN) in found


def test_missing_column_and_new_nulls():
    contract = infer(*table(BASE))
    header, rows = table("id,name,kind,score,when\n,,x,1,2026-01-01\nA-009,,x,1,2026-01-01\nA-010,,x,1,2026-01-01\n")
    found = rules(check(contract, header, rows))
    assert ("flag", "missing-column", ERROR) in found
    assert ("id", "null", ERROR) in found
    assert ("name", "more-nulls", WARN) in found


def test_examples_report_all_planted_problems():
    header, rows = read_csv((EXAMPLES / "orders_2026_09.csv").read_text())
    contract = infer(header, rows)
    found = rules(check(contract, *read_csv((EXAMPLES / "orders_2026_10.csv").read_text())))
    for expected in [
        ("customer_id", "null", ERROR),
        ("status", "not-allowed", ERROR),
        ("order_date", "type", ERROR),
        ("amount", "negative", ERROR),
        ("order_id", "duplicate", ERROR),
        ("country", "not-allowed", ERROR),
        ("country", "drift", ERROR),
        ("amount", "drift", WARN),
    ]:
        assert expected in found, expected


def test_psi_is_zero_for_identical_and_grows_with_shift():
    assert psi([0.5, 0.5], [0.5, 0.5]) == 0
    assert 0.1 < psi([0.5, 0.5], [0.7, 0.3]) < psi([0.5, 0.5], [0.9, 0.1])


def test_bins_from_quantiles():
    xs = list(range(100))
    edges = quantile_edges(xs)
    assert len(edges) == 9
    shares = bin_shares(xs, edges)
    assert sum(shares) == pytest.approx(1) and all(s == pytest.approx(0.1) for s in shares)


def test_messages_use_the_right_number():
    contract = infer(*table(BASE))
    header, rows = table("id,name,kind,score,when,flag\nA-001,a,x,1,2026-01-01,true\nA-001,b,x,2,2026-01-01,true\n")
    dup = next(v for v in check(contract, header, rows) if v.rule == "duplicate")
    assert dup.message.startswith("1 duplicate value in")


def test_cli_end_to_end(tmp_path, capsys):
    out = tmp_path / "c.json"
    assert cli.main(["infer", str(EXAMPLES / "orders_2026_09.csv"), "-o", str(out)]) == 0
    assert cli.main(["check", str(out), str(EXAMPLES / "orders_2026_09.csv")]) == 0
    assert "No problems found." in capsys.readouterr().out
    assert cli.main(["check", str(out), str(EXAMPLES / "orders_2026_10.csv")]) == 1
    assert "7 errors, 2 warnings." in capsys.readouterr().out
    assert cli.main(["check", str(out), str(EXAMPLES / "orders_2026_10.csv"), "--fail-on", "never", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["errors"] == 7 and data["violations"][0]["severity"] == "error"


def test_cli_profile_and_errors(tmp_path, capsys):
    assert cli.main(["profile", str(EXAMPLES / "orders_2026_09.csv")]) == 0
    assert "order_id     string, unique, never empty" in capsys.readouterr().out
    empty = tmp_path / "e.csv"
    empty.write_text("")
    assert cli.main(["profile", str(empty)]) == 2
    assert cli.main(["check", str(tmp_path / "nope.json"), str(empty)]) == 2


def test_bundled_examples_match_the_generator(tmp_path):
    build(tmp_path)
    for name in ("orders_2026_09.csv", "orders_2026_10.csv"):
        assert (tmp_path / name).read_text() == (EXAMPLES / name).read_text()
