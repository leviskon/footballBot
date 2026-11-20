import re

import pytest

from bot.catalog import CATEGORIES, ENTITY_BY_NAME, EXCLUDE_ALIASES, HARD_EXCLUDE_ALIASES
from bot.classifier import Classifier, compile_alias

classifier = Classifier()


@pytest.mark.parametrize("title, categories, entities", [
    ("«Барселона» обыграла «Реал» в Эль Класико", ["laliga"], ["Барселона", "Реал Мадрид"]),
    ("«Реал Сосьедад» уступил «Атлетику»", ["laliga"], ["Реал Сосьедад", "Атлетик"]),
    ("«Атлетико» разгромил «Севилью»", ["laliga"], ["Атлетико", "Севилья"]),
    ("«Арсенал» продлил контракт с Сакой", ["epl", "stars"], ["Арсенал", "Сака"]),
    ("АПЛ: «Манчестер Сити» потерял очки", ["epl"], ["Манчестер Сити"]),
    ("Manchester United sign new midfielder", ["epl"], ["Манчестер Юнайтед"]),
    ("«Интер» обыграл «Милан» в дерби", ["seriea"], ["Интер", "Милан"]),
    ("Серия А. «Рома» — «Лацио» 1:1", ["seriea"], ["Рома", "Лацио"]),
    ("Лига 1: «Марсель» обыграл «Лион»", ["ligue1"], ["Марсель", "Лион"]),
    ("Lens beat PSG in Ligue 1", ["ligue1"], ["Ланс", "ПСЖ"]),
    ("«Брест» проиграл «Ренну»", ["ligue1"], ["Брест", "Ренн"]),
    ("«Боруссия» (Д) проиграла «Боруссии» (М)", ["bundesliga"],
     ["Боруссия Дортмунд", "Боруссия М"]),
    ("«Байер» и «Байерн» сыграли вничью", ["bundesliga"], ["Байер", "Бавария"]),
    ("Матч Лиги чемпионов «Бавария» — «ПСЖ»", ["ucl", "bundesliga", "ligue1"],
     ["Бавария", "ПСЖ"]),
    ("Сборная Литвы сыграла в Лиге наций", ["unl"], []),
    ("Финал Лиги Европы пройдёт в Стамбуле", ["uel"], []),
    ("Мбаппе оформил хет-трик", ["stars"], ["Мбаппе"]),
    ("Слот: «Ливерпуль» заслужил победу", ["stars", "epl"], ["Слот", "Ливерпуль"]),
])
def test_classification(title, categories, entities):
    result = classifier.classify(title)
    assert list(result.categories) == categories
    assert list(result.entities) == entities


@pytest.mark.parametrize("title", [
    "Тульский «Арсенал» уволил тренера",  # Russian Arsenal, not the London one
    "Лионель Месси забил за «Интер Майами»",  # not Inter, not Lyon
    "Милан Шкриниар вернулся в сборную",  # a person, not the club
    "Российская премьер-лига: «Зенит» победил «Спартак»",
    "Леонченко: по инфраструктуре РПЛ — одна из ведущих лиг Европы",
    "Inter Miami win MLS Cup",
    "New camera lens tested",
    "«Динамо» Брест обыграло БАТЭ",
    "Гаврилов перешёл в «Локомотив»",
])
def test_not_top_league(title):
    result = classifier.classify(title)
    assert not {"epl", "laliga", "bundesliga", "seriea", "ligue1", "ucl", "uel"} & set(
        result.categories), result


def test_indian_premier_league_is_not_epl():
    result = classifier.classify(
        "India's Dhoni retires",
        "MS Dhoni retired from playing with Chennai Super Kings in the Indian Premier League",
    )
    assert "epl" not in result.categories


def test_preset_category_comes_first():
    result = classifier.classify("Juventus beat Real Madrid", preset="seriea")
    assert result.categories[0] == "seriea"
    assert "laliga" in result.categories


def test_exclusions():
    assert classifier.classify("«Зенит» обыграл «Спартак»").excluded
    assert classifier.classify("Женская сборная Англии выиграла").hard_excluded
    assert classifier.classify("Arsenal Women beat Chelsea Women").hard_excluded
    assert not classifier.classify("«Арсенал» обыграл «Челси»").hard_excluded


def test_every_alias_compiles():
    aliases = [*EXCLUDE_ALIASES, *HARD_EXCLUDE_ALIASES]
    for category in CATEGORIES:
        aliases += category.keywords
        for entity in category.entities:
            aliases += entity.aliases + entity.unless
    for alias in aliases:
        re.compile(compile_alias(alias))


def test_entity_names_are_unique():
    names = [e.name for c in CATEGORIES for e in c.entities]
    assert len(names) == len(set(names)) == len(ENTITY_BY_NAME)


@pytest.mark.parametrize("title, categories, entities", [
    ("El Real Madrid golea en la Champions", ["laliga", "ucl"], ["Реал Мадрид"]),
    ("La Real Sociedad en Liga Europa", ["laliga", "uel"], ["Реал Сосьедад"]),
    ("Ligue Europa Conference : Strasbourg qualifié", ["uecl", "ligue1"], ["Страсбур"]),
    ("Ligue des nations : la France en quarts", ["unl"], []),
    ("El Como de Fàbregas gana al Cagliari", ["seriea"], ["Комо", "Кальяри"]),
])
def test_foreign_language_classification(title, categories, entities):
    result = classifier.classify(title)
    assert list(result.categories) == categories
    assert list(result.entities) == entities


def test_spanish_como_is_not_the_club():
    assert "Комо" not in classifier.classify("Cómo ver el Barça - Real Madrid en TV").entities


@pytest.mark.parametrize("title", [
    "Bayern-Frauen gewinnen das Topspiel",
    "Inacio fehlt Italiens U 21 monatelang",
    "Juventus, la Primavera perde il derby",
    "El Barça de baloncesto gana en Euroliga",
    "Handball-Bundesliga: Kiel siegt",
])
def test_foreign_women_youth_and_other_sports_are_never_sent(title):
    assert classifier.classify(title).hard_excluded
