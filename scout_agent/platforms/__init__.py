from .doda import DodaAdapter
from .forkwell import ForkwellAdapter
from .generic import GenericAdapter
from .green import GreenAdapter
from .lapras import LaprasAdapter
from .mynavi import MynaviAdapter
from .type_jp import TypeJpAdapter

ADAPTERS = {
    "generic": GenericAdapter,
    "green": GreenAdapter,
    "forkwell": ForkwellAdapter,
    "lapras": LaprasAdapter,
    "doda": DodaAdapter,
    "mynavi": MynaviAdapter,
    "type": TypeJpAdapter,
}
