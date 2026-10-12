"""Read-only OneNote source CLI; imports collection code only when invoked."""

import json

import typer


def _source():
    from ...useful_tools.onenote_source import OneNoteSource
    return OneNoteSource()


def _run(operation, as_json: bool, family: str | None = None) -> None:
    from ...provider_credentials import ProviderCredentialError
    import yaml
    try:
        result = operation(_source())
    except (OSError, yaml.YAMLError):
        result = {"error": "Could not read or write the local source files. Check --output and directory permissions.", "exit_code": 1}
    except (ValueError, ProviderCredentialError) as error:
        result = {"error": str(error), "exit_code": 1}
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    elif "error" in result:
        typer.echo(result["error"], err=True)
    elif family:
        for row in result[family]:
            print(f"{row['id']}  {' '.join(str(row.get('displayName') or row.get('title') or '(untitled)').split())}")
        if not result[family]:
            print(f"No {family} in this scope.")
        if result.get("truncated"):
            print(f"Listing limited to {result['limit']} pages; more pages remain. Increase --limit (maximum 1000).")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if "error" in result:
        raise typer.Exit(1)
    if result.get("errors"):
        # Partial results have already been saved; scripts can distinguish this
        # from success and from a fatal failure without parsing prose.
        from .command_tips import mark_next_step_named
        # Exit 2 is an intentional partial result, not a parser usage error.
        # Recovery is already named in each page diagnostic.
        mark_next_step_named()
        raise typer.Exit(2)


def register_onenote_source(parent, factory) -> None:
    source_app = factory(help="Verify the existing Microsoft connection for read-only OneNote access.",
                         no_args_is_help=True)
    parent.add_typer(source_app, name="source")

    @source_app.command("verify")
    def verify(as_json: bool = typer.Option(False, "--json")):
        """Verify existing co auth microsoft credentials and OneNote read access."""
        _run(lambda source: source.verify_connection(), as_json)
