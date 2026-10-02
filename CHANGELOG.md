# Changelog

Every release is a git tag, and this file says what is in each one. Written by
`scripts/release.sh` from the commits since the previous tag; see
[docs/RELEASING.md](docs/RELEASING.md) for how a release is cut and what one
contains.

Newest first. Dates are DD/MM/YYYY.

## v1.3.0 -- 02/10/2026

First public release. Apache-2.0.

### What it is

- A hub gateway pair (redundant, and the MQTT broker via Cirrus Link Distributor), Edge spokes, Postgres, and a demo console that starts and stops each demonstration.
- Demonstrations: EAM, Store & Forward (Gateway Network and MQTT roads, with audit and alarm evidence tables), Redundancy (changeover timing and data-loss proof), Sparkplug, and the OPC UA server on every gateway.
- Ignition runs on its two-hour trial; reset an expired gateway at https://console.test/_wd/trials.
- Ignition and the Cirrus Link modules are downloaded at install time under their own licences; none are in this repo.
