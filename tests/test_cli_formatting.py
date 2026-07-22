from mcp_email_server.cli.formatting import print_error


class TestPrintError:
    """print_error must never emit a bare "Error: " with no detail.

    Some exceptions (e.g. TimeoutError) stringify to "", which previously
    produced an empty error line when call sites passed str(e).
    """

    def test_plain_string_message(self, capsys):
        print_error("something broke")
        assert "Error: something broke" in capsys.readouterr().out

    def test_exception_with_message(self, capsys):
        print_error(ValueError("bad input"))
        assert "Error: bad input" in capsys.readouterr().out

    def test_exception_with_empty_message_falls_back_to_type_name(self, capsys):
        print_error(TimeoutError())
        assert "Error: TimeoutError" in capsys.readouterr().out
