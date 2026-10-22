# tap-onspd

`tap-onspd` is a Singer tap for the [ONS Postcode Directory (ONSPD)](https://geoportal.statistics.gov.uk/search?q=PRD_ONSPD), the Office for National Statistics' quarterly file of every UK postcode. Each postcode comes with its latitude/longitude and the geographies it falls in: country, region, county, local authority, ward, constituency, LSOA, IMD rank and more. The tap also loads the code-to-name lookups that ship with each release.

Built with the [Meltano Singer SDK](https://sdk.meltano.com).

## How it works

Each ONSPD release is published as an ArcGIS Online item (owner `ONSGeography_data`, tag `PRD_ONSPD`). The item's data is a zip of about 250 MB that unzips to about 1.5 GB of CSV.

1. **Finding the release.** The tap asks the ArcGIS search API for the newest ONSPD item, or uses `item_id` if set.
2. **Discovery** needs the CSV headers to build schemas. It reads them straight from the remote zip with HTTP range requests, transferring about 1 MB in total. Nothing is downloaded to disk.
3. **Sync.**
   - The zip is downloaded to `download_dir` (default: `tap-onspd` in the system temp directory). It is streamed in 8 MB chunks to `<name>.zip.part`, and an interrupted download resumes with a `Range` request. Once the file is complete and passes a zip check, it is renamed to `<name>.zip`.
   - A zip of the right size that is already in `download_dir` is reused.
   - Rows are then streamed out of the zip one at a time (`zipfile` + `csv`). **Nothing is extracted to disk**, so memory and disk use stay flat.
4. **Cleanup.** The zip is deleted when the tap exits, whether the sync succeeded or failed, unless `keep_download` is true.

**Release bookmark.** Every record carries `_onspd_modified_at`, the release's last-modified time, which is used as the replication key. When the bookmark already holds the current release, streams emit nothing and the zip is never downloaded. A scheduled run between releases therefore finishes in seconds. Use `force_reload` to load the release again anyway.

## Streams

| Stream | Source file in the zip | Primary key |
|:--|:--|:--|
| `postcodes` | `Data/ONSPD_<MON>_<YYYY>_UK.csv` | `pcds` |
| `countries` | `Documents/CTRY …csv` | `ctry_cd` |
| `regions` | `Documents/RGN …csv` | `rgn_cd` |
| `counties` | `Documents/CTY …csv` | `cty_cd` |
| `local_authorities` | `Documents/LAD …csv` | `lad_cd` |
| `wards` | `Documents/WD …csv` | `wd_cd` |
| `constituencies` | `Documents/PCON …csv` | `pcon_cd` |

Every record also has `_onspd_release` (e.g. `AUG_2026`), `_onspd_item_id` and `_onspd_modified_at`.

**Schemas** come from each file's header, so columns that ONS adds in a later release show up without a code change. Use Meltano `select` to choose the columns you need.
- `lat` and `long` are numbers, and the `imdNNind` ranks are integers. Everything else is a string, which keeps leading zeros (e.g. in `north1m`).
- Empty values become `null`.
- Postcodes with no grid reference have `lat = 99.999999` and `long = 0`.

**Column names.**
- ONSPD puts the geography vintage in its column names (`lad26cd` = local authority code on 2026 boundaries), and the vintage moves on every year. With `strip_vintage_suffixes` (default on), the year is dropped (`lad26cd` → `lad_cd`, `RGN25NM` → `rgn_nm`), so names stay stable and lookup codes line up with `postcodes` columns.
- Columns that exist in several vintages keep their original names, because stripping the year would make them collide (`oa11cd`/`oa21cd`, `imd20ind`/`imd25ind`). All names are lower-cased.

**Lookup file choice.** When several files match a lookup pattern, the one with the latest `as at MM_YY` date is used.

**County.** ONSPD's county code is only set for two-tier county council areas. Unitary authorities get the pseudo code `E99999999`. Fall back to the local authority if you need a county for every postcode.

**Channel Islands and Isle of Man.** ONSPD also lists postcodes for these Crown Dependencies (`L93000001`, `M83000003`). They are not part of the UK, so the tap leaves them out by default as well.

## Settings

| Setting | Default | Description |
|:--|:--|:--|
| `item_id` | latest | ArcGIS item ID of a specific release |
| `download_dir` | `<tmp>/tap-onspd` | Where the zip is downloaded during a sync |
| `keep_download` | `false` | Keep the zip after the tap exits, so later runs can reuse it |
| `exclude_countries` | `["N92000002", "L93000001", "M83000003"]` | Country codes to leave out (default: Northern Ireland, see Licence, plus the Channel Islands and Isle of Man) |
| `member_pattern` | `^Data/ONSPD_[A-Z]{3}_\d{4}_UK\.csv$` | Regex for the main CSV in the zip |
| `lookups` | the six above | Stream name → regex for a `Documents/` CSV (replaces the defaults) |
| `strip_vintage_suffixes` | `true` | Drop the vintage year from column names |
| `force_reload` | `false` | Ignore the release bookmark |

Run `tap-onspd --about` for the full list, including the SDK's built-in settings.

## Usage

```bash
tap-onspd --about
tap-onspd --config config.json --discover > catalog.json
tap-onspd --config config.json --catalog catalog.json
```

With Meltano, this repo's `meltano.yml` is a dev project that keeps the zip in `.meltano/tap-onspd`:

```bash
meltano install
meltano run tap-onspd target-jsonl
```

**Run time.** Discovery takes about 20 s. A full load takes about 30 s to download, then the time to stream about 2.7M postcode rows; with a database loader, most of the time goes on inserting them.

## Licence

### The tap

The tap's code is licensed under Apache-2.0 (see [LICENSE](LICENSE)). This repository contains no ONSPD data: the tap downloads it from ONS each time it runs. The test fixtures contain only a few sample rows.

### ONSPD data

ONSPD is covered by the ONS's own terms, not by the tap's licence. Anyone who loads it with this tap must follow them. The terms below come from section 3 of the ONSPD User Guide (*Licensing Requirements – Copyright and Reproduction*, shipped in the release zip under `User Guide/`) and the [ONS Licences page](https://www.ons.gov.uk/methodology/geography/licences).

- **Licence:** ONS postcode products are derived from Code-Point® Open and published under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/). The data can be re-used free of charge in any format or medium, commercially or not, under the data owners' terms. ONS and data-owner logos are not covered.
- **Attribution (required).** The following statements must be acknowledged or displayed **wherever the data is used**, including reports, dashboards and apps built on the loaded tables. Use the year of the release you loaded; `_onspd_release` holds it, e.g. `AUG_2026` → 2026.

  > Contains OS data © Crown copyright and database right 2026
  >
  > Contains Royal Mail data © Royal Mail copyright and database right 2026
  >
  > Source: Office for National Statistics licensed under the Open Government Licence v.3.0

- **Northern Ireland (`BT` postcodes):**
  - NI data is excluded from the free re-use above. It comes with Land & Property Services' Northern Ireland End User Licence, which allows **internal business use only**, and using it counts as accepting those terms.
  - **Commercial use requires a separate licence direct from LPS** (see [LPS copyright, licensing and publishing](https://www.finance-ni.gov.uk/articles/lps-copyright-licensing-and-publishing)).
  - The tap excludes NI by default (`N92000002` in `exclude_countries`). Only remove it if your use falls within the End User Licence, or you hold an LPS licence.

## Development

```bash
uv sync
uv run pytest
```

The tests use a small fixture zip shaped like a real release and a mocked ArcGIS API, so they need no network access.
