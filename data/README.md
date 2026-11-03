# Data

## settlements.csv
Candidate settlements for `landpromo shortlist`. Coordinates are approximate town centres; correct them and add or remove rows as the target list firms up. Fleet and Chippenham are flagged because Business Plan §3.2 lists them although they sit outside the stated geography.

## layers/ (not committed)
Local GeoJSON layers in WGS84 (EPSG:4326), configured in `layers.toml`.

| File | What | Where to get it |
|---|---|---|
| `nutrient_catchments.geojson` | Nutrient neutrality catchments (Solent, Poole Harbour, River Avon and others). Give each feature a `name` property. | Natural England publishes the catchment boundaries on its open data portal (search "nutrient neutrality"). Check the current version: the boundaries and the mitigation regime are changing under the Planning and Infrastructure Act / Nature Restoration Fund. |
| `settlement_boundaries.geojson` | Settlement / defined development boundaries | Each council's Local Plan policies map. Digitise the priority settlements by hand first. |

If the source is in British National Grid (EPSG:27700), convert it first, for example:

```bash
ogr2ogr -t_srs EPSG:4326 nutrient_catchments.geojson source.shp
```

Everything else (flood zones, Green Belt, National Landscapes, SSSIs, heritage, local planning authority) comes live from planning.data.gov.uk, so nothing needs downloading.
