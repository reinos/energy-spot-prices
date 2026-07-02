# Home Assistant Energy Spot Prices

Custom component for Home Assistant to fetch day-ahead electricity spot prices for European countries. Prices are exposed as sensors and can be used in automations to schedule equipment at the cheapest moments. A 24-hour forecast is available in the sensor attributes and can be visualized in a graph:

> This project started as a fork of [hass-entso-e](https://github.com/JaccoR/hass-entso-e).

<p align="center">
    <img src="https://user-images.githubusercontent.com/31140879/195382579-c87b3285-c599-4e30-867e-1acf9feffabe.png" width=40% height=40%>
</p>

---

## Data providers

The integration supports two data providers that are tried in order on every fetch. When a provider fails or returns insufficient data, the next one is used automatically.

### 1. ENTSO-e Transparency Platform (primary)

Prices are fetched directly from the official [ENTSO-e Transparency Platform](https://transparency.entsoe.eu/). This is the authoritative source for European day-ahead prices.

**Requires an API key.** To request one, register on the Transparency Platform and send an email to transparency@entsoe.eu with "Restful API access" in the subject line. Include the email address used during registration in the body.

> **Note:** The ENTSO-e API is known to be unreliable — it can be slow, return incomplete data, or be completely unavailable for hours at a time. The integration handles this automatically by falling back to the DAP Xadi provider.

### 2. DAP Xadi (fallback)

[dap.xadi.eu](https://dap.xadi.eu) is an ENTSO-e backed community API that requires no API key. It is used automatically as a fallback when ENTSO-e is unavailable or returns insufficient data.

DAP Xadi aggregates multi-zone countries (Denmark, Sweden, Norway) to a single national price — prices may differ slightly from a specific ENTSO-e bidding zone for those countries.

### How fetching works

On every scheduled update (every 60 minutes):

1. The integration checks whether sufficient data is already cached. If today's prices are complete and tomorrow's prices are either not yet needed (before 11:00) or already cached, the fetch is skipped entirely.
2. If a fetch is needed, each provider is tried in order (ENTSO-e → DAP Xadi). A provider that returns a successful response is not retried in subsequent attempts.
3. The data from all responding providers is merged. The integration stops early when coverage is sufficient.
4. If all providers fail, the integration retries up to 3 times (with a 2-second delay between attempts).
5. When all retries are exhausted and no new data was received, the integration continues running on the last successfully fetched data (degraded mode) as long as that data covers future hours. A persistent notification is created in Home Assistant to inform you of the failure.

The active provider(s) are exposed via the **Price data provider** sensor.

---

## Sensors

The integration creates the following sensors:

| Sensor | Description |
|--------|-------------|
| Current electricity price (all-in) | Current price with your price modifier and VAT applied |
| Current electricity spot price | Raw spot price without any modifier |
| Next hour electricity price (all-in) | Price for the next period (all-in) |
| Lowest energy price | Lowest price in the current calculation window |
| Highest energy price | Highest price in the current calculation window |
| Average electricity price | Average price in the current calculation window. Carries `prices`, `prices_today`, and `prices_tomorrow` attributes with timestamped price lists for use in graphs |
| Current percentage of highest electricity price | Current price as a percentage of the day's highest price |
| Current percentage in electricity price range | Current price as a percentage of the spread between lowest and highest price |
| Time of highest price | Timestamp of the most expensive hour |
| Time of lowest price | Timestamp of the cheapest hour |
| Price data provider | Name of the provider(s) that supplied the current data |

---

## Button

The integration adds a **Refresh prices** button entity. Pressing it immediately triggers a data fetch from all configured providers, bypassing the normal cache check. Use this to force an update after a known outage or to pull in tomorrow's prices as soon as they are published.

The button entity is named `Refresh prices` (or `Refresh prices (<name>)` when a custom entity name is configured).

---

## Installation

### HACS

Search for "Energy Spot Prices" when adding HACS integrations.

Restart Home Assistant and add the integration through Settings.

### Manual

Download this repository and place the contents of `custom_components` in your own `custom_components` folder of your Home Assistant installation. Restart Home Assistant and add the integration through your settings.

---

## Configuration

### Add integration

1. Go to **Settings → Devices & Services**
2. Click **+ Add integration**
3. Search for "Energy Spot Prices"
4. Follow the configuration flow

In the config flow you configure:
- **ENTSO-e API key** (optional — leave blank to use DAP Xadi only)
- **Country / bidding zone**
- **Price resolution** (60-minute or 15-minute intervals, where supported)
- **Price modifier template** and resulting currency (optional)
- **VAT** (optional)
- **Calculation mode** (see below)

### Cost modifier template

In the optional `Price Modifier Template` field you can specify a template to adjust the spot price — for example to add fixed network costs per kWh, apply VAT, or convert currencies. When left empty, the raw spot price in EUR/MWh is used (converted to your chosen energy scale).

In the template, `now()` always refers to the start of the hour for that price slot, and `current_price` is the spot price in your configured energy scale (e.g. EUR/kWh) before VAT.

An example template that applies different tariffs per season and time of day:

```
{% set s = {
    "extra_cost": 0.5352,
    "winter_night": 0.265,
    "winter_day": 0.465,
    "summer_day": 0.284,
    "summer_night": 0.246,
    "VAT": 0.21
}
%}
{% if now().month >= 5 and now().month <11 %}
    {% if now().hour >=6 and now().hour <23 %}
        {{(current_price + s.summer_day+s.extra_cost) * s.VAT | float}}
    {% else %}
        {{(current_price + s.summer_night + s.extra_cost) * s.VAT | float}}
    {% endif %}
{% else %}
    {% if now().hour >=6 and now().hour <23 %}
        {{(current_price + s.winter_day + s.extra_cost) * s.VAT | float}}
    {%else%}
        {{(current_price + s.winter_night + s.extra_cost) * s.VAT | float}}
    {% endif %}
{% endif %}
```

You can find and share other templates [here](https://github.com/JaccoR/hass-entso-e/discussions/categories/price-modifyer-templates).

### Calculation mode

Controls the time window used to compute min, max, and average prices:

- **Default (on publish)** — window updates when new data becomes available (usually between 12:00 and 15:00). Until tomorrow's prices are published, the latest 48 hours of data are used. Good for most use cases.

- **Sliding** — window is always the current hour and beyond (future prices only). The minimum at 13:00 is the cheapest hour still to come. Useful for scheduling loads as soon and cheaply as possible.

- **Rotation** — window resets at midnight to exactly today's 24 hours. At 23:59 the minimum is today's cheapest hour; at 00:00 it switches to tomorrow.

### ApexChart graph

Prices can be visualized with the [ApexChart Graph Card](https://github.com/RomRider/apexcharts-card). Example Lovelace configuration:

```yaml
type: custom:apexcharts-card
graph_span: 24h
span:
  start: day
now:
  show: true
  label: Now
header:
  show: true
  title: Electriciteitsprijzen Vandaag (€/kWh)
yaxis:
  - decimals: 2
series:
  # Use sensor.average_electricity_price when no name is configured,
  # or sensor.<name>_average_electricity_price with a custom name.
  - entity: sensor.average_electricity_price
    stroke_width: 2
    float_precision: 3
    type: column
    opacity: 1
    color: ''
    data_generator: |
      return entity.attributes.prices.map((entry) => {
        return [new Date(entry.time), entry.price];
      });
```

---

#### Updates

If you encounter an error after updating the integration, try removing and re-adding it through Settings. If the issue persists, please open an issue.
