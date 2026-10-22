"""
FIBO + LEI sample: check real GLEIF LEI records against a pinned FIBO slice.

The sample loads a small slice of FIBO (``fibo-slice.ttl``), derives SHACL
shapes from the OWL axioms in that slice, and validates each GLEIF LEI record
in ``records/`` against them. It prints one PASS or FAIL line per record and
exits 0 only when every record conforms. It runs offline: no gateway, no
credentials, no network.

Usage (from the repository root)::

    pip install -e ".[shacl]"
    python examples/fibo-lei/run.py                    # every file in records/
    python examples/fibo-lei/run.py path/to/record.ttl  # specific record files
    python examples/fibo-lei/run.py --show-shapes      # also print the shapes

See README.md in this directory for the pinned FIBO release, the LEI fields
used, and the licenses the slice and records are redistributed under.
"""

import argparse
import sys
from pathlib import Path

import rdflib
from rdflib import OWL, RDF, RDFS, URIRef

HERE = Path(__file__).resolve().parent
SLICE_PATH = HERE / "fibo-slice.ttl"
RECORDS_DIR = HERE / "records"

try:
    from semantica.ingest import OntologyIngestor
    from semantica.ontology import SHACLGenerator, run_shacl_validation
except ImportError:  # running from a source checkout without `pip install -e .`
    sys.path.insert(0, str(HERE.parents[1]))
    from semantica.ingest import OntologyIngestor
    from semantica.ontology import SHACLGenerator, run_shacl_validation

LEI = rdflib.Namespace(
    "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LEIEntities/"
)
REL = rdflib.Namespace(
    "https://spec.edmcouncil.org/fibo/ontology/FND/Relations/Relations/"
)
BREG = rdflib.Namespace(
    "https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/"
    "BusinessRegistries/"
)
CMNS_ID = rdflib.Namespace("https://www.omg.org/spec/Commons/Identifiers/")
CMNS_DSG = rdflib.Namespace("https://www.omg.org/spec/Commons/Designators/")
CMNS_COL = rdflib.Namespace("https://www.omg.org/spec/Commons/Collections/")
CMNS_ORG = rdflib.Namespace("https://www.omg.org/spec/Commons/Organizations/")
CMNS_RGA = rdflib.Namespace("https://www.omg.org/spec/Commons/RegulatoryAgencies/")

# Shapes whose cardinality and sh:class are read from an OWL restriction in the
# slice. The comment names the axiom; derive_shapes() fails if it is missing.
FROM_AXIOMS = [
    # LEIRegisteredEntity == isIdentifiedBy some LegalEntityIdentifier
    (LEI.LEIRegisteredEntity, CMNS_ID.isIdentifiedBy),
    # LegalEntity subClassOf isOrganizedIn exactly 1 Jurisdiction
    (CMNS_ORG.LegalEntity, CMNS_RGA.isOrganizedIn),
    # LegalEntityIdentifier subClassOf identifies exactly 1 LegalPerson
    (LEI.LegalEntityIdentifier, CMNS_ID.identifies),
    # LegalEntityIdentifier subClassOf isDefinedIn some LegalEntityIdentifierScheme
    (LEI.LegalEntityIdentifier, CMNS_DSG.isDefinedIn),
    # LEI registry entry subClassOf hasRegistrationStatus some RegistrationStatus,
    # and (via BusinessRegistryEntry) max 1 RegistrationStatus
    (BREG.LegalEntityIdentifierRegistryEntry, BREG.hasRegistrationStatus),
    # LEI registry entry subClassOf comprises some LegalEntityIdentifier
    (BREG.LegalEntityIdentifierRegistryEntry, CMNS_COL.comprises),
]

# Value constraints FIBO does not state as OWL axioms but takes from the
# standards its terms are adapted from (ISO 17442, the GLEIF LEI-CDF).
LEI_PATTERN = "^[A-Z0-9]{18}[0-9]{2}$"  # ISO 17442: 18 characters + 2 check digits
JURISDICTION_PATTERN = "^[A-Z]{2}(-[A-Z0-9]{1,3})?$"  # ISO 3166-1 / ISO 3166-2
FROM_STANDARDS = [
    # LEI-CDF: LegalName is mandatory and single-valued
    (LEI.LEIRegisteredEntity, REL.hasLegalName, 1, 1, {"range": "string"}),
    (LEI.LegalEntityIdentifier, REL.hasTag, 1, 1, {"pattern": LEI_PATTERN}),
    (CMNS_RGA.Jurisdiction, REL.hasTag, 1, 1, {"pattern": JURISDICTION_PATTERN}),
]


def local_name(iri):
    """Return the last path segment of an IRI (FIBO IRIs end in a term name)."""
    return str(iri).rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def restrictions_on(graph, cls):
    """Yield (property, restriction) for each owl:Restriction on cls or a superclass."""
    seen, todo = set(), [cls]
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        for link in (RDFS.subClassOf, OWL.equivalentClass):
            for target in graph.objects(current, link):
                if isinstance(target, URIRef):
                    todo.append(target)
                elif (target, RDF.type, OWL.Restriction) in graph:
                    yield graph.value(target, OWL.onProperty), target


def constraint_from_axioms(graph, cls, prop):
    """Read (minCount, maxCount, sh:class) from the OWL restrictions on cls for prop."""
    min_count = max_count = value_class = None
    found = False
    for on_property, r in restrictions_on(graph, cls):
        if on_property != prop:
            continue
        found = True
        some = graph.value(r, OWL.someValuesFrom)
        exact = graph.value(r, OWL.qualifiedCardinality)
        at_least = graph.value(r, OWL.minQualifiedCardinality)
        at_most = graph.value(r, OWL.maxQualifiedCardinality)
        lower = 1 if some is not None else None
        for value in (exact, at_least):
            if value is not None:
                lower = max(lower or 0, int(value))
        upper = None
        for value in (exact, at_most):
            if value is not None:
                upper = int(value) if upper is None else min(upper, int(value))
        if lower:
            min_count = lower if min_count is None else max(min_count, lower)
        if upper is not None:
            max_count = upper if max_count is None else min(max_count, upper)
        value_class = value_class or some or graph.value(r, OWL.onClass)
    if not found:
        raise ValueError(f"{SLICE_PATH.name} has no axiom on {cls} for {prop}")
    return min_count, max_count, value_class


def derive_shapes(slice_graph, ontology):
    """Build a Semantica ontology dict from the slice and turn it into SHACL."""
    classes = {c["uri"]: c for c in ontology["classes"]}
    properties = {p["uri"]: p for p in ontology["properties"]}

    def declared(term, kind, index):
        if str(term) not in index:
            raise ValueError(f"{SLICE_PATH.name} does not declare the {kind} {term}")
        return index[str(term)]

    rows = []
    for cls, prop in FROM_AXIOMS:
        min_count, max_count, value_class = constraint_from_axioms(
            slice_graph, cls, prop
        )
        extra = {"range": str(value_class)} if value_class is not None else {}
        rows.append((cls, prop, min_count, max_count, extra))
    rows.extend(FROM_STANDARDS)

    shape_classes, shape_properties = {}, []
    for cls, prop, min_count, max_count, extra in rows:
        cls_def = declared(cls, "class", classes)
        prop_def = declared(prop, "property", properties)
        shape_classes[str(cls)] = {
            "name": local_name(cls),
            "uri": str(cls),
            "label": cls_def.get("label"),
        }
        cardinality = {}
        if min_count is not None:
            cardinality["min"] = min_count
        if max_count is not None:
            cardinality["max"] = max_count
        shape_properties.append(
            {
                "name": local_name(prop),
                "uri": str(prop),
                "label": prop_def.get("label"),
                "type": prop_def["type"],
                "domain": local_name(cls),
                "range": prop_def.get("range", ""),
                "cardinality": cardinality,
                **extra,
            }
        )

    generator = SHACLGenerator(base_uri="https://example.org/fibo-lei/shapes/")
    shapes = generator.generate(
        {"classes": list(shape_classes.values()), "properties": shape_properties}
    )
    return generator.serialize(shapes, format="turtle")


def check_record(slice_graph, shapes, path):
    """Validate one record file (merged with the slice) and return the report."""
    record = rdflib.Graph().parse(path, format="turtle")
    name = next(record.objects(None, REL.hasLegalName), "(no legal name)")
    data = record + slice_graph  # class hierarchy and status individuals for sh:class
    report = run_shacl_validation(data.serialize(format="turtle"), shapes)
    report.explain_violations()
    return name, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("records", nargs="*", type=Path, help="record files (.ttl)")
    parser.add_argument("--show-shapes", action="store_true", help="print the shapes")
    args = parser.parse_args(argv)

    record_paths = args.records or sorted(RECORDS_DIR.glob("*.ttl"))
    if not record_paths:
        print(f"no records found in {RECORDS_DIR}", file=sys.stderr)
        return 2

    ontology = OntologyIngestor().ingest_ontology(SLICE_PATH).data
    slice_graph = rdflib.Graph().parse(SLICE_PATH, format="turtle")
    shapes = derive_shapes(slice_graph, ontology)

    classes, properties = len(ontology["classes"]), len(ontology["properties"])
    print(
        f"FIBO slice: {ontology['version']} "
        f"({classes} classes, {properties} properties)"
    )
    print(f"Shapes derived: {shapes.count('sh:property [')} property shapes")
    if args.show_shapes:
        print(shapes)

    failed = 0
    for path in record_paths:
        name, report = check_record(slice_graph, shapes, path)
        if report.conforms:
            print(f"PASS  {path.stem}  {name}")
            continue
        failed += 1
        print(f"FAIL  {path.stem}  {name}")
        for violation in report.violations:
            print(f"      - {violation.explanation}")

    print(f"{len(record_paths) - failed}/{len(record_paths)} records conform")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
