# Montreal

Waste collection schedules provided by [Info-Collecte Montréal](https://montreal.ca/info-collectes/), using official city GIS datasets.

The integration automatically detects your waste collection sector from your Home Assistant location. Manual sector overrides are optional and only needed for advanced cases.

---

## Configuration via configuration.yaml

### Recommended (automatic sector detection)

```yaml
waste_collection_schedule:
  sources:
    - name: montreal_ca
```

If Home Assistant already has your home location configured, nothing else is required.

---

### Optional: override location

Use this if your waste schedule should follow a different address. You can copy these coordinates from [Google Maps](https://www.google.com/maps) by right-clicking on the map and selecting the coordinates.

```yaml
waste_collection_schedule:
  sources:
    - name: montreal_ca
      args:
        latitude: 45.5017
        longitude: -73.5673
```

---

### Advanced: manual sector override

Only use this if auto-detection fails or if you want to force a specific sector.

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

You may override any individual waste stream. Unspecified streams will auto-detect.

---

## Configuration Variables

* **latitude** *(float) (optional)*
  Override latitude used for automatic sector detection

* **longitude** *(float) (optional)*
  Override longitude used for automatic sector detection

* **sector** *(string) (optional)*
  Manual waste sector override

* **recycling** *(string) (optional)*
  Manual recycling sector override

* **food** *(string) (optional)*
  Manual food/compost sector override

* **green** *(string) (optional)*
  Manual green waste sector override

* **bulky** *(string) (optional)*
  Manual bulky items sector override

---

## How sector detection works

The integration downloads official Montreal GIS datasets and resolves your sector using geographic polygon lookup.

No manual files or maps are required.

If your coordinates fall outside Montreal, you will be prompted to provide manual sector values.

---

## Example (French labels)

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
