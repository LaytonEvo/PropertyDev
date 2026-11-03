import json

from landpromo import constraints as c
from landpromo import shortlist as sl


def test_shortlist_tiers(tmp_path):
    fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"name": "Solent"},
        "geometry": {"type": "Polygon", "coordinates": [[[-1.6, 50.7], [-1.0, 50.7], [-1.0, 51.1], [-1.6, 51.1], [-1.6, 50.7]]]},
    }]}
    p = tmp_path / "nn.geojson"
    p.write_text(json.dumps(fc))
    layer = c.LocalLayer("nutrient_catchments", "Nutrient", p, {"residential": "major"})
    rows = [sl.Settlement("Romsey", "TVBC", 50.989, -1.497), sl.Settlement("Shaftesbury", "Dorset", 51.005, -2.198)]
    results = sl.shortlist(rows, [layer])
    tiers = {r.settlement.name: r.tier for r in results}
    assert tiers == {"Shaftesbury": "Residential tier 1", "Romsey": "Industrial only (nutrient catchment)"}
    assert results[0].settlement.name == "Shaftesbury"
    assert "Solent" in sl.render_markdown(results, True)


def test_reads_bundled_csv():
    assert len(sl.read_settlements("data/settlements.csv")) >= 20
