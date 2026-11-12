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
from .settings import hidden, num

COP_DROP_PER_K = 0.03          # heat pump COP falls ~3 % per K of higher sink temperature


@dataclass
class EconomyConfig:
    # €/kWh bought from the grid
    price_buy: float = num(0.35, 'Strompreis Bezug', '€/kWh', 0.05, 1, 0.001)
    # €/kWh feed-in tariff
    price_feed: float = num(0.092, 'Einspeisevergütung', '€/kWh', 0, 0.5, 0.001)
    # round trip
    battery_efficiency: float = num(0.90, 'Batterie-Wirkungsgrad', '', 0.5, 1, 0.01)
    # fallback; live value from the heat pump counters is preferred
    cop_heating: float = num(4.6, 'COP Heizen (Ersatzwert)', '', 1.5, 7, 0.1,
                             'Wird sonst aus den Zählern der Wärmepumpe berechnet.')
    cop_dhw: float = num(2.6, 'COP Warmwasser (Ersatzwert)', '', 1.2, 5, 0.1,
                         'Wird sonst aus den Zählern der Wärmepumpe berechnet.')
    # COP is higher at midday (warmer outside air) than at night
    day_cop_gain: float = num(0.08, 'COP-Vorteil am Tag', '', 0, 0.5, 0.01, 'Wärmere Außenluft mittags.')
    # extra building losses while the house is 1 K warmer
    heat_loss_share: float = hidden(0.08, why='model constant')
    # extra standing losses of a hotter tank
    tank_loss_share: float = hidden(0.10, why='model constant')

    def validate(self) -> None:
        if not 0 < self.price_feed < self.price_buy:
            raise ValueError("Einspeisevergütung muss zwischen 0 und dem Strompreis liegen")
        if not 0.5 <= self.battery_efficiency <= 1:
            raise ValueError("Batteriewirkungsgrad: 0,5 bis 1")


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
        Use("house", "Hausverbrauch direkt", buy, "ersetzt Netzstrom 1:1 – passiert automatisch"),
        Use("heating", "Haus vorheizen (Winter)", buy * (1 + cfg.day_cop_gain) * (1 - cfg.heat_loss_share),
            f"Wärmepumpe läuft am Tag statt nachts (COP {cop_heating:.1f}); Fußboden/Estrich speichern"),
        # hot water: its COP depends mostly on the tank temperature, so only half the outside-air advantage
        Use("dhw_daily", "Warmwasser-Tagesbedarf mit PV", buy * (1 + cfg.day_cop_gain / 2) * (1 - cfg.tank_loss_share / 2),
            f"Nachheizen auf {dhw_comfort:.0f} °C bei Sonne statt abends (COP {cop_dhw:.1f})"),
        Use("battery", "Batterie laden", buy * cfg.battery_efficiency,
            f"nachts für Haushaltsgeräte, {cfg.battery_efficiency:.0%} Wirkungsgrad"),
        Use("dhw_extra", f"Warmwasser über Bedarf ({dhw_extra:.0f} °C)", buy * cop_extra / cop_dhw * (1 - cfg.tank_loss_share),
            f"nur vor einem trüben Tag – COP sinkt auf {cop_extra:.1f}, Speicher verliert mehr"),
        Use("export", "Einspeisen", feed, "Einspeisevergütung"),
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
