# ERA5 hourly point retrieval through the CDS API

Background research for the optional script `scripts/fetch_era5_bird_layer.py`. The pitch itself does not use this route: its wind comes from the Open-Meteo archive through `scripts/fetch_wind_grid.py`.

Checked 2026-11-14 against official Copernicus and ECMWF documentation.

## Recommended dataset and request

Use the CDS catalogue entry [ERA5 hourly time-series data on single levels from 1940 to present](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels-timeseries). It is the point/time-series entry: send a `location` object and a two-date `date` range, and the service returns hourly data for the nearest atmospheric grid point. The atmospheric grid is 0.25°; the catalogue also offers NetCDF and CSV output.

The current CDS setup page still documents the Python `cdsapi` client (`cdsapi>=0.7.7`) and says users may continue using it. Configure the personal token in `~/.cdsapirc` with `url: https://cds.climate.copernicus.eu/api`, then accept the dataset terms in the CDS web form before the first download.

```python
import cdsapi

client = cdsapi.Client()

client.retrieve(
    "reanalysis-era5-single-levels-timeseries",
    {
        "variable": [
            "10m_u_component_of_wind",
            "10m_v_component_of_wind",
            "2m_temperature",
            "2m_dewpoint_temperature",
            "total_precipitation",
        ],
        "date": ["2024-08-01", "2024-08-03"],
        "location": {"longitude": 151.2093, "latitude": -33.8686},
        "data_format": "netcdf",
    },
    "era5-point.nc",
)
```

This request shape follows the current [CDS API setup documentation](https://cds.climate.copernicus.eu/how-to-api) and the official [ERA Explorer point-request examples](https://era-explorer.climate.copernicus.eu/notebooks/content/wind/wind_annual_climatology.html). The time-series dataset returns all 24 hourly steps for each complete day; filter individual UTC hours locally if needed.

## Variables needed

| Need | CDS variable | Units | Notes |
| --- | --- | --- | --- |
| 10 m wind U component | `10m_u_component_of_wind` | m s⁻¹ | Available directly. |
| 10 m wind V component | `10m_v_component_of_wind` | m s⁻¹ | Available directly. |
| 2 m relative humidity | — | % | Not archived as a near-surface ERA5 field; derive it from 2 m temperature and dewpoint. |
| 2 m temperature | `2m_temperature` | K | Required for the RH denominator. |
| 2 m dewpoint temperature | `2m_dewpoint_temperature` | K | Required for the RH numerator. |
| Total precipitation | `total_precipitation` | m | Hourly accumulation; multiply by 1,000 for mm. |

ECMWF documents the near-surface calculation as:

```text
RH (%) = 100 × es(Td) / es(T)
```

Use one consistent saturation-vapour-pressure definition for `es`; ECMWF says the usual practice for near-surface RH is saturation over water. `surface_pressure` is also available from the time-series entry, but is needed for specific humidity rather than for this RH formula. See the official [ERA5 data documentation](https://confluence.ecmwf.int/pages/viewpage.action?pageId=216494709) and the [current time-series/ARCO parameter listing](https://confluence.ecmwf.int/spaces/CKB/pages/505390919/ERA5%2Bhourly%2BAnalysis%2BReady%2BCloud%2BOptimised%2BARCO%2Bdata%2Bon%2Bsingle%2Blevels%2Bfrom%2B1940%2Bto%2Bpresent%2BProduct%2BUser%2BGuide%2BPUG).

## Implementation caveats

- **Nearest grid point, not interpolation:** the requested latitude/longitude is rounded to the nearest 0.25° atmospheric grid point. Persist the returned coordinate if the result must be reproducible or auditable; do not label it as an exact station observation.
- **UTC and precipitation boundaries:** ERA5 validity times are UTC. ECMWF says reanalysis accumulations cover the hour ending at the validity time, so the `00:00` precipitation value covers 23:00–24:00 UTC on the preceding UTC date. Sum or convert to a local day only after handling that boundary explicitly.
- **Incomplete recent/early dates:** the time-series preparation skips a date when an accumulation variable has fewer than 24 hourly samples. Check the returned time index before assuming every requested date is complete.
- **Latency and revisions:** ERA5 is extended roughly five days behind real time and updated daily; delivery can be delayed. Cache the request, returned grid coordinate, data format, and retrieval date.
- **Service maturity:** ECMWF describes the time-series entry as an ARCO-backed subset and warns that it is experimental/not recommended for operational use; formats and structure may change. Practical fallback recommendation (an inference from that warning): use the parent [ERA5 hourly data on single levels](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels), request a small `area`, and select the nearest grid cell in local post-processing.

## Official sources

- [CDS API setup and current `cdsapi` guidance](https://cds.climate.copernicus.eu/how-to-api)
- [ERA5 hourly time-series dataset](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels-timeseries)
- [ECMWF ERA5 hourly time-series / ARCO Product User Guide](https://confluence.ecmwf.int/spaces/CKB/pages/505390919/ERA5%2Bhourly%2BAnalysis%2BReady%2BCloud%2BOptimised%2BARCO%2Bdata%2Bon%2Bsingle%2Blevels%2Bfrom%2B1940%2Bto%2Bpresent%2BProduct%2BUser%2BGuide%2BPUG)
- [ECMWF ERA5 data documentation](https://confluence.ecmwf.int/pages/viewpage.action?pageId=216494709)
- [Official Copernicus ERA Explorer wind request example](https://era-explorer.climate.copernicus.eu/notebooks/content/wind/wind_annual_climatology.html)
