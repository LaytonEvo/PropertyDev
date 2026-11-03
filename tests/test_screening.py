import json
from types import SimpleNamespace

from landpromo import economics as econ
from landpromo import screening as scr
from landpromo.constraints import ConstraintHit


def _inp(hits, ev_scenario=None):
    e = econ.evaluate(*econ.load_scenario(ev_scenario)) if ev_scenario else None
    return scr.ScreeningInput("Test", "residential", 3, 50.9, -1.5, hits, e,
                              sources_checked=["planning.data.gov.uk", "nutrient_catchments"])


def test_rules():
    fatal = ConstraintHit("x", "SSSI", "fatal")
    major = ConstraintHit("x", "Green Belt", "major")
    nearby_fatal = ConstraintHit("x", "SSSI", "moderate", within_buffer_only=True)
    assert scr.rules_rating(_inp([], "scenarios/residential-3ac.toml")).rating == "GREEN"
    assert scr.rules_rating(_inp([])).rating == "AMBER"  # no economics
    assert scr.rules_rating(_inp([nearby_fatal], "scenarios/residential-3ac.toml")).rating == "GREEN"
    assert scr.rules_rating(_inp([major])).rating == "AMBER"
    assert scr.rules_rating(_inp([fatal, major])).rating == "RED"
    # Plan v2 floor: positive but below the £50k target -> AMBER
    assert scr.rules_rating(_inp([], "scenarios/plan-v2-floor.toml")).rating == "AMBER"


def test_strict_schema_requires_all_fields():
    schema = scr._strict_schema(scr.Narrative)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(scr.Narrative.model_fields)


def test_draft_narrative_parses_structured_output():
    payload = {
        "summary": ["a", "b", "c"], "key_risks": ["r"], "checks_before_outreach": ["c"],
        "questions_for_planning_consultant": ["q"], "suggested_next_action": "n",
        "agrees_with_rules_rating": True, "disagreement_reason": "",
    }
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(payload))])

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    inp = _inp([], "scenarios/residential-3ac.toml")
    n = scr.draft_narrative(inp, scr.rules_rating(inp), client=client)
    assert n.summary == ["a", "b", "c"]
    assert captured["model"] == "claude-opus-5-5"
    md = scr.render_markdown(inp, scr.rules_rating(inp), n)
    assert "Desktop appraisal: Test" in md and "Expected profit" in md


def test_unchecked_sources_cap_at_amber():
    e = econ.evaluate(*econ.load_scenario("scenarios/residential-3ac.toml"))
    inp = scr.ScreeningInput("T", "residential", 3, 50.9, -1.5, [], e, sources_checked=[])
    r = scr.rules_rating(inp)
    assert r.rating == "AMBER" and "nutrient catchment" in r.reasons[-1]
