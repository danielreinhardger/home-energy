"""What a kWh of PV is worth in each use (pure, no I/O).

Every kWh of PV that is not used in the house either earns the feed-in tariff or
replaces a kWh that would otherwise have to come from the grid later. The value
of a use is therefore

    value [€/kWh PV] = price of the grid power it replaces later × efficiency of the detour

compared with the feed-in tariff. The efficiencies use the heat pump's own lifetime
counters (COP heating ≈ 4.6, COP hot water ≈ 2.6 on this site).

Key consequence (docs/strategy.md): storing heat only pays if that heat is needed
*before the next PV window*. Hot water heated beyond what lasts until tomorrow's sun
just displaces tomorrow's free PV (worth only the feed-in tariff) at a worse COP – so
the old "55 °C on every sunny day" boost is gone. Space heating in winter has the best
ratio: COP 4.6 instead of 2.6, and a KfW40+ house loses only ~1–1.5 K over a night.
"""
from __future__ import annotations

from dataclasses import dataclass
from .i18n import fnum, t
from .settings import hidden, num

COP_DROP_PER_K = 0.03          # heat pump COP falls ~3 % per K of higher sink temperature


@dataclass
class EconomyConfig:
    # €/kWh bought from the grid
    price_buy: float = num(0.35, '€/kWh', 0.05, 1, 0.001)
    # €/kWh feed-in tariff
    price_feed: float = num(0.092, '€/kWh', 0, 0.5, 0.001)
    # round trip
    battery_efficiency: float = num(0.90, '', 0.5, 1, 0.01)
    # fallback; live value from the heat pump counters is preferred
    cop_heating: float = num(4.6, '', 1.5, 7, 0.1)
    cop_dhw: float = num(2.6, '', 1.2, 5, 0.1)
    # COP is higher at midday (warmer outside air) than at night
    day_cop_gain: float = num(0.08, '', 0, 0.5, 0.01)
    # extra building losses while the house is 1 K warmer
    heat_loss_share: float = hidden(0.08, why='model constant')
    # extra standing losses of a hotter tank
    tank_loss_share: float = hidden(0.10, why='model constant')

    def validate(self) -> None:
        if not 0 < self.price_feed < self.price_buy:
            raise ValueError(t("eco.einspeiseverguetung_muss_zwischen_0_und"))
        if not 0.5 <= self.battery_efficiency <= 1:
            raise ValueError(t("eco.batteriewirkungsgrad_0_5_bis_1"))


@dataclass
class Use:
    key: str
    label: str
    value: float                 # €/kWh of PV
    note: str
    gain_vs_feed: float = 0.0    # €/kWh more than feeding in

    def __post_init__(self):
        self.value = round(self.value, 4)


def merit_order(cfg: EconomyConfig, cop_heating: float, cop_dhw: float, dhw_comfort: float,
                dhw_extra: float) -> list[Use]:
    """All uses of a PV kWh, best first. Values assume the stored energy is used before the next PV window."""
    buy, feed = cfg.price_buy, cfg.price_feed
    extra_k = max(0.0, dhw_extra - dhw_comfort)
    cop_extra = cop_dhw * max(0.3, 1 - COP_DROP_PER_K * extra_k)
    uses = [
        Use("house", t("eco.hausverbrauch_direkt"), buy, t("eco.ersetzt_netzstrom_1_1_passiert")),
        Use("heating", t("eco.haus_vorheizen_winter"), buy * (1 + cfg.day_cop_gain) * (1 - cfg.heat_loss_share),
            t("eco.waermepumpe_laeuft_am_tag_statt_nachts", cop_heating=fnum(cop_heating, 1))),
        # hot water: its COP depends mostly on the tank temperature, so only half the outside-air advantage
        Use("dhw_daily", t("eco.warmwasser_tagesbedarf_mit_pv"), buy * (1 + cfg.day_cop_gain / 2) * (1 - cfg.tank_loss_share / 2),
            t("eco.nachheizen_auf_c_bei_sonne_statt", dhw_comfort=fnum(dhw_comfort, 0), cop_dhw=fnum(cop_dhw, 1))),
        Use("battery", t("eco.batterie_laden"), buy * cfg.battery_efficiency,
            t("eco.nachts_fuer_haushaltsgeraete", battery_efficiency=f"{cfg.battery_efficiency:.0%}")),
        Use("dhw_extra", t("eco.warmwasser_ueber_bedarf_c", dhw_extra=fnum(dhw_extra, 0)), buy * cop_extra / cop_dhw * (1 - cfg.tank_loss_share),
            t("eco.nur_vor_einem_trueben_tag_cop", cop_extra=fnum(cop_extra, 1))),
        Use("export", t("eco.einspeisen"), feed, t("eco.einspeiseverguetung")),
    ]
    for u in uses:
        u.gain_vs_feed = round(u.value - feed, 4)
    # direct use always comes first (it happens by itself); the rest by value
    return uses[:1] + sorted(uses[1:], key=lambda u: -u.value)


def money_today(import_kwh: float | None, export_kwh: float | None, cfg: EconomyConfig) -> dict:
    cost = None if import_kwh is None else import_kwh * cfg.price_buy
    earn = None if export_kwh is None else export_kwh * cfg.price_feed
    return {"import_kwh": import_kwh, "export_kwh": export_kwh, "cost_eur": cost, "earn_eur": earn,
            "net_eur": None if cost is None or earn is None else earn - cost}
