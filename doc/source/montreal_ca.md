# Montreal (QC) Waste Collection

Waste collection schedules provided by [Info-Collecte Montréal](https://montreal.ca/info-collectes/), using official city GIS datasets.

This integration automatically detects your waste collection sector using your Home Assistant location. Manual sector overrides are optional and only needed for advanced cases or if your address is outside Montreal.

---

## Configuration via `configuration.yaml`

### Recommended: Automatic sector detection

```yaml
waste_collection_schedule:
  sources:
    - name: montreal_ca
```

If Home Assistant already has your home location configured, nothing else is required. The integration will use your latitude and longitude to determine the correct sector for each waste stream.

---

### Optional: Override location

Use this if your waste schedule should follow a different address. You can copy coordinates from [Google Maps](https://www.google.com/maps) by right-clicking on the map and selecting the coordinates.

```yaml
waste_collection_schedule:
  sources:
    - name: montreal_ca
      args:
        latitude: 45.5017
        longitude: -73.5673
```

---

### Advanced: Manual sector override

Only use this if auto-detection fails or if you want to force a specific sector for any waste stream. You may override any individual stream; unspecified streams will auto-detect.

```yaml
waste_collection_schedule:
  sources:
    - name: montreal_ca
      args:
        sector: MHM_41-1
        recycling: RPP_MR-5
        food: RPP-RE-22-RA
        green: RPP-RE-22-RV
        bulky: RPP-REGIE-22
```

---

## Configuration Variables

* **latitude** *(float, optional)* — Override latitude for automatic sector detection
* **longitude** *(float, optional)* — Override longitude for automatic sector detection
* **sector** *(string, optional)* — Manual waste sector override
* **recycling** *(string, optional)* — Manual recycling sector override
* **food** *(string, optional)* — Manual food sector override
* **green** *(string, optional)* — Manual green sector override
* **bulky** *(string, optional)* — Manual bulky sector override
* **cache_hours** *(int, optional)* — Dataset cache lifetime in hours (default: 24, min: 1, max: 168)

---

## How sector detection works

The integration downloads official Montreal GIS datasets and resolves your sector using a geographic polygon lookup. No manual files or maps are required. If your coordinates fall outside Montreal, you will be prompted to provide manual sector values.

---

## Example: French labels

```yaml
waste_collection_schedule:
  sources:
    - name: montreal_ca
      calendar_title: Info-Collecte
      customize:
        - type: Waste
          alias: Ordures
        - type: Food
          alias: Compost
        - type: Recycling
          alias: Recyclage
        - type: Green
          alias: Feuilles mortes
        - type: Bulky
          alias: Encombrants
```
