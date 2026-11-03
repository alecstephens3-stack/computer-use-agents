import types

from harness.ledger import Ledger, cost


def test_cost_counts_cache_writes_at_1_25x_and_reads_at_the_read_price():
    u = types.SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000,
                              cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000)
    assert round(cost("claude-sonnet-5-5", u), 4) == round(2.00 + 1.00 + 2.50 + 0.20, 4)


def test_ledger_total_survives_new_runs_and_caps_them(tmp_path):
    a = Ledger(tmp_path / "s.json", cap=1.0)
    a.add("r1", 0.60)
    b = Ledger(tmp_path / "s.json", cap=1.0, run_cap=0.30)
    assert b.can_start() and b.this_run() == 0
    b.add("r2", 0.31)
    assert b.over() and round(b.total(), 2) == 0.91
    assert not Ledger(tmp_path / "s.json", cap=0.95).can_start()
