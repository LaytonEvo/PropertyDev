import json

from shapely.geometry import Point

from landpromo import constraints as c


class FakeClient:
    """Returns on-site entities for the exact site geometry, extra ones for the buffer."""

    def __init__(self, on_site, nearby):
        self.on_site, self.nearby, self.calls = on_site, nearby, 0

    def entities(self, geom, datasets):
        self.calls += 1
        return self.on_site if self.calls == 1 else self.on_site + self.nearby


def test_site_from_point_area():
    geom = c.site_from_point(50.9, -1.5, 3.0)
    assert abs(c.area_acres(geom) - 3.0) < 0.05


def test_severity_mapping_and_buffer_downgrade():
    on_site = [
        {"dataset": "flood-risk-zone", "reference": "f1", "flood-risk-level": "2"},
        {"dataset": "local-planning-authority", "reference": "E1", "name": "Test Valley"},
    ]
    nearby = [
        {"dataset": "site-of-special-scientific-interest", "reference": "s1", "name": "Marsh"},
        {"dataset": "flood-risk-zone", "reference": "f2", "flood-risk-level": "3"},
    ]
    hits = c.lookup_constraints(c.site_from_point(51, -1.5, 2), "residential",
                                client=FakeClient(on_site, nearby))
    by_label = {h.label: h for h in hits}
    assert by_label["Flood Zone 2"].severity == "moderate"
    assert by_label["SSSI"].severity == "moderate" and by_label["SSSI"].within_buffer_only
    assert "Flood Zone 3" not in by_label  # adjacent flood zone ignored
    assert by_label["Local planning authority"].name == "Test Valley"


def test_local_layer_hits_by_land_use(tmp_path):
    fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"name": "Solent"},
        "geometry": {"type": "Polygon", "coordinates": [[[-2, 50], [-1, 50], [-1, 51], [-2, 51], [-2, 50]]]},
    }]}
    (tmp_path / "layers").mkdir()
    (tmp_path / "layers" / "nn.geojson").write_text(json.dumps(fc))
    (tmp_path / "layers.toml").write_text(
        '[layers.nutrient_catchments]\nlabel="Nutrient"\npath="layers/nn.geojson"\n'
        'severity={residential="major"}\n'
        '[layers.missing]\npath="layers/nope.geojson"\n'
    )
    layers = c.load_local_layers(tmp_path / "layers.toml")
    assert [l.name for l in layers] == ["nutrient_catchments"]
    pt = Point(-1.5, 50.5)
    assert c.lookup_constraints(pt, "residential", local_layers=layers)[0].name == "Solent"
    assert c.lookup_constraints(pt, "industrial", local_layers=layers) == []
    assert c.lookup_constraints(Point(0, 50.5), "residential", local_layers=layers) == []
