# Attribution

This project uses carbon intensity data from the **National Energy System
Operator (NESO) Carbon Intensity API**.

- **Source:** National Energy System Operator (NESO) - Carbon Intensity API
- **Endpoint:** https://api.carbonintensity.org.uk
- **Licence:** [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)

> Carbon Intensity data provided by the National Energy System Operator (NESO),
> licensed under the Creative Commons Attribution 4.0 International Licence.

Any published output, dataset, figure, or paper derived from this project must
retain this attribution to NESO under CC BY 4.0.

## Notes on the data

- All timestamps are **UTC** and data is **half-hourly**.
- National `/intensity/{from}/{to}` provides **both forecast and actual**
  gCO2/kWh.
- **Regional data is forecast only** - there are no regional actuals.
- The national range endpoint is capped at a **14-day** range per request.
