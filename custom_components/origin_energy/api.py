"""Thin API client for Origin Energy's consumer BFF/GraphQL gateway.

Every endpoint here was reverse-engineered from a captured browser
session and is undocumented, unsupported, and subject to change without
notice. Only the handful of endpoints actually seen in that capture are
wired up - see the integration's README for what's still missing
(notably: any interval/consumption usage data).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiohttp

from .auth import OriginEnergyAuth
from .const import API_BASE, EV_ICHARGE_BASE, GRAPHQL_URL

_LOGGER = logging.getLogger(__name__)

# Trimmed down to the fields this integration currently uses. The real
# query captured in the HAR asks for a lot more (bills, product/agreement
# detail, address, etc.) - extend this as needed.
ACCOUNT_DETAILS_QUERY = """
query AccountDetailsQuery($type: ServiceType!) {
  viewer {
    kraken {
      accounts {
        accountNumber
        accountState
        services {
          ... on ElectricityService {
            agreementId
            active
            meterPoint {
              identifier
              meters {
                meterCapabilityLevel
                active
              }
            }
          }
        }
      }
    }
  }
}
"""

# Trimmed to the ElectricityService branch only (this integration doesn't
# handle gas), and to the fields actually used. The real captured query
# also covers GasService/EmbeddedElectricityService/EmbeddedHotWaterService
# branches and a `product.rates` sub-selection this integration doesn't
# use - dropped for a smaller request.
ACCOUNT_USAGE_QUERY = """
query AccountUsageQuery($agreementId: String!, $type: ServiceType!, $timeUnit: UsageTimeUnit!, $startDate: DateTime!, $endDate: DateTime!) {
  viewer {
    kraken {
      service(type: $type, agreementId: $agreementId) {
        ... on ElectricityService {
          usage(timeUnit: $timeUnit, startDate: $startDate, endDate: $endDate, orderBy: START_DATE_ASC) {
            hasMissingCosts
            hasFeedIn
            lastReadingDate
            unitsOfMeasure {
              totalCost
              usageCost
              supplyCost
              totalConsumedEnergy
              totalEarnings
              totalFeedInEnergy
            }
            dataPoints {
              type
              startDate
              endDate
              totalConsumedEnergy
              totalCost
              usageCost
              supplyCost
              totalEarnings
              totalFeedInEnergy
            }
          }
        }
      }
    }
  }
}
"""

# Origin has been observed reporting these two unit families (WATT_HOUR /
# CENT). Converts to the integration's standard units (kWh / AUD)
# regardless of which one Origin says it's using, rather than assuming
# WATT_HOUR/CENT is permanent - an unrecognised unit is passed through
# unconverted (and logged) rather than silently mis-scaled.
_ENERGY_TO_KWH = {"WATT_HOUR": 1 / 1000, "KILOWATT_HOUR": 1}
_MONEY_TO_AUD = {"CENT": 1 / 100, "DOLLAR": 1}


def _convert(value: float | None, unit: str | None, table: dict[str, float]) -> float | None:
    if value is None:
        return None
    factor = table.get(unit)
    if factor is None:
        _LOGGER.warning("Unrecognised usage unit %r - returning unconverted value", unit)
        return value
    return round(value * factor, 4)


class OriginEnergyApiError(Exception):
    """Raised for non-auth API failures (bad HTTP status, malformed body)."""


class OriginEnergyApiClient:
    """Wraps the REST "BFF" endpoints and the GraphQL gateway."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: OriginEnergyAuth,
        origin_account_id: str,
    ) -> None:
        self._session = session
        self._auth = auth
        self._origin_account_id = origin_account_id

    # -- REST "BFF" endpoints -------------------------------------------------

    async def async_get_account_info(self) -> dict[str, Any]:
        url = (
            f"{API_BASE}/energy-widgets-bff-api/"
            f"{self._origin_account_id}/account-info"
        )
        return await self._async_get(url)

    async def async_get_billing_details(self) -> dict[str, Any]:
        url = (
            f"{API_BASE}/billing-payments-bff/"
            f"{self._origin_account_id}/account-billing-details"
        )
        return await self._async_get(url)

    async def async_get_upcoming_bill(self) -> dict[str, Any]:
        url = (
            f"{API_BASE}/billing-payments-bff/"
            f"{self._origin_account_id}/upcoming-bill"
        )
        return await self._async_get(url)

    async def async_get_transactions(self) -> list[Any]:
        url = (
            f"{API_BASE}/billing-payments-bff/"
            f"{self._origin_account_id}/transactions"
        )
        result = await self._async_get(url)
        return result if isinstance(result, list) else result.get("data", [])

    # -- GraphQL ----------------------------------------------------------

    async def async_get_account_details_graphql(self) -> dict[str, Any]:
        payload = {
            "operationName": "AccountDetailsQuery",
            "variables": {"type": "ELECTRICITY"},
            "query": ACCOUNT_DETAILS_QUERY,
        }
        return await self._async_post_graphql(payload)

    async def async_get_daily_usage(
        self, agreement_id: str, start_date: datetime, end_date: datetime
    ) -> dict[str, Any] | None:
        """Electricity usage for [start_date, end_date], DAILY granularity.

        Returns a dict with values already converted to kWh/AUD (see
        _convert above), taking the LAST data point in range - i.e. the
        most recent day - since callers use this for "today's" figures.
        Returns None if there's no data point in range yet (e.g. queried
        before the first meter read has landed).

        usage_cost_aud and earnings_aud can legitimately be None even
        with real consumed_kwh/feed_in_kwh present - Origin flags this
        itself via hasMissingCosts when its own cost reconciliation
        hasn't caught up yet (observed on a brand-new account: real kWh
        figures within a day, but null costs for several days after).
        """
        payload = {
            "operationName": "AccountUsageQuery",
            "variables": {
                "agreementId": agreement_id,
                "type": "ELECTRICITY",
                "timeUnit": "DAILY",
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
            "query": ACCOUNT_USAGE_QUERY,
        }
        result = await self._async_post_graphql(payload)
        service = ((result.get("viewer") or {}).get("kraken") or {}).get(
            "service"
        ) or {}
        usage = service.get("usage")
        if not usage:
            return None

        data_points = usage.get("dataPoints") or []
        if not data_points:
            return {
                "has_missing_costs": usage.get("hasMissingCosts"),
                "has_feed_in": usage.get("hasFeedIn"),
                "last_reading_date": usage.get("lastReadingDate"),
                "consumed_kwh": None,
                "feed_in_kwh": None,
                "total_cost_aud": None,
                "usage_cost_aud": None,
                "supply_cost_aud": None,
                "earnings_aud": None,
            }

        latest = data_points[-1]
        uom = usage.get("unitsOfMeasure") or {}
        return {
            "has_missing_costs": usage.get("hasMissingCosts"),
            "has_feed_in": usage.get("hasFeedIn"),
            "last_reading_date": usage.get("lastReadingDate"),
            "period_start": latest.get("startDate"),
            "period_end": latest.get("endDate"),
            "consumed_kwh": _convert(
                latest.get("totalConsumedEnergy"),
                uom.get("totalConsumedEnergy"),
                _ENERGY_TO_KWH,
            ),
            "feed_in_kwh": _convert(
                latest.get("totalFeedInEnergy"),
                uom.get("totalFeedInEnergy"),
                _ENERGY_TO_KWH,
            ),
            "total_cost_aud": _convert(
                latest.get("totalCost"), uom.get("totalCost"), _MONEY_TO_AUD
            ),
            "usage_cost_aud": _convert(
                latest.get("usageCost"), uom.get("usageCost"), _MONEY_TO_AUD
            ),
            "supply_cost_aud": _convert(
                latest.get("supplyCost"), uom.get("supplyCost"), _MONEY_TO_AUD
            ),
            "earnings_aud": _convert(
                latest.get("totalEarnings"), uom.get("totalEarnings"), _MONEY_TO_AUD
            ),
        }

    # -- EV iCharge / EV Power Up -----------------------------------------
    # Same-origin on www.originenergy.com.au (NOT the api.rx.* gateway),
    # but uses the same Bearer token. Returns whatever vehicles/schedules
    # are actually linked to the account - callers should not assume a
    # fixed number or set of vehicles.

    async def async_get_ev_vehicles(self) -> list[dict[str, Any]]:
        """All EV iCharge vehicles linked to this account, whatever they are."""
        result = await self._async_get(
            f"{EV_ICHARGE_BASE}/vehicles", headers_kind="ev"
        )
        return result if isinstance(result, list) else result.get("data", [])

    async def async_get_ev_charge_cost_dashboard(self) -> dict[str, Any]:
        return await self._async_get(
            f"{EV_ICHARGE_BASE}/charge-cost/dashboard", headers_kind="ev"
        )

    async def async_set_ev_target_charge_time(
        self, vehicle_id: str, schedule_id: str, target_charge_time: str
    ) -> None:
        """Set a vehicle's target-charge-complete-by time.

        target_charge_time must be "HH:MM:SS" (24h), matching what the
        portal itself sends (e.g. "05:30:00"). Origin returns 204 with no
        body on success - there is nothing meaningful to return here.
        """
        url = f"{EV_ICHARGE_BASE}/vehicles/{vehicle_id}/charge-schedule/{schedule_id}"
        payload = {
            "vehicleId": vehicle_id,
            "scheduleId": schedule_id,
            "targetChargeTime": target_charge_time,
        }
        await self._async_write("PATCH", url, json_body=payload)

    async def async_start_ev_instant_charge(self, vehicle_id: str) -> None:
        """Start "Instant Charge" - bypasses the scheduled/cheapest-time
        charging and charges immediately at full rate. Confirmed against
        a real start/stop cycle: empty-body POST, 204 on success."""
        url = f"{EV_ICHARGE_BASE}/vehicles/{vehicle_id}/charge-override/active"
        await self._async_write("POST", url)

    async def async_stop_ev_instant_charge(self, vehicle_id: str) -> None:
        """Cancel Instant Charge, reverting to the scheduled/optimised plan."""
        url = f"{EV_ICHARGE_BASE}/vehicles/{vehicle_id}/charge-override/active"
        await self._async_write("DELETE", url)

    # -- internals ----------------------------------------------------------

    async def _async_write(
        self, method: str, url: str, json_body: dict[str, Any] | None = None
    ) -> None:
        """POST/PATCH/DELETE that returns no useful body (204 on success).

        Every EV iCharge write observed so far behaves this way - nothing
        to parse out, just confirm the status code.
        """
        headers = await self._headers(kind="ev")
        async with self._session.request(
            method, url, headers=headers, json=json_body
        ) as resp:
            if resp.status not in (200, 204):
                body = await resp.text()
                raise OriginEnergyApiError(
                    f"{method} {url} failed: HTTP {resp.status} - {body[:200]}"
                )


    async def _headers(self, kind: str = "bff") -> dict[str, str]:
        token = await self._auth.async_get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            # Same reasoning as auth.py: Origin's domains sit behind
            # CloudFront/WAF and block requests that don't look like they
            # came from a browser XHR/fetch call. Copied from real Chrome
            # requests that succeeded.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-AU,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
        }
        if kind == "ev":
            # EV iCharge is same-origin on www.originenergy.com.au, not a
            # separate subdomain, so these differ slightly from the BFF
            # ones below (matches what a real browser sent).
            headers["Origin"] = "https://www.originenergy.com.au"
            headers["Referer"] = "https://www.originenergy.com.au/my/"
            headers["Sec-Fetch-Site"] = "same-origin"
        else:
            headers["Origin"] = "https://www.originenergy.com.au"
            headers["Referer"] = "https://www.originenergy.com.au/"
            headers["Sec-Fetch-Site"] = "same-site"
        return headers

    async def _async_get(
        self, url: str, headers_kind: str = "bff"
    ) -> dict[str, Any]:
        headers = await self._headers(kind=headers_kind)
        async with self._session.get(url, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise OriginEnergyApiError(
                    f"GET {url} failed: HTTP {resp.status} - {body[:200]}"
                )
            data = await resp.json()
        return data.get("data", data)

    async def _async_post_graphql(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = await self._headers()
        async with self._session.post(
            GRAPHQL_URL, headers=headers, json=payload
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise OriginEnergyApiError(
                    f"GraphQL request failed: HTTP {resp.status} - {body[:200]}"
                )
            data = await resp.json()
        if "errors" in data and data["errors"]:
            raise OriginEnergyApiError(f"GraphQL errors: {data['errors']}")
        return data.get("data", {})
