"""Device register maps. Add a module per device and register its SPEC here."""
from . import kostal, stiebel_isg

SPECS = {s.key: s for s in (stiebel_isg.SPEC, kostal.SPEC)}
