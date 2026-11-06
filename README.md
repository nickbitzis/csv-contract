# csvcontract

Learn what "normal" looks like from a CSV you trust, save it as a data contract, and check every new batch against it before it reaches a dashboard or a model. Pure Python, no dependencies.

## Why

Most data pipeline failures are not crashes. The job runs, the numbers are wrong, and someone finds out a week later. Typical causes:

- A new status code ships upstream and every report that groups by status quietly drops it.
- A field starts arriving empty after a release.
- One service switches date formats.
- Refunds leak into an orders feed as negative amounts.
- A retried write duplicates an ID.
- Prices shift, so a model trained on last month's data sees a different world.

A data contract is a short, readable file that writes down what each column should look like. Checking against it turns those silent problems into a failed CI step with a clear message.

## Quick start

The example files are a synthetic order export: a trusted September and an October batch from "after an upstream release", with problems planted in it.

```
$ uv run csvcontract infer examples/orders_2026_09.csv -o examples/orders.contract.json
Wrote a contract for 9 columns from 1,500 rows to examples/orders.contract.json

$ uv run csvcontract check examples/orders.contract.json examples/orders_2026_10.csv
Checked 1,500 rows of orders_2026_10.csv against the contract from orders_2026_09.csv.
7 errors, 2 warnings.

  ERROR  amount: 3 negative values in a column that has never been negative, e.g. '-219.41', '-88.30', '-123.67'  (rows 141, 1463, 1475)
  ERROR  country: 443 values outside the allowed set ['AU', 'CA', 'DE', 'GB', 'US'], e.g. 'MX', 'MX', 'MX'  (rows 4, 14, 18, 22, 30)
  ERROR  country: distribution shifted, PSI 2.46 (large)
  ERROR  customer_id: 40 empty values in a column that has never been empty, e.g. '', '', ''  (rows 2, 101, 156, 158, 163)
  ERROR  order_date: 6 values that are not date, e.g. '10/29/2026', '10/26/2026', '10/21/2026'  (rows 231, 437, 637, 867, 1147)
  ERROR  order_id: 1 duplicate value in a unique column, e.g. 'ORD-002200'  (rows 701)
  ERROR  status: 12 values outside the allowed set ['cancelled', 'completed', 'returned', 'shipped'], e.g. 'on_hold', 'on_hold', 'on_hold'  (rows 5, 341, 351, 578, 609)
  WARN   amount: 4 values above the old maximum of 437.03, e.g. '977.76', '514.18', '579.56'  (rows 56, 121, 599, 784)
  WARN   amount: distribution shifted, PSI 0.16 (moderate)
```

Every planted problem is found, and checking September against its own contract finds nothing. Some findings are real business changes rather than bugs, like the new market in Mexico. That is the point: someone decides, then updates the contract on purpose.

`csvcontract profile FILE` prints a one-line summary of each column without writing anything.

## What the contract records

For each column, from the trusted file:

| Rule | Inferred when | Checked as |
|---|---|---|
| Type | boolean, integer, number, ISO date, ISO datetime, or string, the narrowest that fits every value | error |
| Never empty | the column had no empty values | error |
| Unique | a string or integer column had no repeats, as IDs do | error |
| Allowed values | a string column had 20 or fewer distinct values, repeated | error |
| Shape | every string had the same shape, like `ORD-004512`, saved as a regex | error |
| Never negative | a numeric column's minimum was zero or more | error |
| Range | numeric minimum and maximum | warning |
| Empty share | the share of empty values; a jump to three times as many is flagged | warning |
| Distribution | deciles for numbers, value shares for categories | PSI drift |

The contract is plain JSON, so it can be reviewed in a pull request and edited by hand, for example to add `MX` to the allowed countries.

Drift uses the population stability index, the standard measure in credit risk and model monitoring. Under 0.1 is stable, 0.1 to 0.25 is a moderate shift and a warning, and over 0.25 is a large shift and an error. Both thresholds are flags.

## In CI

`check` exits 1 when there are errors, 0 when the batch passes, and 2 when a file can't be read. `--fail-on warn` is stricter and `--fail-on never` only reports. `--json` prints the violations for other tools.

```bash
uvx --from git+https://github.com/nickbitzis/csv-contract csvcontract check contracts/orders.json exports/orders_latest.csv
```

## Tests

```bash
uv run --group dev pytest
```

14 tests cover type and shape inference, the JSON round trip, a file passing its own contract, each rule, every planted problem in the examples, PSI, the CLI and its exit codes, and that the bundled examples match the generator.

## License

MIT. See [LICENSE](LICENSE).
