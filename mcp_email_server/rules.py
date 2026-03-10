from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import tomli_w
from pydantic import BaseModel, field_validator

from mcp_email_server.config import CONFIG_PATH
from mcp_email_server.log import logger

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

RULES_DIR = CONFIG_PATH.parent / "rules"


def _validate_rule_path(file_name: str, rules_dir: Path) -> Path:
    """Resolve rule file path and validate it's within the rules directory."""
    if not file_name.endswith(".toml"):
        file_name = file_name + ".toml"
    path = (rules_dir / file_name).resolve()
    if not path.is_relative_to(rules_dir.resolve()):
        raise ValueError(f"Invalid file name: {file_name}")
    return path


class Rule(BaseModel):
    name: str
    account: str
    target_folder: str
    senders: list[str]
    source_mailbox: str = "INBOX"
    mark_read: bool = False

    @field_validator("senders")
    @classmethod
    def senders_must_not_be_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("senders must not be empty")
        return v


class RuleFile(BaseModel):
    rules: list[Rule]


class RuleApplyResult(BaseModel):
    rule_name: str
    account: str
    source_mailbox: str
    target_folder: str
    matched: int
    moved: int
    failed: int
    dry_run: bool = False


def load_rules_from_file(path: Path) -> list[Rule]:
    try:
        data = tomllib.loads(path.read_text())
        rule_file = RuleFile.model_validate(data)
        return rule_file.rules
    except Exception as e:
        logger.warning(f"Failed to load rules from {path}: {e}")
        return []


def load_all_rules(
    rules_dir: Path | None = None,
    account: str | None = None,
    file_name: str | None = None,
) -> dict[str, list[Rule]]:
    if rules_dir is None:
        rules_dir = RULES_DIR

    if not rules_dir.is_dir():
        return {}

    toml_files = sorted(rules_dir.glob("*.toml"))

    if file_name is not None:
        if not file_name.endswith(".toml"):
            file_name = file_name + ".toml"
        toml_files = [f for f in toml_files if f.name == file_name]

    result: dict[str, list[Rule]] = {}
    for path in toml_files:
        rules = load_rules_from_file(path)
        if account is not None:
            rules = [r for r in rules if r.account == account]
        if rules:
            result[path.stem] = rules

    return result


def add_rule(file_name: str, rule: Rule, rules_dir: Path | None = None) -> None:
    if rules_dir is None:
        rules_dir = RULES_DIR

    rules_dir.mkdir(parents=True, exist_ok=True)
    path = _validate_rule_path(file_name, rules_dir)

    if path.exists():
        existing = load_rules_from_file(path)
        for r in existing:
            if r.name == rule.name:
                raise ValueError(f"Rule with name '{rule.name}' already exists in {file_name}")
        existing.append(rule)
    else:
        existing = [rule]

    data = {"rules": [r.model_dump() for r in existing]}
    path.write_text(tomli_w.dumps(data))


def delete_rule(file_name: str, rule_name: str, rules_dir: Path | None = None) -> bool:
    if rules_dir is None:
        rules_dir = RULES_DIR

    path = _validate_rule_path(file_name, rules_dir)
    if not path.exists():
        return False

    existing = load_rules_from_file(path)
    filtered = [r for r in existing if r.name != rule_name]

    if len(filtered) == len(existing):
        return False

    if not filtered:
        path.unlink()
    else:
        data = {"rules": [r.model_dump() for r in filtered]}
        path.write_text(tomli_w.dumps(data))

    return True


async def apply_rules(
    rules_by_file: dict[str, list[Rule]],
    since: datetime | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> list[RuleApplyResult]:
    from mcp_email_server.emails.dispatcher import dispatch_handler

    results: list[RuleApplyResult] = []

    for _file_name, rules in rules_by_file.items():
        for rule in rules:
            try:
                handler = dispatch_handler(rule.account)
                response = await handler.apply_filter_rule(
                    senders=rule.senders,
                    target_folder=rule.target_folder,
                    source_mailbox=rule.source_mailbox,
                    since=since,
                    dry_run=dry_run,
                    limit=limit,
                    mark_read=rule.mark_read,
                )
                results.append(
                    RuleApplyResult(
                        rule_name=rule.name,
                        account=rule.account,
                        source_mailbox=rule.source_mailbox,
                        target_folder=rule.target_folder,
                        matched=len(response["matched"]),
                        moved=len(response["moved"]),
                        failed=len(response["failed"]),
                        dry_run=dry_run,
                    )
                )
            except Exception as e:
                logger.error(f"Failed to apply rule '{rule.name}' for account '{rule.account}': {type(e).__name__}: {e!r}")
                results.append(
                    RuleApplyResult(
                        rule_name=rule.name,
                        account=rule.account,
                        source_mailbox=rule.source_mailbox,
                        target_folder=rule.target_folder,
                        matched=0,
                        moved=0,
                        failed=0,
                        dry_run=dry_run,
                    )
                )

    return results
