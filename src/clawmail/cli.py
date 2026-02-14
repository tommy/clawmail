"""Click CLI commands with Rich output."""

from __future__ import annotations

import os
import subprocess
import sys

import click
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from clawmail import __version__
from clawmail.config import (
    CONFIG_FILE,
    PROCESSED_FILE,
    add_processed_uids,
    get_anthropic_api_key,
    get_category_rules,
    get_imap_password,
    get_suggestions_prompt,
    get_system_prompt,
    load_config,
    load_processed_uids,
    save_config,
    set_anthropic_api_key,
    set_imap_password,
)

console = Console(width=None if sys.stdout.isatty() else 160)
err_console = Console(stderr=True)

MODEL_ALIASES = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-5-20250929",
    "opus": "claude-opus-4-6",
}


def resolve_model(name: str) -> str:
    """Resolve a short model alias to a full model ID."""
    return MODEL_ALIASES.get(name, name)


@click.group()
@click.version_option(version=__version__)
def cli():
    """Clawmail - AI-powered email triage using Claude."""


@cli.command()
def configure():
    """Interactive setup: email, App Password, API key. Tests both connections."""
    config = load_config()

    console.print("\n[bold]Clawmail Configuration[/bold]\n")

    # IMAP setup
    current_email = config.get("imap", {}).get("email", "")
    email_addr = click.prompt("Gmail address", default=current_email or None)
    config.setdefault("imap", {})["email"] = email_addr

    console.print(
        "\n[dim]Gmail requires an App Password (not your regular password).[/dim]"
    )
    console.print(
        "[dim]Generate one at: https://myaccount.google.com/apppasswords[/dim]\n"
    )
    app_password = click.prompt("Gmail App Password", hide_input=True)
    set_imap_password(app_password)

    # Anthropic API key
    console.print()
    api_key = click.prompt("Anthropic API key", hide_input=True)
    set_anthropic_api_key(api_key)

    # Save config
    save_config(config)
    console.print(f"\n[green]Config saved to {CONFIG_FILE}[/green]")

    # Test IMAP connection
    console.print("\nTesting IMAP connection...", end=" ")
    try:
        from clawmail.imap import IMAPClient

        imap_cfg = config["imap"]
        with IMAPClient(
            imap_cfg["host"], imap_cfg["port"], imap_cfg["email"], app_password
        ) as client:
            if client.test_connection():
                console.print("[green]OK[/green]")
            else:
                console.print("[red]FAILED[/red]")
    except Exception as e:
        console.print(f"[red]FAILED: {e}[/red]")

    # Test Anthropic connection
    console.print("Testing Anthropic API...", end=" ")
    try:
        from clawmail.classifier import EmailClassifier

        anthropic_cfg = config.get("anthropic", {})
        classifier = EmailClassifier(
            api_key=api_key,
            model=anthropic_cfg.get("model", "claude-sonnet-4-5"),
        )
        if classifier.test_connection():
            console.print("[green]OK[/green]")
        else:
            console.print("[red]FAILED[/red]")
    except Exception as e:
        console.print(f"[red]FAILED: {e}[/red]")

    console.print("\n[bold green]Setup complete![/bold green]")


@cli.command()
@click.option("--days", default=None, type=int, help="Days back to fetch")
@click.option("--limit", default=None, type=int, help="Max emails to fetch")
@click.option("--all", "fetch_all", is_flag=True, help="Include read emails")
def fetch(days, limit, fetch_all):
    """Fetch and display recent emails (read-only)."""
    config = load_config()
    imap_cfg = config["imap"]
    fetch_cfg = config.get("fetch", {})

    password = get_imap_password()
    if not password:
        err_console.print("[red]No IMAP password found. Run: clawmail configure[/red]")
        sys.exit(1)

    days_back = days or fetch_cfg.get("days_back", 1)
    max_emails = limit or fetch_cfg.get("max_emails", 50)
    unread_only = not fetch_all and fetch_cfg.get("unread_only", True)
    mailbox = fetch_cfg.get("mailbox", "INBOX")
    excluded_uids = load_processed_uids()

    from clawmail.imap import IMAPClient

    try:
        with IMAPClient(
            imap_cfg["host"], imap_cfg["port"], imap_cfg["email"], password
        ) as client:
            emails = client.fetch_recent(
                mailbox,
                days_back,
                max_emails,
                unread_only,
                excluded_uids,
            )
    except Exception as e:
        err_console.print(f"[red]IMAP error: {e}[/red]")
        sys.exit(1)

    if not emails:
        console.print("[dim]No emails found.[/dim]")
        return

    table = Table(title=f"Recent Emails ({len(emails)})")
    table.add_column("UID", style="dim", width=8)
    table.add_column("Date", width=12)
    table.add_column("From", width=25, no_wrap=True)
    table.add_column("Subject", min_width=30)
    table.add_column("Flags", style="dim", width=10)

    for e in emails:
        date_str = e.date.strftime("%m/%d %H:%M") if e.date else ""
        sender = e.sender[:25] if e.sender else ""
        flags = " ".join(f.strip("\\") for f in e.flags) if e.flags else ""
        table.add_row(str(e.uid), date_str, sender, e.subject, flags)

    console.print(table)


@cli.command()
@click.option("--dry-run", is_flag=True, help="Show proposals without executing")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.option("--days", default=None, type=int, help="Days back to fetch")
@click.option("--limit", default=None, type=int, help="Max emails to process")
@click.option("--all", "fetch_all", is_flag=True, help="Include read emails")
@click.option("--quiet", "-q", is_flag=True, help="Suppress non-error output")
@click.option(
    "--label",
    default=None,
    type=str,
    help="Process emails in this Gmail label instead of INBOX",
)
@click.option(
    "--compare",
    default=None,
    type=str,
    help="Compare with alternate model (e.g. haiku, opus)",
)
def process(dry_run, yes, days, limit, fetch_all, quiet, label, compare):
    """Fetch, classify with Claude, confirm, and execute actions."""

    def out(*args, **kwargs):
        if not quiet:
            console.print(*args, **kwargs)

    config = load_config()
    imap_cfg = config["imap"]
    fetch_cfg = config.get("fetch", {})
    anthropic_cfg = config.get("anthropic", {})

    password = get_imap_password()
    api_key = get_anthropic_api_key()

    if not password:
        err_console.print("[red]No IMAP password found. Run: clawmail configure[/red]")
        sys.exit(1)
    if not api_key:
        err_console.print(
            "[red]No Anthropic API key found. Run: clawmail configure[/red]"
        )
        sys.exit(1)

    days_back = days or fetch_cfg.get("days_back", 1)
    max_emails = limit or fetch_cfg.get("max_emails", 50)
    unread_only = not fetch_all and fetch_cfg.get("unread_only", True)
    mailbox = label or fetch_cfg.get("mailbox", "INBOX")
    excluded_uids = load_processed_uids()

    # Fetch emails
    from clawmail.imap import IMAPClient

    out("[bold]Fetching emails...[/bold]")
    try:
        with IMAPClient(
            imap_cfg["host"], imap_cfg["port"], imap_cfg["email"], password
        ) as client:
            emails = client.fetch_recent(
                mailbox,
                days_back,
                max_emails,
                unread_only,
                excluded_uids,
            )
    except Exception as e:
        err_console.print(f"[red]IMAP error: {e}[/red]")
        sys.exit(1)

    if not emails:
        out("[dim]No emails to process.[/dim]")
        return

    # Separate already-flagged emails — no need to re-classify them
    flagged = [e for e in emails if "\\Flagged" in e.flags]
    to_classify = [e for e in emails if "\\Flagged" not in e.flags]

    if flagged:
        out(f"Found {len(emails)} email(s), {len(flagged)} already flagged (skipping).")
    else:
        out(f"Found {len(emails)} email(s).")

    if not to_classify:
        out("[dim]All emails already flagged — nothing to classify.[/dim]")
        return

    out(f"Classifying {len(to_classify)} email(s) with Claude...")

    # Classify
    from clawmail.classifier import EmailClassifier

    categories = get_category_rules(config)
    system_prompt = get_system_prompt(config)

    try:
        classifier = EmailClassifier(
            api_key=api_key,
            model=anthropic_cfg.get("model", "claude-sonnet-4-5"),
            max_tokens=anthropic_cfg.get("max_tokens", 1024),
        )
        actions, usage = classifier.classify(to_classify, categories, system_prompt)
    except Exception as e:
        err_console.print(f"[red]Classification error: {e}[/red]")
        sys.exit(1)

    primary_model = anthropic_cfg.get("model", "claude-sonnet-4-5")
    out(
        f"[dim]{primary_model} tokens: {usage['input_tokens']} in / {usage['output_tokens']} out"
        f" ({usage['input_tokens'] + usage['output_tokens']} total)[/dim]"
    )

    # Build lookup for display
    email_map = {e.uid: e for e in emails}

    # --compare mode: classify with alt model and show side-by-side
    if compare:
        alt_model = resolve_model(compare)
        out(f"\nClassifying with alternate model [bold]{alt_model}[/bold]...")
        try:
            alt_classifier = EmailClassifier(
                api_key=api_key,
                model=alt_model,
                max_tokens=anthropic_cfg.get("max_tokens", 1024),
            )
            alt_actions, alt_usage = alt_classifier.classify(
                to_classify, categories, system_prompt
            )
        except Exception as e:
            err_console.print(f"[red]Alt model classification error: {e}[/red]")
            sys.exit(1)

        out(
            f"[dim]{alt_model} tokens: {alt_usage['input_tokens']} in / {alt_usage['output_tokens']} out"
            f" ({alt_usage['input_tokens'] + alt_usage['output_tokens']} total)[/dim]"
        )

        # Build comparison table
        alt_map = {a.email_uid: a for a in alt_actions}
        primary_map = {a.email_uid: a for a in actions}

        comp_table = Table(title=f"Comparison: {primary_model} vs {alt_model}")
        comp_table.add_column("UID", style="dim", width=8)
        comp_table.add_column("Subject", min_width=20)
        comp_table.add_column("Primary Category", width=14)
        comp_table.add_column("Primary Conf", width=6)
        comp_table.add_column("Alt Category", width=14)
        comp_table.add_column("Alt Conf", width=6)
        comp_table.add_column("Match", width=5, justify="center")

        match_count = 0
        total_count = 0
        for e in to_classify:
            pa = primary_map.get(e.uid)
            aa = alt_map.get(e.uid)
            p_cat = pa.category if pa else "—"
            p_conf = f"{pa.confidence:.0%}" if pa else "—"
            a_cat = aa.category if aa else "—"
            a_conf = f"{aa.confidence:.0%}" if aa else "—"
            matched = p_cat == a_cat
            if matched:
                match_count += 1
            total_count += 1
            match_icon = "[green]✓[/green]" if matched else "[red]✗[/red]"
            comp_table.add_row(
                str(e.uid),
                e.subject[:40],
                p_cat,
                p_conf,
                a_cat,
                a_conf,
                match_icon,
            )

        out(comp_table)
        out(
            f"\n[bold]{match_count}/{total_count}[/bold] emails matched between"
            f" {primary_model} and {alt_model}."
        )
        out("\n[dim]Compare mode — no actions executed.[/dim]")
        return

    # Display proposed actions
    if not quiet:
        action_table = Table(title="Proposed Actions", expand=True)
        action_table.add_column("UID", style="dim", width=8)
        action_table.add_column("Subject", min_width=25)
        action_table.add_column("Flags", style="dim", width=10)
        action_table.add_column("Category", width=12)
        action_table.add_column("Conf", width=5)
        action_table.add_column("Action", width=8)
        action_table.add_column("Target", width=15)
        action_table.add_column("Reasoning", min_width=20)

        action_styles = {
            "flag": "yellow",
            "move": "blue",
            "trash": "red",
            "archive": "cyan",
            "none": "dim",
        }

        for a in actions:
            email_info = email_map.get(a.email_uid)
            subject = email_info.subject[:40] if email_info else f"UID {a.email_uid}"
            flags = (
                " ".join(f.strip("\\") for f in email_info.flags) if email_info else ""
            )
            style = action_styles.get(a.action.value, "")
            action_table.add_row(
                str(a.email_uid),
                subject,
                flags,
                a.category,
                f"{a.confidence:.0%}",
                f"[{style}]{a.action.value}[/{style}]",
                a.target_folder or "",
                a.reasoning,
            )

        out(action_table)

    if dry_run:
        out("\n[dim]Dry run — no actions executed.[/dim]")

        # Suggest new categories
        suggestions_prompt = get_suggestions_prompt(config)
        out("\n[bold]Suggesting new categories...[/bold]")
        try:
            suggestions, suggestions_usage = classifier.suggest_categories(
                emails,
                categories,
                actions,
                suggestions_prompt,
            )
            out(
                f"[dim]Suggestions tokens: {suggestions_usage['input_tokens']} in"
                f" / {suggestions_usage['output_tokens']} out[/dim]"
            )
            if suggestions.suggestions:
                if not quiet:
                    stable = Table(title="Suggested New Categories")
                    stable.add_column("Name", width=15)
                    stable.add_column("Description", min_width=25)
                    stable.add_column("Action", width=8)
                    stable.add_column("Reasoning", min_width=25)
                    stable.add_column("Example UIDs", width=12)

                    for s in suggestions.suggestions:
                        uids = ", ".join(str(u) for u in s.example_uids[:3])
                        stable.add_row(
                            s.name,
                            s.description,
                            s.suggested_action,
                            s.reasoning,
                            uids,
                        )
                    out(stable)
            else:
                out("[dim]No new categories suggested — current rules look good.[/dim]")
        except Exception as e:
            err_console.print(f"[yellow]Could not generate suggestions: {e}[/yellow]")

        return

    # Confirm
    actionable = [a for a in actions if a.action.value != "none"]
    if not actionable:
        added = add_processed_uids(
            {a.email_uid for a in actions if a.action.value == "none"},
        )
        if added:
            out(f"[dim]Recorded {added} UID(s) in {PROCESSED_FILE}[/dim]")
        out("\n[dim]No actions to execute (all classified as 'none').[/dim]")
        return

    # Check that target folders exist before executing
    needed_folders = {a.target_folder for a in actionable if a.target_folder}
    if needed_folders:
        try:
            with IMAPClient(
                imap_cfg["host"], imap_cfg["port"], imap_cfg["email"], password
            ) as client:
                existing_folders = set(client.list_folders())
        except Exception as e:
            err_console.print(f"[red]IMAP error checking folders: {e}[/red]")
            sys.exit(1)

        missing = needed_folders - existing_folders
        if missing:
            err_console.print(
                f"[red]Missing Gmail labels: {', '.join(sorted(missing))}[/red]"
            )
            err_console.print("[red]Create them in Gmail before running again.[/red]")
            sys.exit(1)

    if not yes:
        if not Confirm.ask(
            f"\nExecute {len(actionable)} action(s)?",
            default=False,
            console=console,
        ):
            out("[dim]Aborted.[/dim]")
            return

    # Execute actions — flags first (no expunge), then moves/trash/archive
    actionable.sort(key=lambda a: 0 if a.action.value == "flag" else 1)

    out("\n[bold]Executing actions...[/bold]")
    success_count = 0
    error_count = 0
    successful_action_uids: set[int] = set()

    try:
        with IMAPClient(
            imap_cfg["host"], imap_cfg["port"], imap_cfg["email"], password
        ) as client:
            client.select_mailbox(mailbox)
            for a in actionable:
                try:
                    client.execute_action(
                        a.email_uid,
                        a.action.value,
                        a.target_folder,
                    )
                    email_info = email_map.get(a.email_uid)
                    label = (
                        email_info.subject[:30] if email_info else f"UID {a.email_uid}"
                    )
                    out(f"  [green]✓[/green] {a.action.value}: {label}")
                    success_count += 1
                    successful_action_uids.add(a.email_uid)
                except Exception as e:
                    err_console.print(f"  [red]✗[/red] UID {a.email_uid}: {e}")
                    error_count += 1
    except Exception as e:
        err_console.print(f"[red]IMAP error: {e}[/red]")
        sys.exit(1)

    out(f"\n[bold]Done:[/bold] {success_count} succeeded, {error_count} failed.")

    processed_now = {a.email_uid for a in actions if a.action.value == "none"}
    processed_now.update(successful_action_uids)
    added = add_processed_uids(processed_now)
    if added:
        out(f"[dim]Recorded {added} UID(s) in {PROCESSED_FILE}[/dim]")


@cli.command()
@click.option("--edit", is_flag=True, help="Open config in $EDITOR")
def rules(edit):
    """View current rules or edit config."""
    if edit:
        editor = os.environ.get("EDITOR", "vi")
        if not CONFIG_FILE.exists():
            save_config(load_config())
            console.print(f"[dim]Created default config at {CONFIG_FILE}[/dim]")
        subprocess.run([editor, str(CONFIG_FILE)])
        return

    config = load_config()
    categories = get_category_rules(config)

    if not categories:
        console.print("[dim]No rules configured.[/dim]")
        return

    table = Table(title="Triage Rules")
    table.add_column("Category", width=12)
    table.add_column("Description", min_width=30)
    table.add_column("Action", width=8)
    table.add_column("Target Folder", width=15)
    table.add_column("Age Gate", style="dim", width=10)

    for c in categories:
        age = f"{c.older_than_minutes}m" if c.older_than_minutes is not None else ""
        table.add_row(c.name, c.description, c.action.value, c.target_folder or "", age)

    console.print(table)

    console.print(f"\n[dim]System prompt:[/dim] {get_system_prompt(config)[:100]}...")
    console.print(f"[dim]Config file:[/dim] {CONFIG_FILE}")


@cli.command()
def folders():
    """List all IMAP folders."""
    config = load_config()
    imap_cfg = config["imap"]

    password = get_imap_password()
    if not password:
        err_console.print("[red]No IMAP password found. Run: clawmail configure[/red]")
        sys.exit(1)

    from clawmail.imap import IMAPClient

    try:
        with IMAPClient(
            imap_cfg["host"], imap_cfg["port"], imap_cfg["email"], password
        ) as client:
            folder_list = client.list_folders()
    except Exception as e:
        err_console.print(f"[red]IMAP error: {e}[/red]")
        sys.exit(1)

    if not folder_list:
        console.print("[dim]No folders found.[/dim]")
        return

    console.print(f"[bold]IMAP Folders ({len(folder_list)}):[/bold]\n")
    for folder in folder_list:
        console.print(f"  {folder}")
