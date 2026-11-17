# Day/night support in this fork

This fork retains Mercury's separate **Day** and **Night** prices instead of
discarding them when a plan has no Anytime price. It adds:

- Day Rate and Night Rate (NZD/kWh).
- Active Tariff and a Current Rate that switches locally, within one minute of
  a configured boundary. It does not need a new Mercury request to switch.
- Latest Complete Day Daytime Usage and Nighttime Usage. Attributes include the
  date, covered hours, estimated-hour count, schedule, and statistic IDs.
- Two external long-term statistics series for calculated day/night kWh.
- Latest Usage Reading and Latest Usage Read Type (actual/estimated when supplied).
- Meter Registers: attributes preserve each meter/register's reading, reading
  date and read type. These are delayed billed readings, not live counters.
- Plan attributes preserve the full unit-rate table (including unrecognised
  tariff names), generation rates, other charges, plan code, time-of-use flag,
  and pending-change date. Generation rates may have different GST treatment;
  check Mercury's plan display before comparing them with import rates.

## Configure a schedule

Go to **Settings → Devices & services → Mercury CO NZ → Configure**. Keep or
change the existing ICP selection, then select a meter under **Configure day/night
schedule for meter**. Each meter has independent settings.

The schedule is **disabled by default**. First confirm your tariff hours with
Mercury. The form's suggested 21:00–07:00 is not a claim about your account.
Choose the start date over which this same schedule is valid and choose either
NZ local time (including daylight saving) or fixed NZ standard time (UTC+12).
Prices are displayed without a schedule; a day/night plan's Current Rate and
calculated splits remain unknown until configured.

Classification uses recorded hourly kWh, not the provider's cost divided by
consumption. Only whole-hour boundaries and one daily schedule are supported.
Flex's half-hour boundaries, weekend exceptions, and changes of schedule within
the chosen history period require more detailed source data or future support.
Do not represent them using an approximate schedule.

## Historical data and accuracy

Actual hourly source records are retained privately in HA's `.storage`, scoped
to the config entry and meter, with no automatic expiry. The initial import
includes the integration's available hourly cache; subsequent polls add new
hours and replace revised readings. This feature does not itself download an
account's entire history. A local archive can feed real records through
`coordinator.async_import_tariff_history(service_id, records, combined_data)`.

No daily total is interpolated into hours. Missing hours remain missing in the
statistics. The latest complete-day sensors require every hour of that local
day: 23, 24 or 25 hours depending on daylight saving. Repeated autumn hours stay
distinct. Mercury's nonexistent spring-hour placeholder is excluded.

The statistics are **calculated classifications**, not Mercury's billed register
breakdown. Current prices are never applied retrospectively to historical usage.
Existing provider cost statistics are untouched and are not certified as billing
costs by this feature. Meter register 1 or 2 is never guessed to mean Day or Night.

Corrected readings rebuild the cumulative series without double counting. A
changed schedule creates a distinct pair of statistic IDs, leaving the old pair
available. Disabling classification stops imports without deleting history.
The first import after startup occurs on a regular coordinator refresh.

For the Energy dashboard, use **either** the existing combined consumption series
**or** the day and night pair. Adding all three double-counts electricity. After
changing a schedule, select the new pair shown in the usage sensors' attributes.

## Installation and local modifications

Install the `custom_components/mercury_co_nz` directory from this fork and reload
the integration/restart HA. Preserve any locally added archive extensions before
replacing files. The integration domain and existing entity/statistic IDs remain
unchanged. HACS updates from the original repository can overwrite this fork;
use this fork as the update source if you want to retain these features.

No account credentials, meter IDs, account history, or local archive are included
in this repository. Tests use synthetic records.
