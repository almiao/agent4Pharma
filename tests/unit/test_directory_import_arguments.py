from pathlib import Path

import pytest

from agent4pharma.entrypoints.cli.argument_parser import build_parser


@pytest.mark.parametrize(
    "command,arguments",
    [
        (["catalog", "import"], ["--seed", "external.json", "--inventory", "inventory.json"]),
        (["registry", "import-demo"], ["--seed", "external.json"]),
    ],
)
def test_directory_import_requires_explicit_external_data(command, arguments):
    parser = build_parser()
    with pytest.raises(SystemExit) as failure:
        parser.parse_args(command)
    assert failure.value.code == 2
    parsed = parser.parse_args(command + arguments)
    assert parsed.seed == Path("external.json")
