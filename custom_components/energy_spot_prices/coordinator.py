from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import aiohttp
import async_timeout
import homeassistant.helpers.config_validation as cv
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.template import Template
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt
from jinja2 import pass_context

from .const import AREA_INFO, CALCULATION_MODE, DEFAULT_MODIFYER, DOMAIN, ENERGY_SCALES
from .providers.base import PriceProvider
from .utils import get_interval_minutes, bucket_time

# depending on timezone les than 24 hours could be returned.
MIN_HOURS = 20

# how many times to retry fetching prices (across all providers) before logging
# the failure and notifying the user, and how long to wait between rounds.
FETCH_RETRY_ATTEMPTS = 3
FETCH_RETRY_DELAY_SECONDS = 2


# This class contains actually two main tasks
# 1. ENTSO: Refresh data from ENTSO on interval basis triggered by HASS every 60 minutes
# 2. ANALYSIS:  Implement some analysis on this data, like min(), max(), avg(), perc(). Updated analysis is triggered by an explicit call from a sensor
class EntsoeCoordinator(DataUpdateCoordinator):
    """Get the latest data and update the states."""

    def __init__(
            self,
            hass: HomeAssistant,
            providers: list[PriceProvider],
            area,
            period,
            energy_scale,
            modifyer,
            calculation_mode=CALCULATION_MODE["default"],
            VAT=0,
    ) -> None:
        """Initialize the data object."""
        self.hass = hass
        self.providers = providers
        self.modifyer = modifyer
        self.period = period
        self.period_minutes = get_interval_minutes(period)
        self.area = AREA_INFO[area]["code"]
        self.energy_scale = energy_scale
        self.calculation_mode = calculation_mode
        self.vat = VAT
        self.calculator_last_sync = None
        self.filtered_hourprices = []
        self.lock = asyncio.Lock()
        self._last_cleanup_date = None
        self.raw_hourprices = {}
        self._force_update = False
        self.current_provider_name: str | None = None
        self._missing_data_notification_id = f"{DOMAIN}_missing_today_data_{self.area}"
        self._fetch_failed_notification_id = f"{DOMAIN}_fetch_failed_{self.area}"

        # Check incase the sensor was setup using config flow.
        # This blow up if the template isnt valid.
        if not isinstance(self.modifyer, Template):
            if self.modifyer in (None, ""):
                self.modifyer = DEFAULT_MODIFYER
            self.modifyer = cv.template(self.modifyer)
        # check for yaml setup.
        else:
            if self.modifyer.template in ("", None):
                self.modifyer = cv.template(DEFAULT_MODIFYER)

        logger = logging.getLogger(__name__)
        super().__init__(
            hass,
            logger,
            name="Energy Spot Prices coordinator",
            update_interval=timedelta(minutes=self.period_minutes),
        )

    # ENTSO: recalculate the price using the given template
    def calc_price(self, value, fake_dt=None, no_template=False) -> float:
        """Calculate price based on the users settings."""
        # Used to inject the current hour.
        # so template can be simplified using now
        if no_template:
            price = round(value / ENERGY_SCALES[self.energy_scale], 5)
            return price

        price = value / ENERGY_SCALES[self.energy_scale]
        if fake_dt is not None:

            def faker():
                def inner(*args, **kwargs):
                    return fake_dt

                return pass_context(inner)

            template_kwargs = {"now": faker(), "current_price": price}
        else:
            template_kwargs = {"current_price": price}

        try:
            template_value = self.modifyer.async_render(**template_kwargs)
        except Exception as exc:
            self.logger.error(
                f"Failed to render price modifier template '{self.modifyer.template}'. "
                f"Please check your price modifier template. Error: {exc}"
            )
            raise

        try:
            price = round(float(template_value) * (1 + self.vat), 5)
        except (ValueError, TypeError) as exc:
            self.logger.error(
                f"Failed to convert template result '{template_value}' to float. "
                f"Please check your price modifier template. Error: {exc}"
            )
            raise

        return price

    # ENTSO: recalculate the price for each price
    def parse_hourprices(self, hourprices):
        return {hour: self.calc_price(value=price, fake_dt=hour) for hour, price in hourprices.items()}

    # ENTSO: Triggered by HA to refresh the data (interval = 60 minutes)
    async def _async_update_data(self) -> dict:
        """Get the latest data from configured providers"""
        self.logger.debug("Energy Spot Prices coordinator data update")
        self.logger.debug(self.area)

        now = dt.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.check_update_needed(now) is False:
            self.logger.debug("Skipping api fetch. All data is already available")
            return self.data
        self._force_update = False

        yesterday = today - timedelta(days=1)
        tomorrow_evening = yesterday + timedelta(hours=71)

        self.logger.debug(f"fetching prices for start date: {yesterday} to end date: {tomorrow_evening}")
        data = await self.fetch_prices(yesterday, tomorrow_evening)
        self.logger.debug(f"received data = {data}")


        if data is not None:
            if data is self.data:
                # Degraded mode returned cached data; don't re-parse already-processed prices
                self.logger.debug("Using cached data from degraded mode")
                return data
            new_raw = {k: round(v / ENERGY_SCALES[self.energy_scale], 5) for k, v in data.items()}
            if self.raw_hourprices:
                self.raw_hourprices.update(new_raw)
            else:
                self.raw_hourprices = new_raw

            parsed_data = self.parse_hourprices(data)
            self.logger.debug(
                f"received pricing data for {len(data)} hours; "
                f"total after merge: {len((self.data or {}) | parsed_data)} hours"
            )
            if self.data:
                self.data.update(parsed_data)
            else:
                self.data = parsed_data
            if len(self.get_data_today()) < MIN_HOURS:
                message = (
                    f"No price data available for today ({self.today.date()}). "
                    "Sensors will stay unavailable until today's prices are published."
                )
                self.logger.warning(message)
                persistent_notification.async_create(
                    self.hass,
                    message,
                    title="Energy Spot Prices: missing today's prices",
                    notification_id=self._missing_data_notification_id,
                )
            else:
                persistent_notification.async_dismiss(self.hass, self._missing_data_notification_id)
            return parsed_data
        
        # Return existing data if available, otherwise empty dict
        return self.data if self.data is not None else {}

    # ENTSO: check if we need to refresh the data. If we have None, or less than 20hrs left for today, or less than 20hrs tomorrow and its after 11
    def check_update_needed(self, now):
        if self._force_update:
            return True
        if self.data is None:
            return True
        if len(self.get_data_today()) < MIN_HOURS:
            return True
        if len(self.get_data_tomorrow()) < MIN_HOURS and now.hour > 11:
            return True
        return False

    # SENSOR: Manually triggered refresh, bypassing the update-needed check above
    async def async_force_update(self) -> None:
        """Force a refresh of the ENTSO-e prices, ignoring the cached-data check."""
        self._force_update = True
        await self.async_request_refresh()

    # Fetch prices by calling all configured providers and merging their results.
    # Providers that successfully respond are not retried (their data won't change);
    # only providers that raised network exceptions are retried in subsequent attempts.
    # We stop early when combined data is sufficient, or when all providers have
    # responded (no point retrying). Tomorrow's coverage is only required after 11:00.
    async def fetch_prices(self, start_date, end_date):
        now = dt.now()
        today_date = self.today.date()
        tomorrow_date = (self.today + timedelta(days=1)).date()
        tomorrow_needed = now.hour > 11

        combined: dict = {}
        providers_done: set = set()  # provider_ids that returned a response (even empty)
        exc = None

        for attempt in range(1, FETCH_RETRY_ATTEMPTS + 1):
            for provider in self.providers:
                if provider.provider_id in providers_done:
                    continue  # already have their data; only retry network failures

                self.logger.debug(
                    f"Fetching prices from provider '{provider.name}' "
                    f"(attempt {attempt}/{FETCH_RETRY_ATTEMPTS})"
                )
                try:
                    async with async_timeout.timeout(10):
                        result = await provider.query_day_ahead_prices(
                            area=self.area, start=start_date, end=end_date
                        )
                except Exception as provider_exc:
                    exc = provider_exc
                    if isinstance(exc, aiohttp.ClientResponseError) and exc.status == 401:
                        message = (
                            f"The API key for provider '{provider.name}' was rejected. "
                            "Please check your API key in the integration settings."
                        )
                        self.logger.error(message)
                        persistent_notification.async_create(
                            self.hass,
                            message,
                            title="Energy Spot Prices: invalid API key",
                            notification_id=self._fetch_failed_notification_id,
                        )
                        raise UpdateFailed("Unauthorized: Please check your API-key.") from exc
                    self.logger.debug(
                        f"Provider '{provider.name}' failed (attempt {attempt}/{FETCH_RETRY_ATTEMPTS}): {exc}"
                    )
                    continue  # will be retried on next attempt

                # Merge this provider's data into the combined result
                combined.update(result)
                providers_done.add(provider.provider_id)
                today_h = sum(1 for k in result if k.astimezone().date() == today_date)
                tomorrow_h = sum(1 for k in result if k.astimezone().date() == tomorrow_date)
                self.logger.debug(
                    f"Provider '{provider.name}' returned {len(result)} entries "
                    f"({today_h}h today, {tomorrow_h}h tomorrow); "
                    f"combined total: {len(combined)}"
                )

            # Evaluate combined coverage after this round
            today_hours = sum(1 for k in combined if k.astimezone().date() == today_date)
            tomorrow_hours = sum(1 for k in combined if k.astimezone().date() == tomorrow_date)
            today_ok = today_hours >= MIN_HOURS
            tomorrow_ok = not tomorrow_needed or tomorrow_hours >= MIN_HOURS

            if today_ok and tomorrow_ok:
                # Full coverage — success
                persistent_notification.async_dismiss(self.hass, self._fetch_failed_notification_id)
                self.current_provider_name = " + ".join(
                    p.name for p in self.providers if p.provider_id in providers_done
                ) or None
                return combined

            # If all providers have already responded, no point doing more rounds
            if len(providers_done) >= len(self.providers):
                self.logger.info(
                    f"All providers exhausted after attempt {attempt}: "
                    f"{today_hours}h today, {tomorrow_hours}h tomorrow. "
                    f"Returning combined data ({len(combined)} entries)."
                )
                if combined:
                    self.current_provider_name = " + ".join(
                        p.name for p in self.providers if p.provider_id in providers_done
                    ) or None
                    return combined
                break  # all responded but nothing — fall through to degraded mode

            if attempt < FETCH_RETRY_ATTEMPTS:
                await asyncio.sleep(FETCH_RETRY_DELAY_SECONDS)

        # Partial combined data after all retries (some providers had network errors)
        if combined:
            self.logger.info(
                f"Returning partial combined data after {FETCH_RETRY_ATTEMPTS} attempts "
                f"({len(combined)} entries; some providers may still be unavailable)."
            )
            self.current_provider_name = " + ".join(
                p.name for p in self.providers if p.provider_id in providers_done
            ) or None
            return combined

        # No data from any provider — fall back to cached data or raise
        if self.data is not None and len(self.data) > 0:
            newest_timestamp = max(self.data.keys())
            if newest_timestamp > dt.now():
                message = (
                    f"Failed to fetch prices after {FETCH_RETRY_ATTEMPTS} attempts. "
                    f"Running in degraded mode (falling back on stored data). "
                    f"Last error: {exc}."
                )
                self.logger.warning(message)
                persistent_notification.async_create(
                    self.hass,
                    message,
                    title="Energy Spot Prices: fetching prices failed",
                    notification_id=self._fetch_failed_notification_id,
                )
                return self.data
            else:
                message = (
                    f"Failed to fetch prices after {FETCH_RETRY_ATTEMPTS} attempts and "
                    f"cached data is stale. Sensors will no longer update. Last error: {exc}."
                )
                self.logger.error(message)
                persistent_notification.async_create(
                    self.hass,
                    message,
                    title="Energy Spot Prices: fetching prices failed",
                    notification_id=self._fetch_failed_notification_id,
                )
                raise UpdateFailed(message) from exc
        else:
            message = (
                f"All price providers failed after {FETCH_RETRY_ATTEMPTS} attempts. "
                f"Last error: {exc}."
            )
            self.logger.error(message)
            persistent_notification.async_create(
                self.hass,
                message,
                title="Energy Spot Prices: fetching prices failed",
                notification_id=self._fetch_failed_notification_id,
            )
            raise UpdateFailed("Fetching spot prices failed.") from exc

    @property
    def today(self):
        return dt.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # ENTSO: Return the data for the given date
    def get_data(self, date):
        if self.data is None:
            return {}
        target_date = date.date()
        return {k: v for k, v in self.data.items() if k.astimezone().date() == target_date}

    # ENTSO: Return the data for today
    def get_data_today(self):
        return self.get_data(self.today)

    # ENTSO: Return the data for tomorrow
    def get_data_tomorrow(self):
        return self.get_data(self.today + timedelta(days=1))

    # ENTSO: Return the data for yesterday
    def get_data_yesterday(self):
        return self.get_data(self.today - timedelta(days=1))

    # SENSOR: Do we have data available for today
    def today_data_available(self):
        return len(self.get_data_today()) > MIN_HOURS

    @property
    def current_bucket_time(self):
        return bucket_time(dt.now(), self.period_minutes)

    # SENSOR: Get the current price
    def get_current_price(self) -> float | None:
        return self.data.get(self.current_bucket_time)
    
    # SENSOR: Get the current raw (unmodified) price in kWh
    def get_current_raw_price(self) -> float | None:
        return self.raw_hourprices.get(self.current_bucket_time)

    # SENSOR: Get the next hour price
    def get_next_price(self) -> float | None:
        return self.data.get(
            self.current_bucket_time + timedelta(minutes=self.period_minutes)
        )
    # SENSOR: Get timestamped prices of today as attribute for Average Sensor
    def get_prices_today(self):
        return self.get_timestamped_prices(self.get_data_today())

    # SENSOR: Get timestamped prices of tomorrow as attribute for Average Sensor
    def get_prices_tomorrow(self):
        return self.get_timestamped_prices(self.get_data_tomorrow())

    # SENSOR: Get timestamped prices of today & tomorrow or yesterday & today as attribute for Average Sensor
    def get_prices(self):
        if len(self.data) > 48:
            return self.get_timestamped_prices(
                {hour: price for hour, price in self.data.items() if hour >= self.today}
            )
        return self.get_timestamped_prices(
            {
                hour: price
                for hour, price in self.data.items()
                if hour >= self.today - timedelta(days=1)
            }
        )

    # SENSOR: Timestamp the prices
    def get_timestamped_prices(self, hourprices):
        list = []
        for hour, price in hourprices.items():
            str_hour = str(hour)
            entry = {"time": str_hour, "spot_price": self.raw_hourprices.get(hour), "price": price}
            list.append(entry)
        return list

    # --------------------------------------------------------------------------------------------------------------------------------
    # ANALYSIS: this method is called by each sensor, each complete hour, and ensures the date and filtered hourprices are in line with the current time
    # we could still optimize as not every calculator mode needs hourly updates
    async def sync_calculator(self):
        now = dt.now()
        bucket = self.current_bucket_time
        async with self.lock:
            if (
                self.calculator_last_sync is None
                or self.calculator_last_sync != bucket
            ):
                self.logger.debug(
                    "The calculator needs to be synced with the current time"
                )
                if not self.data:
                    self.logger.debug("no data available yet, fetching data")
                    try:
                        await self._async_update_data()
                    except UpdateFailed as exc:
                        self.logger.warning(
                            f"Failed to fetch initial data during calculator sync: {exc}"
                        )
                        return

                current_date = now.date()
                if self._last_cleanup_date is None or self._last_cleanup_date != current_date:
                    self.logger.debug(
                        "new day detected: update today and filtered hourprices"
                    )
                    self._last_cleanup_date = current_date

                    # remove stale data
                    self.data = {
                        hour: price
                        for hour, price in self.data.items()
                        if hour >= self.today - timedelta(days=1)
                    }

                    self.raw_hourprices = {
                        hour: price
                        for hour, price in self.raw_hourprices.items()
                        if hour >= self.today - timedelta(days=1)
                    }

            self.calculator_last_sync = bucket

    # ANALYSIS: filter the prices on which to apply the calculations based on the calculation_mode
    @property
    def _filtered_prices(self) -> dict:
        """
        Filter the prices based on the calculation mode.
        """
        # rotation = calculations made upon 24hrs today
        if self.calculation_mode == CALCULATION_MODE["rotation"]:
            return {
                ts: price
                for ts, price in self.data.items()
                if self.today <= ts < self.today + timedelta(days=1)
            }
        # sliding = calculations made on all data from the current bucket and beyond (future data only)
        elif self.calculation_mode == CALCULATION_MODE["sliding"]:
            return {ts: price for ts, price in self.data.items() if ts >= self.current_bucket_time}
        # publish >48 hrs of data = calculations made on all data of today and tomorrow (48 hrs)
        elif (
                self.calculation_mode == CALCULATION_MODE["publish"] and len(self.data) > 48
        ):
            return {ts: price for ts, price in self.data.items() if ts >= self.today}
        # publish <=48 hrs of data = calculations made on all data of yesterday and today (48 hrs)
        elif self.calculation_mode == CALCULATION_MODE["publish"]:
            return {
                ts: price
                for ts, price in self.data.items()
                if ts >= self.today - timedelta(days=1)
            }

        self.logger.error("Unknown calculation mode, returning empty filtered prices")
        return {}

    # ANALYSIS: Get max price in filtered period
    def get_max_price(self):
        prices = self._filtered_prices
        if not prices:
            return None
        return max(prices.values())

    # ANALYSIS: Get min price in filtered period
    def get_min_price(self):
        prices = self._filtered_prices
        if not prices:
            return None
        return min(prices.values())

    # ANALYSIS: Get timestamp of max price in filtered period
    def get_max_time(self):
        prices = self._filtered_prices
        if not prices:
            return None
        return max(prices, key=prices.get)

    # ANALYSIS: Get timestamp of min price in filtered period
    def get_min_time(self):
        prices = self._filtered_prices
        if not prices:
            return None
        return min(prices, key=prices.get)

    # ANALYSIS: Get avg price in filtered period
    def get_avg_price(self):
        prices = self._filtered_prices
        if not prices:
            return None
        return round(
            sum(prices.values()) / len(prices.values()),
            5,
        )

    # ANALYSIS: Get percentage of current price relative to maximum of filtered period
    def get_percentage_of_max(self):
        current_price = self.get_current_price()
        max_price = self.get_max_price()
        if current_price is None or max_price is None or max_price == 0:
            return None
        return round(current_price / max_price * 100, 1)

    # ANALYSIS: Get percentage of current price relative to spread (max-min) of filtered period
    def get_percentage_of_range(self):
        current_price = self.get_current_price()
        min_price = self.get_min_price()
        max_price = self.get_max_price()
        if current_price is None or min_price is None or max_price is None:
            return None
        spread = max_price - min_price
        if spread == 0:
            return 100.0  # If all prices are the same, current is always 100% of range
        current = current_price - min_price
        return round(current / spread * 100, 1)

    # --------------------------------------------------------------------------------------------------------------------------------
    # SERVICES: returns data from the coordinator cache, or directly from ENTSO when not availble
    async def get_energy_prices(self, start_date, end_date):
        # check if we have the data already
        if (
                len(self.get_data(start_date)) > MIN_HOURS
                and len(self.get_data(end_date)) > MIN_HOURS
        ):
            self.logger.debug("return prices from coordinator cache.")
            return {
                k: v
                for k, v in self.data.items()
                if k.date() >= start_date.date() and k.date() <= end_date.date()
            }
        try:
            data = await self.fetch_prices(start_date, end_date)
        except UpdateFailed as exc:
            raise HomeAssistantError(
                f"Failed to fetch energy prices from ENTSO-e: {exc}"
            ) from exc
        if data is self.data:
            # Degraded mode: return filtered cached data (already parsed)
            return {
                k: v
                for k, v in data.items()
                if k.date() >= start_date.date() and k.date() <= end_date.date()
            }

        # Update raw_hourprices with the newly fetched data
        self.raw_hourprices.update(
            {k: round(v / ENERGY_SCALES[self.energy_scale], 5) for k, v in data.items()}
        )

        return self.parse_hourprices(data)
