import pytest

from bot.textutil import (
    clean_url, hashtag, html_to_text, same_story, strip_feed_junk, title_fingerprint, truncate,
    url_key,
)


def test_html_to_text_keeps_block_structure():
    raw = "<p>Один&nbsp;абзац</p><ul><li>Два</li><li>Три</li></ul><script>x()</script>"
    assert html_to_text(raw) == "Один абзац\nДва\nТри"


def test_html_to_text_plain_passthrough():
    assert html_to_text("  просто   текст ") == "просто текст"
    assert html_to_text(None) == ""


@pytest.mark.parametrize("raw, expected", [
    ("Текст новости. Читать дальше →", "Текст новости."),
    ("Текст новостиЧитать далее", "Текст новости"),
    ("Body text. The post Juventus win appeared first on Football Italia.", "Body text."),
    ("Some text [...]", "Some text…"),
])
def test_strip_feed_junk(raw, expected):
    assert strip_feed_junk(raw) == expected


def test_truncate_prefers_sentence_boundary():
    text = "Первое предложение здесь. Второе предложение тоже есть. Третье очень длинное " * 3
    result = truncate(text, 70)
    assert len(result) <= 70
    assert result.endswith(".")


def test_truncate_word_boundary_with_ellipsis():
    result = truncate("слово " * 50, 40)
    assert result.endswith("…") and len(result) <= 41
    assert truncate("короткий", 40) == "короткий"


def test_clean_url_removes_trackers_and_fragment():
    url = "https://www.bbc.co.uk/sport/football/articles/abc?at_medium=RSS&at_campaign=rss&page=2#0"
    assert clean_url(url) == "https://www.bbc.co.uk/sport/football/articles/abc?page=2"
    assert "utm_" not in clean_url("https://x.ru/a?utm_source=tg&id=5")


def test_clean_url_encodes_non_ascii_path():
    assert clean_url("https://example.com/новость") == (
        "https://example.com/%D0%BD%D0%BE%D0%B2%D0%BE%D1%81%D1%82%D1%8C"
    )


def test_url_key_ignores_www_scheme_and_trailing_slash():
    assert url_key("https://www.sports.ru/news/1/") == url_key("http://sports.ru/news/1?utm_x=1")


@pytest.mark.parametrize("a, b, expected", [
    ("Скауты «Барселоны» и «Баварии» изучат игру Батракова с «Астон Виллой»",
     "Представители «Барселоны» и «Баварии» приедут посмотреть на игру Батракова в матче ЛЧ",
     True),
    ("Деклан Райс согласовал пятилетний контракт с «Арсеналом» — Джейкобс",
     "«Арсенал» продлил контракт с Декланом Райсом до 2031 года", True),
    ("«Реал» — «Барселона» 2:1. Мбаппе забил победный гол",
     "«Реал» обыграл «Барселону» со счётом 2-1, Мбаппе — автор победного гола", True),
    ("Мбаппе забил два гола за «Реал» в матче с «Хетафе»",
     "Мбаппе получил травму в матче с «Хетафе»", False),
    ("«Барселона» обыграла «Баварию» в ЛЧ", "«Бавария» хочет купить игрока «Барселоны»", False),
    ("Холанд оформил хет-трик в матче с «Эвертоном»",
     "Холанд получил травму в матче с «Эвертоном»", False),
    ("Гол", "Гол", False),  # too short to judge
])
def test_same_story(a, b, expected):
    assert same_story(title_fingerprint(a), title_fingerprint(b)) is expected


def test_hashtag():
    assert hashtag("Лига 1") == "#Лига1"
    assert hashtag("Ле-Ман") == "#ЛеМан"
    assert hashtag("1. FC") is None
    assert hashtag("") is None
