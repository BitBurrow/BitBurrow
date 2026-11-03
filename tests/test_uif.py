from datetime import timedelta as TimeDelta
import pytest
import hub.util as util
import hub.uif as uif


def test_parse_markdown_sections(tmp_path, monkeypatch):
    input = (
        "## one\n\n"
        + "Text within 'one'.\n\n"
        + "### two\n\n"
        + "Text within 'two'.\n\n"
        + "## ..\n\n"
        + "Text within 'one' again.\n"
    )
    expected_output = [
        "one",
        "\nText within 'one'.\n\n",
        ["two", "\nText within 'two'.\n\n"],
        "\nText within 'one' again.\n",
    ]
    (tmp_path / 'sections.md').write_text(input, encoding='utf-8')
    monkeypatch.setattr(util, 'ui_path', str(tmp_path))
    assert uif.parse_markdown_sections('sections') == expected_output


def test_split_at_ctags():
    input = '{{ zero }}{{ one }}{ foo }{{bar}}{{ two }}'
    assert uif.split_at_ctags(input) == ['{{ zero }}', '{{ one }}', '{ foo }{{bar}}', '{{ two }}']
    input = 'foo {{ three }} bar{{ four }} }}'  # unmatched close are ignored
    assert uif.split_at_ctags(input) == ['foo ', '{{ three }}', ' bar', '{{ four }}', ' }}']
    with pytest.raises(util.Berror, match='B68301'):
        uif.split_at_ctags('{{ thir{{ teen }}{{ fourteen }}')


def test_human_duration():
    assert uif.human_duration(None) == '–'
    assert uif.human_duration(-TimeDelta(seconds=129)) == "-2 minutes"
    assert uif.human_duration(TimeDelta(seconds=129)) == "2 minutes"
    assert uif.human_duration(TimeDelta(seconds=129), positive_only=True) == "2 minutes"
    assert uif.human_duration(TimeDelta(seconds=129), positive_only=False) == "2 minutes"
    assert uif.human_duration(-TimeDelta(seconds=129), positive_only=True) == '–'
    assert uif.human_duration(-TimeDelta(seconds=129), positive_only=False) == "-2 minutes"
    assert uif.human_duration(-TimeDelta(seconds=0)) == "0 seconds"
    assert uif.human_duration(TimeDelta(seconds=0)) == "0 seconds"
    assert uif.human_duration(TimeDelta(seconds=0), positive_only=True) == '–'
    assert uif.human_duration(-TimeDelta(seconds=2)) == "-2 seconds"
    assert uif.human_duration(-TimeDelta(seconds=99)) == "-99 seconds"
    assert uif.human_duration(TimeDelta(seconds=1)) == "1 second"
    assert uif.human_duration(TimeDelta(seconds=5)) == "5 seconds"
    assert uif.human_duration(-TimeDelta(seconds=5)) == "-5 seconds"
    assert uif.human_duration(TimeDelta(seconds=0)) == "0 seconds"
    assert uif.human_duration(TimeDelta(seconds=100)) == "2 minutes"
    assert uif.human_duration(TimeDelta(seconds=149)) == "2 minutes"
    assert uif.human_duration(TimeDelta(seconds=151)) == "3 minutes"  # 150 could go either way
    assert uif.human_duration(TimeDelta(seconds=152)) == "3 minutes"
    assert uif.human_duration(TimeDelta(seconds=160)) == "3 minutes"
    assert uif.human_duration(TimeDelta(minutes=1)) == "60 seconds"
    assert uif.human_duration(TimeDelta(minutes=2)) == "2 minutes"
    assert uif.human_duration(-TimeDelta(minutes=2)) == "-2 minutes"
    assert uif.human_duration(TimeDelta(minutes=99)) == "99 minutes"
    assert uif.human_duration(TimeDelta(seconds=6000)) == "2 hours"
    assert uif.human_duration(TimeDelta(hours=1)) == "60 minutes"
    assert uif.human_duration(-TimeDelta(hours=49)) == "-49 hours"
    assert uif.human_duration(-TimeDelta(hours=50)) == "-2 days"
    assert uif.human_duration(TimeDelta(hours=1)) == "60 minutes"
    assert uif.human_duration(TimeDelta(hours=25)) == "25 hours"
    assert uif.human_duration(TimeDelta(hours=30)) == "30 hours"
    assert uif.human_duration(TimeDelta(days=2)) == "48 hours"
    assert uif.human_duration(TimeDelta(days=29)) == "29 days"
    assert uif.human_duration(TimeDelta(days=30)) == "4 weeks"
    assert uif.human_duration(TimeDelta(days=1)) == "24 hours"
    assert uif.human_duration(TimeDelta(days=29)) == "29 days"
    assert uif.human_duration(TimeDelta(days=28)) == "28 days"
    assert uif.human_duration(TimeDelta(weeks=2)) == "14 days"
    assert uif.human_duration(TimeDelta(weeks=3)) == "21 days"
    assert uif.human_duration(TimeDelta(weeks=10)) == "2 months"
    assert uif.human_duration(TimeDelta(weeks=5)) == "5 weeks"
    assert uif.human_duration(TimeDelta(weeks=3)) == "21 days"
    assert uif.human_duration(TimeDelta(weeks=8)) == "8 weeks"
    assert uif.human_duration(TimeDelta(weeks=9)) == "9 weeks"
    assert uif.human_duration(TimeDelta(days=300)) == "10 months"
    assert uif.human_duration(TimeDelta(days=600)) == "2 years"
    assert uif.human_duration(TimeDelta(days=60)) == "9 weeks"
    assert uif.human_duration(TimeDelta(days=300)) == "10 months"
    assert uif.human_duration(TimeDelta(days=90)) == "3 months"
    assert uif.human_duration(TimeDelta(days=365)) == "12 months"
    assert uif.human_duration(TimeDelta(days=360)) == "12 months"
    assert uif.human_duration(TimeDelta(days=800)) == "2 years"
    assert uif.human_duration(TimeDelta(days=2000)) == "5 years"
    assert uif.human_duration(TimeDelta(days=800)) == "2 years"
    assert uif.human_duration(TimeDelta(days=365 * 20)) == "20 years"
