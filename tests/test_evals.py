from evals.run_evals import eval_fit, invented_facts, load_dataset, spearman

SRC = "Jane Doe. Built Python services at Acme Corp, cut latency 40%. BS Georgia Tech 2019."


def test_invented_facts_flags_new_numbers_and_names():
    out = "# Jane Doe\n- Built Python services at Acme Corp, cut latency 40%\n- Led a team of 12 at Google"
    flagged = invented_facts(SRC, out)
    assert "12" in flagged and "Google" in flagged
    assert "40%" not in flagged and "Acme" not in flagged and "Python" not in flagged


def test_invented_facts_clean_output_and_headings_ignored():
    out = "# Jane Doe\n## Experience\n- Built Python services at Acme Corp, cut latency 40%"
    assert invented_facts(SRC, out) == []


def test_spearman_handles_ties_and_direction():
    assert spearman([10, 50, 90], [0, 1, 2]) > 0.99
    assert spearman([90, 50, 10], [0, 1, 2]) < -0.99
    assert spearman([80, 80, 80], [0, 1, 2]) is None   # no variance in scores
    assert spearman([1, 2], [1, 2]) is None             # too few points


def test_eval_fit_band_accuracy():
    cases = load_dataset()
    perfect = {"strong": 85, "medium": 55, "weak": 10}
    lookup = {c["resume"]: perfect[c["label"]] for c in cases}
    report = eval_fit(cases, lambda resume, job: {"score": lookup[resume]})
    assert report["band_accuracy"] == 1.0 and report["spearman"] > 0.9


def test_dataset_is_well_formed():
    cases = load_dataset()
    assert len(cases) >= 6 and len({c["id"] for c in cases}) == len(cases)
    assert {c["label"] for c in cases} == {"strong", "medium", "weak"}
