"""Research-area taxonomy for cross-user reuse buckets (task.md §13.4, ADR-023).

The Owner chose the OECD Fields of Science and Technology (FORD) second-level
list, 42 fields, on 2026-11-24. It is coarse on purpose: a bucket built from it
cannot single out a student, and it needs no network or external data.

A keyword maps to a field when one of the field's keyword phrases appears as a
whole phrase in it, after the same normalisation the matcher uses (case
folding, punctuation split, plural folding). A keyword that maps to no field is
``other``. ``other`` never forms a reuse bucket: those students are scored
normally. The keyword map is plain data and can be extended in review.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .matching import _phrases

OTHER = "other"

#: OECD FORD second-level fields: code -> label.
FIELDS: Mapping[str, str] = {
    "1.1": "Mathematics",
    "1.2": "Computer and information sciences",
    "1.3": "Physical sciences",
    "1.4": "Chemical sciences",
    "1.5": "Earth and related environmental sciences",
    "1.6": "Biological sciences",
    "1.7": "Other natural sciences",
    "2.1": "Civil engineering",
    "2.2": "Electrical engineering, electronic engineering, information engineering",
    "2.3": "Mechanical engineering",
    "2.4": "Chemical engineering",
    "2.5": "Materials engineering",
    "2.6": "Medical engineering",
    "2.7": "Environmental engineering",
    "2.8": "Environmental biotechnology",
    "2.9": "Industrial biotechnology",
    "2.10": "Nano-technology",
    "2.11": "Other engineering and technologies",
    "3.1": "Basic medicine",
    "3.2": "Clinical medicine",
    "3.3": "Health sciences",
    "3.4": "Medical biotechnology",
    "3.5": "Other medical sciences",
    "4.1": "Agriculture, forestry, and fisheries",
    "4.2": "Animal and dairy science",
    "4.3": "Veterinary science",
    "4.4": "Agricultural biotechnology",
    "4.5": "Other agricultural sciences",
    "5.1": "Psychology",
    "5.2": "Economics and business",
    "5.3": "Educational sciences",
    "5.4": "Sociology",
    "5.5": "Law",
    "5.6": "Political science",
    "5.7": "Social and economic geography",
    "5.8": "Media and communications",
    "5.9": "Other social sciences",
    "6.1": "History and archaeology",
    "6.2": "Languages and literature",
    "6.3": "Philosophy, ethics and religion",
    "6.4": "Arts",
    "6.5": "Other humanities",
}

#: Field code -> keyword phrases. Editable data; every code must be in FIELDS.
KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "1.1": (
        "mathematics",
        "algebra",
        "topology",
        "geometry",
        "number theory",
        "statistics",
        "probability",
        "optimization",
        "combinatorics",
        "numerical analysis",
    ),
    "1.2": (
        "computer science",
        "machine learning",
        "deep learning",
        "artificial intelligence",
        "neural network",
        "computer vision",
        "natural language processing",
        "nlp",
        "data science",
        "data mining",
        "algorithm",
        "software engineering",
        "databases",
        "distributed systems",
        "compilers",
        "programming languages",
        "cryptography",
        "cybersecurity",
        "computer security",
        "human computer interaction",
        "information retrieval",
        "reinforcement learning",
        "graph learning",
        "bioinformatics",
        "computational biology",
        "robotics",
    ),
    "1.3": (
        "physics",
        "quantum",
        "astrophysics",
        "astronomy",
        "cosmology",
        "optics",
        "condensed matter",
        "particle physics",
        "photonics",
        "plasma",
    ),
    "1.4": (
        "chemistry",
        "organic chemistry",
        "inorganic chemistry",
        "physical chemistry",
        "spectroscopy",
        "catalysis",
        "electrochemistry",
    ),
    "1.5": (
        "geology",
        "geophysics",
        "climate",
        "oceanography",
        "meteorology",
        "atmospheric science",
        "hydrology",
        "seismology",
        "earth science",
    ),
    "1.6": (
        "biology",
        "molecular biology",
        "cell biology",
        "genetics",
        "genomics",
        "protein",
        "neuroscience",
        "ecology",
        "evolution",
        "microbiology",
        "biochemistry",
        "bioinformatics",
        "computational biology",
        "structural biology",
        "immunology",
    ),
    "1.7": ("natural science",),
    "2.1": (
        "civil engineering",
        "structural engineering",
        "geotechnical",
        "transportation engineering",
        "construction",
    ),
    "2.2": (
        "electrical engineering",
        "electronic",
        "electronics",
        "signal processing",
        "control systems",
        "power systems",
        "telecommunications",
        "wireless",
        "embedded systems",
        "vlsi",
        "circuit design",
        "robotics",
    ),
    "2.3": (
        "mechanical engineering",
        "fluid mechanics",
        "thermodynamics",
        "aerospace",
        "mechatronics",
        "heat transfer",
        "manufacturing",
    ),
    "2.4": ("chemical engineering", "process engineering", "reaction engineering"),
    "2.5": (
        "materials science",
        "materials engineering",
        "polymers",
        "ceramics",
        "metallurgy",
        "composites",
        "semiconductor materials",
    ),
    "2.6": (
        "biomedical engineering",
        "medical devices",
        "medical imaging",
        "biomechanics",
        "tissue engineering",
    ),
    "2.7": (
        "environmental engineering",
        "water treatment",
        "waste management",
        "air pollution",
        "renewable energy",
    ),
    "2.8": ("environmental biotechnology", "bioremediation"),
    "2.9": ("industrial biotechnology", "bioprocess", "fermentation", "synthetic biology"),
    "2.10": ("nanotechnology", "nanomaterials", "nanoscience"),
    "2.11": ("engineering technology",),
    "3.1": ("anatomy", "physiology", "pharmacology", "pathology", "toxicology"),
    "3.2": (
        "clinical medicine",
        "oncology",
        "cardiology",
        "neurology",
        "surgery",
        "radiology",
        "psychiatry",
        "pediatrics",
    ),
    "3.3": ("public health", "epidemiology", "nursing", "health policy", "nutrition"),
    "3.4": ("medical biotechnology", "drug discovery", "gene therapy"),
    "3.5": ("medicine", "medical science"),
    "4.1": ("agriculture", "forestry", "fisheries", "agronomy", "soil science", "crop science"),
    "4.2": ("animal science", "dairy science"),
    "4.3": ("veterinary",),
    "4.4": ("agricultural biotechnology", "plant biotechnology"),
    "4.5": ("food science",),
    "5.1": ("psychology", "cognitive science", "behavioral science"),
    "5.2": (
        "economics",
        "finance",
        "business",
        "management science",
        "marketing",
        "econometrics",
        "accounting",
    ),
    "5.3": ("education", "pedagogy", "learning sciences"),
    "5.4": ("sociology", "demography", "anthropology"),
    "5.5": ("law", "legal studies"),
    "5.6": ("political science", "public policy", "international relations"),
    "5.7": ("human geography", "urban planning", "urban studies", "economic geography"),
    "5.8": ("media studies", "communication studies", "journalism"),
    "5.9": ("social science",),
    "6.1": ("history", "archaeology"),
    "6.2": ("linguistics", "literature studies", "foreign languages", "language studies"),
    "6.3": ("philosophy", "ethics", "religion", "theology"),
    "6.4": (
        "fine arts",
        "visual arts",
        "art history",
        "music",
        "graphic design",
        "industrial design",
        "film studies",
        "architecture",
    ),
    "6.5": ("humanities",),
}


def _compiled() -> tuple[tuple[tuple[str, ...], str], ...]:
    pairs: list[tuple[tuple[str, ...], str]] = []
    for code, phrases in KEYWORDS.items():
        if code not in FIELDS:
            raise ValueError(f"taxonomy keyword field {code!r} is not an OECD FORD field.")
        for phrase in _phrases(phrases):
            pairs.append((tuple(phrase.split()), code))
    return tuple(pairs)


_PATTERNS = _compiled()


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    size = len(needle)
    return any(haystack[i : i + size] == needle for i in range(len(haystack) - size + 1))


def fields_for(keywords: Iterable[str]) -> frozenset[str]:
    """The OECD fields a set of keywords maps to; ``{"other"}`` when none match."""

    found: set[str] = set()
    for phrase in _phrases(keywords):
        tokens = tuple(phrase.split())
        matched = {code for pattern, code in _PATTERNS if _contains(tokens, pattern)}
        found |= matched or {OTHER}
    return frozenset(found)
