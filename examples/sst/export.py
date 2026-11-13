"""Export SST figures and definition details, optionally run one DAB cell."""
from argparse import ArgumentParser
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from circuit_drawer.compile import build_sheet
from circuit_drawer.layout import Layout
from circuit_drawer.pipeline import render
from circuit_drawer.spice import export_spice

HERE = Path(__file__).resolve().parent
TABLES = ("two_stage_sst", "single_stage_sst")


def module_detail(source, name):
    """An independent view of the exact definition used in the main drawing."""
    definition = deepcopy(source.extra["modules"]["definitions"][name])
    return Layout.from_dict({
        "name": name + "_detail",
        "modules": {
            "version": 1,
            "definitions": {name: definition},
            "instances": [{"id": "M", "definition": name, "display": "expanded",
                           "unconnected": list(definition["ports"])}],
            "links": [],
        },
    })


def dab_bench(source):
    """48 V / 14.4 ohm bench using the two-stage drawing's saved DAB cell."""
    return Layout.from_dict({
        "name": "sst_dab_bench",
        "modules": {
            "version": 1,
            "definitions": {"dab": deepcopy(source.extra["modules"]["definitions"]["dab"])},
            "instances": [{"id": "D", "definition": "dab", "display": "expanded"}],
            "parts": [{"id": "Vin", "kind": "V", "label": "$V_\\mathrm{in}$"},
                      {"id": "RL", "kind": "R", "label": "$R_L$"}],
            "links": [["Vin.b", "D.P"], ["Vin.a", "D.N"],
                      ["D.OP", "RL.a"], ["D.ON", "RL.b"]],
        },
        "simulation": {
            "version": 1,
            "ground": "Vin.a",
            "components": {
                "Vin": {"dc": "48", "wave": {"kind": "step", "initial": "0",
                        "final": "48", "delay": "0", "rise": "2u"}},
                "RL": {"value": "14.4"},
            },
            "transient": {"step": "100n", "max_step": "100n",
                          "node_capacitance": "1n", "stop": "1m"},
            "probes": [{"name": "v_out", "kind": "voltage", "positive": "RL.a", "negative": "RL.b"},
                       {"name": "i_L", "kind": "current", "element": "D/L"},
                       {"name": "i_in", "kind": "current", "element": "Vin"}],
        },
    })


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--out", type=Path, default=HERE / "figures")
    parser.add_argument("--simulate", action="store_true", help="also run the 1 ms DAB bench in ngspice")
    args = parser.parse_args()
    sources = {name: Layout.load(HERE / (name + ".json")) for name in TABLES}
    for name, source in sources.items():
        render(source, "double", args.out, stem=name, formats=("pdf", "png"))
        expanded = Layout.from_dict(source.to_dict())
        for instance in expanded.extra["modules"]["instances"]:
            instance["display"] = "expanded"
        render(expanded, "double", args.out, stem=name + "_expanded", formats=("pdf", "png"))
    details = []
    for name in ("sm", "idc"):
        result = render(module_detail(sources["single_stage_sst"], name), "double",
                        args.out, stem=name + "_detail", formats=("pdf", "png"))
        details.append(result.files["pdf"])
    with TemporaryDirectory() as temporary:
        row = build_sheet(details, [r"$\mathrm{SM}_i$", "IDC"], Path(temporary),
                          "details", formats=("pdf",))
        build_sheet([args.out / "single_stage_sst.pdf", row["pdf"]],
                    ["Single-stage SST", "Shared module definitions"], args.out,
                    "single_stage_sst_sheet", cols=1, formats=("pdf", "png"))
    if args.simulate:
        bench = dab_bench(sources["two_stage_sst"])
        bench.save(args.out / "sst_dab_bench.json")
        export_spice(bench, args.out, "sst_dab_bench", profile="double", run=True)
    print(args.out.resolve())


if __name__ == "__main__":
    main()
