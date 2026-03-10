import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import tomli_w
from pydantic import ValidationError

from mcp_email_server.config import EmailServer, EmailSettings
from mcp_email_server.emails.classic import ClassicEmailHandler, EmailClient
from mcp_email_server.rules import (
    Rule,
    RuleFile,
    add_rule,
    apply_rules,
    delete_rule,
    load_all_rules,
    load_rules_from_file,
)


@pytest.fixture
def email_settings():
    return EmailSettings(
        account_name="test_account",
        full_name="Test User",
        email_address="test@example.com",
        incoming=EmailServer(
            user_name="test_user",
            password="test_password",
            host="imap.example.com",
            port=993,
            use_ssl=True,
        ),
        outgoing=EmailServer(
            user_name="test_user",
            password="test_password",
            host="smtp.example.com",
            port=465,
            use_ssl=True,
        ),
    )


@pytest.fixture
def email_client(email_settings):
    return EmailClient(email_settings.incoming)


@pytest.fixture
def classic_handler(email_settings):
    return ClassicEmailHandler(email_settings)


def _make_mock_imap():
    """Create a mock IMAP connection with standard setup."""
    mock_imap = AsyncMock()
    future = asyncio.get_event_loop().create_future()
    future.set_result(None)
    mock_imap._client_task = future
    mock_imap.wait_hello_from_server = AsyncMock()
    mock_imap.login = AsyncMock()
    mock_imap.logout = AsyncMock()
    mock_imap.select = AsyncMock()
    mock_imap.list = AsyncMock()
    mock_imap.create = AsyncMock()
    mock_imap.uid = AsyncMock()
    mock_imap.uid_search = AsyncMock()
    mock_imap.expunge = AsyncMock()
    return mock_imap


def _sample_rule(**overrides):
    defaults = {
        "name": "test-rule",
        "account": "test_account",
        "target_folder": "Newsletters",
        "senders": ["alice@example.com", "bob@example.com"],
    }
    defaults.update(overrides)
    return Rule(**defaults)


def _write_rules_toml(path, rules):
    data = {"rules": [r.model_dump() for r in rules]}
    path.write_text(tomli_w.dumps(data))


class TestRuleModel:
    def test_valid_rule(self):
        rule = Rule(
            name="my-rule",
            account="acct1",
            target_folder="Archive",
            senders=["a@b.com"],
            source_mailbox="Sent",
        )
        assert rule.name == "my-rule"
        assert rule.account == "acct1"
        assert rule.target_folder == "Archive"
        assert rule.senders == ["a@b.com"]
        assert rule.source_mailbox == "Sent"

    def test_rule_defaults(self):
        rule = _sample_rule()
        assert rule.source_mailbox == "INBOX"

    def test_empty_senders_and_subjects_raises(self):
        with pytest.raises(ValidationError, match="must specify either senders or subjects"):
            Rule(name="bad", account="acct", target_folder="X")

    def test_both_senders_and_subjects_raises(self):
        with pytest.raises(ValidationError, match="not both"):
            Rule(name="bad", account="acct", target_folder="X", senders=["a@b.com"], subjects=["test"])

    def test_subject_rule(self):
        rule = Rule(name="subj", account="acct", target_folder="Alerts", subjects=["Alert", "Notification"])
        assert rule.subjects == ["Alert", "Notification"]
        assert rule.senders == []


class TestRuleFile:
    def test_valid_rule_file(self):
        r1 = _sample_rule(name="r1")
        r2 = _sample_rule(name="r2", senders=["c@d.com"])
        rf = RuleFile(rules=[r1, r2])
        assert len(rf.rules) == 2
        assert rf.rules[0].name == "r1"
        assert rf.rules[1].name == "r2"


class TestLoadRules:
    def test_load_rules_from_file(self, tmp_path):
        rule = _sample_rule()
        path = tmp_path / "test.toml"
        _write_rules_toml(path, [rule])

        loaded = load_rules_from_file(path)
        assert len(loaded) == 1
        assert loaded[0].name == "test-rule"
        assert loaded[0].senders == ["alice@example.com", "bob@example.com"]

    def test_load_rules_missing_file(self, tmp_path):
        result = load_rules_from_file(tmp_path / "nonexistent.toml")
        assert result == []

    def test_load_rules_invalid_toml(self, tmp_path):
        path = tmp_path / "bad.toml"
        path.write_text("this is {{not valid toml")
        result = load_rules_from_file(path)
        assert result == []

    def test_load_all_rules(self, tmp_path):
        r1 = _sample_rule(name="r1", account="acct1")
        r2 = _sample_rule(name="r2", account="acct2")
        _write_rules_toml(tmp_path / "file1.toml", [r1])
        _write_rules_toml(tmp_path / "file2.toml", [r2])

        result = load_all_rules(rules_dir=tmp_path)
        assert len(result) == 2
        assert "file1" in result
        assert "file2" in result
        assert result["file1"][0].name == "r1"
        assert result["file2"][0].name == "r2"

    def test_load_all_rules_filter_account(self, tmp_path):
        r1 = _sample_rule(name="r1", account="acct1")
        r2 = _sample_rule(name="r2", account="acct2")
        _write_rules_toml(tmp_path / "mixed.toml", [r1, r2])

        result = load_all_rules(rules_dir=tmp_path, account="acct1")
        assert len(result) == 1
        assert result["mixed"][0].name == "r1"

    def test_load_all_rules_filter_file(self, tmp_path):
        r1 = _sample_rule(name="r1")
        r2 = _sample_rule(name="r2")
        _write_rules_toml(tmp_path / "keep.toml", [r1])
        _write_rules_toml(tmp_path / "skip.toml", [r2])

        result = load_all_rules(rules_dir=tmp_path, file_name="keep")
        assert len(result) == 1
        assert "keep" in result

    def test_load_all_rules_empty_dir(self, tmp_path):
        result = load_all_rules(rules_dir=tmp_path)
        assert result == {}

    def test_load_all_rules_nonexistent_dir(self, tmp_path):
        result = load_all_rules(rules_dir=tmp_path / "nope")
        assert result == {}


class TestAddDeleteRule:
    def test_add_rule_new_file(self, tmp_path):
        rule = _sample_rule()
        add_rule("myrules", rule, rules_dir=tmp_path)

        loaded = load_rules_from_file(tmp_path / "myrules.toml")
        assert len(loaded) == 1
        assert loaded[0].name == "test-rule"

    def test_add_rule_existing_file(self, tmp_path):
        r1 = _sample_rule(name="r1")
        add_rule("myrules", r1, rules_dir=tmp_path)

        r2 = _sample_rule(name="r2", senders=["x@y.com"])
        add_rule("myrules", r2, rules_dir=tmp_path)

        loaded = load_rules_from_file(tmp_path / "myrules.toml")
        assert len(loaded) == 2
        assert {r.name for r in loaded} == {"r1", "r2"}

    def test_add_rule_duplicate_name(self, tmp_path):
        rule = _sample_rule(name="dup")
        add_rule("myrules", rule, rules_dir=tmp_path)

        with pytest.raises(ValueError, match="already exists"):
            add_rule("myrules", rule, rules_dir=tmp_path)

    def test_add_rule_auto_toml_extension(self, tmp_path):
        rule = _sample_rule()
        add_rule("noext", rule, rules_dir=tmp_path)
        assert (tmp_path / "noext.toml").exists()

    def test_delete_rule(self, tmp_path):
        r1 = _sample_rule(name="r1")
        r2 = _sample_rule(name="r2", senders=["x@y.com"])
        add_rule("myrules", r1, rules_dir=tmp_path)
        add_rule("myrules", r2, rules_dir=tmp_path)

        result = delete_rule("myrules", "r1", rules_dir=tmp_path)
        assert result is True

        loaded = load_rules_from_file(tmp_path / "myrules.toml")
        assert len(loaded) == 1
        assert loaded[0].name == "r2"

    def test_delete_rule_nonexistent(self, tmp_path):
        rule = _sample_rule()
        add_rule("myrules", rule, rules_dir=tmp_path)

        result = delete_rule("myrules", "no-such-rule", rules_dir=tmp_path)
        assert result is False

    def test_delete_rule_removes_empty_file(self, tmp_path):
        rule = _sample_rule()
        add_rule("myrules", rule, rules_dir=tmp_path)

        delete_rule("myrules", "test-rule", rules_dir=tmp_path)
        assert not (tmp_path / "myrules.toml").exists()

    def test_delete_rule_missing_file(self, tmp_path):
        result = delete_rule("missing", "anything", rules_dir=tmp_path)
        assert result is False

    def test_add_rule_path_traversal(self, tmp_path):
        rule = _sample_rule()
        with pytest.raises(ValueError, match="Invalid file name"):
            add_rule("../../etc/evil", rule, rules_dir=tmp_path)

    def test_delete_rule_path_traversal(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid file name"):
            delete_rule("../../../etc/evil", "anything", rules_dir=tmp_path)


class TestApplyRules:
    @pytest.mark.asyncio
    async def test_apply_rules_calls_handler(self):
        mock_handler = AsyncMock()
        mock_handler.apply_filter_rule.return_value = {
            "matched": ["101", "102"],
            "moved": ["101", "102"],
            "failed": [],
        }

        rule = _sample_rule()
        rules_by_file = {"test_file": [rule]}

        with patch("mcp_email_server.emails.dispatcher.dispatch_handler", return_value=mock_handler) as mock_dispatch:
            results = await apply_rules(rules_by_file)

        mock_dispatch.assert_called_once_with("test_account")
        mock_handler.apply_filter_rule.assert_called_once_with(
            senders=["alice@example.com", "bob@example.com"],
            subjects=None,
            target_folder="Newsletters",
            source_mailbox="INBOX",
            since=None,
            dry_run=False,
            limit=None,
            mark_read=False,
        )
        assert len(results) == 1
        assert results[0].rule_name == "test-rule"
        assert results[0].matched == 2
        assert results[0].moved == 2
        assert results[0].failed == 0

    @pytest.mark.asyncio
    async def test_apply_rules_dry_run(self):
        mock_handler = AsyncMock()
        mock_handler.apply_filter_rule.return_value = {
            "matched": ["101"],
            "moved": [],
            "failed": [],
        }

        rule = _sample_rule()
        rules_by_file = {"f": [rule]}

        with patch("mcp_email_server.emails.dispatcher.dispatch_handler", return_value=mock_handler):
            results = await apply_rules(rules_by_file, dry_run=True)

        mock_handler.apply_filter_rule.assert_called_once_with(
            senders=rule.senders,
            subjects=None,
            target_folder=rule.target_folder,
            source_mailbox="INBOX",
            since=None,
            dry_run=True,
            limit=None,
            mark_read=False,
        )
        assert results[0].dry_run is True

    @pytest.mark.asyncio
    async def test_apply_rules_handler_error(self):
        mock_handler = AsyncMock()
        mock_handler.apply_filter_rule.side_effect = RuntimeError("IMAP down")

        rule = _sample_rule()
        rules_by_file = {"f": [rule]}

        with patch("mcp_email_server.emails.dispatcher.dispatch_handler", return_value=mock_handler):
            results = await apply_rules(rules_by_file)

        assert len(results) == 1
        assert results[0].matched == 0
        assert results[0].moved == 0
        assert results[0].failed == 0

    @pytest.mark.asyncio
    async def test_apply_rules_multiple_rules(self):
        mock_handler = AsyncMock()
        mock_handler.apply_filter_rule.side_effect = [
            {"matched": ["1"], "moved": ["1"], "failed": []},
            {"matched": ["2", "3"], "moved": ["2"], "failed": ["3"]},
        ]

        r1 = _sample_rule(name="r1", account="acct")
        r2 = _sample_rule(name="r2", account="acct", senders=["z@z.com"])
        rules_by_file = {"f1": [r1], "f2": [r2]}

        with patch("mcp_email_server.emails.dispatcher.dispatch_handler", return_value=mock_handler):
            results = await apply_rules(rules_by_file)

        assert len(results) == 2
        assert results[0].rule_name == "r1"
        assert results[0].matched == 1
        assert results[0].moved == 1
        assert results[1].rule_name == "r2"
        assert results[1].matched == 2
        assert results[1].moved == 1
        assert results[1].failed == 1


class TestApplyFilterRule:
    @pytest.mark.asyncio
    async def test_apply_filter_rule_basic(self, email_client):
        mock_imap = _make_mock_imap()

        # Combined OR query returns all matching UIDs at once
        mock_imap.uid_search.return_value = ("OK", [b"101 102 103"])

        # Folder check passes
        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "Newsletters"'])

        # MOVE succeeds for all
        move_response = MagicMock()
        move_response.result = "OK"
        mock_imap.uid.return_value = move_response

        email_client.imap_class = MagicMock(return_value=mock_imap)

        result = await email_client.apply_filter_rule(
            senders=["alice@example.com", "bob@example.com"],
            target_folder="Newsletters",
        )

        assert sorted(result["matched"], key=int) == ["101", "102", "103"]
        assert sorted(result["moved"], key=int) == ["101", "102", "103"]
        assert result["failed"] == []
        # Two senders fit in one chunk — single IMAP search call
        assert mock_imap.uid_search.call_count == 1
        call_args = list(mock_imap.uid_search.call_args.args)
        assert "OR" in call_args

    @pytest.mark.asyncio
    async def test_apply_filter_rule_copy_delete_fallback(self, email_client):
        """Test COPY+DELETE fallback when MOVE is not supported."""
        mock_imap = _make_mock_imap()
        mock_imap.uid_search.return_value = ("OK", [b"401"])

        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "Archive"'])

        copy_response = MagicMock()
        copy_response.result = "OK"

        async def uid_side_effect(cmd, *args):
            if cmd == "move":
                raise OSError("MOVE not supported")
            if cmd == "copy":
                return copy_response
            return MagicMock(result="OK")

        mock_imap.uid.side_effect = uid_side_effect
        email_client.imap_class = MagicMock(return_value=mock_imap)

        result = await email_client.apply_filter_rule(
            senders=["test@example.com"],
            target_folder="Archive",
        )

        assert result["matched"] == ["401"]
        assert result["moved"] == ["401"]
        assert result["failed"] == []
        mock_imap.expunge.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_filter_rule_dry_run(self, email_client):
        mock_imap = _make_mock_imap()
        mock_imap.uid_search.return_value = ("OK", [b"201 202"])

        email_client.imap_class = MagicMock(return_value=mock_imap)

        result = await email_client.apply_filter_rule(
            senders=["test@example.com"],
            target_folder="Archive",
            dry_run=True,
        )

        assert result["matched"] == ["201", "202"]
        assert result["moved"] == []
        assert result["failed"] == []
        # No move commands issued
        mock_imap.uid.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_filter_rule_no_matches(self, email_client):
        mock_imap = _make_mock_imap()
        mock_imap.uid_search.return_value = ("OK", [b""])

        email_client.imap_class = MagicMock(return_value=mock_imap)

        result = await email_client.apply_filter_rule(
            senders=["nobody@example.com"],
            target_folder="Archive",
        )

        assert result["matched"] == []
        assert result["moved"] == []
        assert result["failed"] == []

    @pytest.mark.asyncio
    async def test_apply_filter_rule_with_since(self, email_client):
        mock_imap = _make_mock_imap()
        mock_imap.uid_search.return_value = ("OK", [b"301"])

        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "Archive"'])
        move_response = MagicMock()
        move_response.result = "OK"
        mock_imap.uid.return_value = move_response

        email_client.imap_class = MagicMock(return_value=mock_imap)

        since = datetime(2026, 1, 1)
        result = await email_client.apply_filter_rule(
            senders=["test@example.com"],
            target_folder="Archive",
            since=since,
        )

        assert result["matched"] == ["301"]
        # Verify uid_search was called with SINCE in criteria
        call_args = mock_imap.uid_search.call_args
        # The positional args should contain SINCE and the formatted date
        all_args = list(call_args.args) if call_args.args else []
        assert "SINCE" in all_args
        assert "01-JAN-2026" in all_args


class TestBuildOrCriteria:
    def test_single_sender(self):
        from mcp_email_server.emails.classic import EmailClient

        result = EmailClient._build_or_criteria("FROM", ["alice@example.com"])
        assert result == ["FROM", '"alice@example.com"']

    def test_two_senders(self):
        from mcp_email_server.emails.classic import EmailClient

        result = EmailClient._build_or_criteria("FROM", ["a@x.com", "b@x.com"])
        assert result == ["OR", "FROM", '"a@x.com"', "FROM", '"b@x.com"']

    def test_three_senders(self):
        from mcp_email_server.emails.classic import EmailClient

        result = EmailClient._build_or_criteria("FROM", ["a@x.com", "b@x.com", "c@x.com"])
        assert result == ["OR", "FROM", '"a@x.com"', "OR", "FROM", '"b@x.com"', "FROM", '"c@x.com"']

    def test_subject_field(self):
        from mcp_email_server.emails.classic import EmailClient

        result = EmailClient._build_or_criteria("SUBJECT", ["Alert", "Notification"])
        assert result == ["OR", "SUBJECT", '"Alert"', "SUBJECT", '"Notification"']


class TestBuildUidSet:
    def test_single_uid(self):
        from mcp_email_server.emails.classic import EmailClient

        assert EmailClient._build_uid_set(["42"]) == "42"

    def test_consecutive_range(self):
        from mcp_email_server.emails.classic import EmailClient

        assert EmailClient._build_uid_set(["1", "2", "3", "4", "5"]) == "1:5"

    def test_gaps(self):
        from mcp_email_server.emails.classic import EmailClient

        assert EmailClient._build_uid_set(["1", "3", "5"]) == "1,3,5"

    def test_mixed_ranges_and_gaps(self):
        from mcp_email_server.emails.classic import EmailClient

        assert EmailClient._build_uid_set(["1", "2", "3", "5", "7", "8", "9"]) == "1:3,5,7:9"

    def test_empty_list(self):
        from mcp_email_server.emails.classic import EmailClient

        assert EmailClient._build_uid_set([]) == ""


class TestSearchByFieldChunking:
    @pytest.mark.asyncio
    async def test_chunking_deduplicates_across_chunks(self, email_client):
        """Two chunks with overlapping UIDs are deduplicated."""
        mock_imap = _make_mock_imap()

        mock_imap.uid_search.side_effect = [
            ("OK", [b"1 2"]),
            ("OK", [b"2 3"]),
        ]

        result = await email_client._search_by_field(
            mock_imap, "FROM", ["a@x.com", "b@x.com", "c@x.com"], since=None, chunk_size=2
        )

        assert result == ["1", "2", "3"]
        assert mock_imap.uid_search.call_count == 2

    @pytest.mark.asyncio
    async def test_chunking_builds_or_criteria_per_chunk(self, email_client):
        """Each chunk builds its own OR tree in the search criteria."""
        mock_imap = _make_mock_imap()

        mock_imap.uid_search.side_effect = [
            ("OK", [b"1"]),
            ("OK", [b"2"]),
        ]

        await email_client._search_by_field(
            mock_imap, "FROM", ["a@x.com", "b@x.com", "c@x.com"], since=None, chunk_size=2
        )

        # First call: 2 senders → OR query
        first_call_args = list(mock_imap.uid_search.call_args_list[0].args)
        assert "OR" in first_call_args

        # Second call: 1 sender → no OR
        second_call_args = list(mock_imap.uid_search.call_args_list[1].args)
        assert "OR" not in second_call_args

    @pytest.mark.asyncio
    async def test_subject_search(self, email_client):
        """SUBJECT field search works with the same chunking logic."""
        mock_imap = _make_mock_imap()
        mock_imap.uid_search.return_value = ("OK", [b"10 20"])

        result = await email_client._search_by_field(
            mock_imap, "SUBJECT", ["Alert", "Notification"], since=None
        )

        assert result == ["10", "20"]
        call_args = list(mock_imap.uid_search.call_args.args)
        assert "SUBJECT" in call_args
        assert "OR" in call_args


class TestBatchMoveUids:
    @pytest.mark.asyncio
    async def test_batch_move_with_small_batch_size(self, email_client):
        """UIDs are split into batches and each batch is moved separately."""
        mock_imap = _make_mock_imap()
        move_response = MagicMock()
        move_response.result = "OK"
        mock_imap.uid.return_value = move_response

        moved, failed = await email_client._batch_move_uids(
            mock_imap, ["1", "2", "3", "4", "5"], "Target", batch_size=2
        )

        assert moved == ["1", "2", "3", "4", "5"]
        assert failed == []
        # 3 batches: [1,2], [3,4], [5] → 3 move calls
        move_calls = [c for c in mock_imap.uid.call_args_list if c.args[0] == "move"]
        assert len(move_calls) == 3
        mock_imap.expunge.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_move_fails_fast(self, email_client):
        """On batch failure, remaining batches are not attempted."""
        mock_imap = _make_mock_imap()

        batch_count = 0

        async def uid_side_effect(cmd, *args):
            nonlocal batch_count
            if cmd == "move":
                batch_count += 1
                if batch_count == 2:
                    # MOVE fails
                    raise OSError("MOVE not supported")
                response = MagicMock()
                response.result = "OK"
                return response
            if cmd == "copy":
                # COPY also fails on the second batch
                response = MagicMock()
                response.result = "NO"
                return response
            return MagicMock(result="OK")

        mock_imap.uid.side_effect = uid_side_effect

        moved, failed = await email_client._batch_move_uids(
            mock_imap, ["1", "2", "3", "4", "5", "6"], "Target", batch_size=2
        )

        assert moved == ["1", "2"]
        assert failed == ["3", "4", "5", "6"]
        # Batch [5,6] was never attempted but is included in failed
        assert batch_count == 2

    @pytest.mark.asyncio
    async def test_batch_move_with_mark_read(self, email_client):
        """mark_read=True sets \\Seen flag before moving each batch."""
        mock_imap = _make_mock_imap()
        move_response = MagicMock()
        move_response.result = "OK"
        mock_imap.uid.return_value = move_response

        moved, failed = await email_client._batch_move_uids(
            mock_imap, ["1", "2", "3"], "Target", batch_size=2, mark_read=True
        )

        assert moved == ["1", "2", "3"]
        assert failed == []
        # Check that store \Seen was called before each move
        uid_calls = mock_imap.uid.call_args_list
        store_calls = [c for c in uid_calls if c.args[0] == "store" and r"(\Seen)" in c.args]
        assert len(store_calls) == 2  # 2 batches

    @pytest.mark.asyncio
    async def test_batch_move_uses_uid_set_compression(self, email_client):
        """Consecutive UIDs are compressed into range notation."""
        mock_imap = _make_mock_imap()
        move_response = MagicMock()
        move_response.result = "OK"
        mock_imap.uid.return_value = move_response

        await email_client._batch_move_uids(
            mock_imap, ["1", "2", "3", "5", "6"], "Target", batch_size=100
        )

        move_call = next(c for c in mock_imap.uid.call_args_list if c.args[0] == "move")
        uid_set_arg = move_call.args[1]
        assert uid_set_arg == "1:3,5:6"


class TestApplyFilterRuleLimit:
    @pytest.mark.asyncio
    async def test_limit_caps_moves_but_reports_full_matched(self, email_client):
        """limit caps emails moved but matched reports the full search results."""
        mock_imap = _make_mock_imap()
        mock_imap.uid_search.return_value = ("OK", [b"1 2 3 4 5"])
        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "Target"'])
        move_response = MagicMock()
        move_response.result = "OK"
        mock_imap.uid.return_value = move_response

        email_client.imap_class = MagicMock(return_value=mock_imap)

        result = await email_client.apply_filter_rule(
            senders=["test@example.com"],
            target_folder="Target",
            limit=3,
        )

        assert result["matched"] == ["1", "2", "3", "4", "5"]
        assert result["moved"] == ["1", "2", "3"]
        assert result["failed"] == []


class TestApplyFilterRuleNegativeLimit:
    @pytest.mark.asyncio
    async def test_negative_limit_raises(self, email_client):
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            await email_client.apply_filter_rule(senders=["a@b.com"], target_folder="X", limit=-1)

    @pytest.mark.asyncio
    async def test_zero_limit_raises(self, email_client):
        with pytest.raises(ValueError, match="limit must be a positive integer"):
            await email_client.apply_filter_rule(senders=["a@b.com"], target_folder="X", limit=0)


class TestApplyFilterRuleSubjects:
    @pytest.mark.asyncio
    async def test_apply_filter_rule_with_subjects(self, email_client):
        """Subject-based rules use SUBJECT field in IMAP search."""
        mock_imap = _make_mock_imap()
        mock_imap.uid_search.return_value = ("OK", [b"50 51"])
        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "Alerts"'])
        move_response = MagicMock()
        move_response.result = "OK"
        mock_imap.uid.return_value = move_response

        email_client.imap_class = MagicMock(return_value=mock_imap)

        result = await email_client.apply_filter_rule(
            subjects=["Alert", "Notification"],
            target_folder="Alerts",
        )

        assert result["matched"] == ["50", "51"]
        assert result["moved"] == ["50", "51"]
        assert result["failed"] == []
        call_args = list(mock_imap.uid_search.call_args.args)
        assert "SUBJECT" in call_args
        assert "OR" in call_args

    @pytest.mark.asyncio
    async def test_apply_filter_rule_no_criteria_raises(self, email_client):
        """Calling with neither senders nor subjects raises ValueError."""
        with pytest.raises(ValueError, match="Either senders or subjects must be provided"):
            await email_client.apply_filter_rule(target_folder="X")


class TestClassicHandlerApplyFilterRule:
    @pytest.mark.asyncio
    async def test_handler_delegates_to_client(self, classic_handler):
        expected = {"matched": ["100"], "moved": ["100"], "failed": []}
        classic_handler.incoming_client.apply_filter_rule = AsyncMock(return_value=expected)

        since = datetime(2026, 3, 1)
        result = await classic_handler.apply_filter_rule(
            senders=["a@b.com"],
            target_folder="Archive",
            source_mailbox="INBOX",
            since=since,
            dry_run=True,
        )

        assert result == expected
        classic_handler.incoming_client.apply_filter_rule.assert_called_once_with(
            senders=["a@b.com"],
            subjects=None,
            target_folder="Archive",
            source_mailbox="INBOX",
            since=since,
            dry_run=True,
            limit=None,
            mark_read=False,
        )
