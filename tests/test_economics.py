from dataclasses import replace

import pytest

from landpromo import economics as econ


def test_plan_v2_floor_expected_profit_is_thin():
    site, terms, plan = econ.load_scenario("scenarios/plan-v2-floor.toml")
    r = econ.evaluate(site, terms, plan)
    assert r.gross_land_value == pytest.approx(306_122, rel=1e-6)
    # Success: £60k recovered + 30% of (300k - 60k) = £72k profit
    assert r.outcomes[0].profit == pytest.approx(72_000, abs=5)
    # 60% x 72k - 40% x 60k = 19.2k
    assert r.expected_profit == pytest.approx(19_200, abs=10)


def test_costs_recovered_first_and_capped_by_proceeds():
    site = econ.Site(gross_acres=1, land_use="industrial", net_developable_ratio=1,
                     value_per_net_acre=50_000)
    terms = econ.DealTerms(promoter_share=0.3, sale_cost_pct=0, landowner_legal_fees=0)
    # proceeds 50k < costs 60k: promoter recovers only 50k and nothing is split
    assert econ.promoter_receipt_on_sale(site, terms, 60_000) == 50_000


def test_uplift_basis_pays_promoter_less_than_net_proceeds_basis():
    site, terms, plan = econ.load_scenario("scenarios/residential-3ac.toml")
    net = econ.evaluate(site, terms, plan).expected_profit
    upl = econ.evaluate(site, replace(terms, split_basis="uplift"), plan).expected_profit
    assert upl < net


def test_dwellings_and_affordable_split():
    site = econ.Site(gross_acres=3, net_developable_ratio=0.65, density_dph=30)
    assert site.dwellings == int(3 * 0.65 * econ.ACRE_TO_HA * 30)  # 23


def test_nutrient_cost_reduces_land_value():
    site = econ.Site(gross_acres=3)
    assert econ.Site(gross_acres=3, nutrient_cost_per_dwelling=5_000).gross_land_value() == \
        site.gross_land_value() - 5_000 * site.dwellings


def test_probabilities_sum_to_one():
    for f in ["residential-3ac", "industrial-5ac", "plan-v2-floor"]:
        r = econ.evaluate(*econ.load_scenario(f"scenarios/{f}.toml"))
        assert sum(o.probability for o in r.outcomes) == pytest.approx(1)


def test_min_viable_acres_meets_target():
    site, terms, plan = econ.load_scenario("scenarios/residential-3ac.toml")
    acres = econ.min_viable_acres(site, terms, plan, 50_000)
    assert acres is not None
    assert econ.evaluate(replace(site, gross_acres=acres), terms, plan).expected_profit >= 50_000
    assert econ.evaluate(replace(site, gross_acres=acres - 0.05), terms, plan).expected_profit < 50_000
