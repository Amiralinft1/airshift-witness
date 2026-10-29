# AirShift Witness

AirShift Witness is a standalone GenLayer Intelligent Contract that checks hourly air-quality measurements from an official German monitoring station against a caller-supplied threshold. It stores the result after GenLayer validators independently fetch the UBA data and agree on the derived assessment.

## How it works

Call `check_window` with a UBA station code, date, hourly range, pollutant component, and threshold. The contract fetches the UBA Air Data API and stores one of these outcomes:

- `BREACH`: at least one observed hour exceeded the threshold.
- `WITHIN_LIMIT`: every requested hour had data and none exceeded the threshold.
- `INCOMPLETE`: one or more requested readings were unavailable.

The validators compare the outcome, expected hours, observed hours, and number of breach hours. Use `get_record` to read the stored result.

## Inputs

- `station_code`: official UBA station code, for example `DEHE041`.
- `local_date`: date in `YYYY-MM-DD` format.
- `time_from` and `time_to`: UBA hourly slots from 1 to 24. Slot 1 covers 00:00 to 01:00 in the API's documented CET time convention.
- `component_id`: `1` for PM10, `3` for O3, `4` for SO2, `5` for NO2, or `9` for PM2.5.
- `threshold_milli`: threshold in thousandths of the API unit. For example, `30000` means 30.000 micrograms per cubic meter.

## Live verification

Verified on GenLayer Studio with station `DEHE041`, date `2026-10-28`, slot 1, NO2, and a threshold of `30000`. The contract stored `BREACH`, with 1 expected hour, 1 observed hour, and 1 breach hour.

- Contract: https://explorer-studio.genlayer.com/address/0x2b9A67093D6A90963d9Cb33CF2949a1bB2bA2F09
- Deployment transaction: https://explorer-studio.genlayer.com/tx/0x72f4b2d6c0d6c425c768a4facb1e3f6817e702497876e906a94ef93f4eba3f9d
- Assessment transaction: https://explorer-studio.genlayer.com/tx/0x695fc065da212873b59ec39fd09b382af099e10afc1b600f0aba8b86bf431e70

## Data limitations

UBA reports that current-year measurements are provisional and may contain gaps or later revisions. Missing readings are reported as `INCOMPLETE`, never as a pass. A station measures conditions at its own location. This contract is an evidence-recording tool, not medical or legal advice.

Source attribution: "Umweltbundesamt mit Daten der Messnetze der Länder und des Bundes". See the [UBA Air Data API v4 documentation](https://luftdaten.umweltbundesamt.de/api/air-data/v4/doc) and its [Data licence Germany, attribution, version 2.0](https://www.govdata.de/dl-de/by-2-0).
