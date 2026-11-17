"""Static football catalogue: tournaments, clubs, star players and team-name translations.

Aliases use a tiny matching language (compiled in ``classifier.compile_alias``):

* ``"челси"``      — whole word, case-insensitive;
* ``"ливерпул*"``  — word stem: «Ливерпуль», «Ливерпуля», «Ливерпулем»…;
* ``"=АПЛ"``       — case-sensitive;
* ``"q:милан*"``   — only right after an opening quote: «Милан», "Милан";
* ``"re:…"``       — raw regular expression.

Spaces and hyphens inside literals match any run of spaces/hyphens, accents are ignored
on both sides (ё = е, й = и, é = e). Club lists follow the 2026/27 season — update them
when teams are promoted or relegated.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Entity:
    """A club, player or coach that can be mentioned in news."""

    name: str
    aliases: tuple[str, ...]
    espn: tuple[str, ...] = ()
    unless: tuple[str, ...] = ()
    tag: str | None = None


@dataclass(frozen=True, slots=True)
class Category:
    """A tournament (or pseudo-category such as «Звёзды») news can belong to."""

    key: str
    name: str
    short: str
    emoji: str
    tag: str | None
    keywords: tuple[str, ...] = ()
    entities: tuple[Entity, ...] = ()
    espn_code: str | None = None


def E(
    name: str,
    *aliases: str,
    espn: str | tuple[str, ...] = (),
    unless: tuple[str, ...] = (),
    tag: str | None = None,
) -> Entity:
    return Entity(
        name=name,
        aliases=aliases,
        espn=(espn,) if isinstance(espn, str) else espn,
        unless=unless,
        tag=tag,
    )


# --------------------------------------------------------------------------- leagues

EPL = Category(
    key="epl",
    name="АПЛ",
    short="АПЛ",
    emoji="🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    tag="АПЛ",
    espn_code="eng.1",
    keywords=(
        "=АПЛ",
        "английск* премьер-лиг*",
        "премьер-лиг* англи*",
        "чемпионат* англии",
        r"re:(?<!indian\s)(?<!caribbean\s)premier[\s\-]+league(?![\s\-]+darts)",
        "=EPL",
    ),
    entities=(
        E("Арсенал", "q:арсенал*", "лондонск* арсенал*", "канонир*", "arsenal",
          espn="Arsenal", unless=("тульск*", r"re:арсенал\w*\W{0,3}тул")),
        E("Астон Вилла", "астон вилл*", "aston villa", espn="Aston Villa"),
        E("Борнмут", "борнмут*", "bournemouth", espn="AFC Bournemouth"),
        E("Брентфорд", "брентфорд*", "brentford", espn="Brentford"),
        E("Брайтон", "брайтон*", "brighton", espn="Brighton & Hove Albion"),
        E("Челси", "челси", "chelsea", espn="Chelsea"),
        E("Ковентри", "ковентри", "coventry", espn="Coventry City"),
        E("Кристал Пэлас", "кристал пэлас", "кристал пэлэс", "crystal palace", espn="Crystal Palace"),
        E("Эвертон", "эвертон*", "everton", espn="Everton"),
        E("Фулхэм", "фулхэм*", "фулхем*", "fulham", espn="Fulham"),
        E("Халл Сити", "халл сити", "q:халл*", "hull city", espn="Hull City"),
        E("Ипсвич", "ипсвич*", "ipswich", espn="Ipswich Town"),
        E("Лидс", "лидс*", "leeds", espn="Leeds United"),
        E("Ливерпуль", "ливерпул*", "liverpool", espn="Liverpool"),
        E("Манчестер Сити", "манчестер сити", "ман сити", "manchester city", "man city",
          espn="Manchester City"),
        E("Манчестер Юнайтед", "манчестер юнайтед", "ман юнайтед", "=МЮ",
          "manchester united", "man utd", "man united", espn="Manchester United", tag="МЮ"),
        E("Ньюкасл", "ньюкасл*", "newcastle", espn="Newcastle United"),
        E("Ноттингем Форест", "ноттингем*", "nottingham forest", espn="Nottingham Forest"),
        E("Сандерленд", "сандерленд*", "sunderland", espn="Sunderland"),
        E("Тоттенхэм", "тоттенхэм*", "тоттенхем*", "tottenham", "=Spurs",
          espn="Tottenham Hotspur"),
    ),
)

LALIGA = Category(
    key="laliga",
    name="Ла Лига",
    short="Ла Лига",
    emoji="🇪🇸",
    tag="ЛаЛига",
    espn_code="esp.1",
    keywords=("ла лиг*", "laliga", "la liga", "чемпионат* испании"),
    entities=(
        E("Алавес", "алавес*", "alaves", espn="Alavés"),
        E("Атлетик", r"re:атлетик(а|у|ом|е)?(?!\w)", "бильбао", "athletic club",
          "athletic bilbao", "bilbao", espn="Athletic Club"),
        E("Атлетико", "атлетико", "матрасник*", "atletico", "atleti",
          espn="Atlético Madrid", unless=("минейро", "mineiro", "паранаэнсе")),
        E("Барселона", "барселон*", r"re:барс(а|ы|е|у|ой)(?!\w)", "блаугран*", "barcelona", "barca",
          espn="Barcelona"),
        E("Сельта", "сельт*", "celta", espn="Celta Vigo"),
        E("Депортиво", "депортиво", "deportivo", espn="Deportivo"),
        E("Эльче", "эльче", "elche", espn="Elche"),
        E("Эспаньол", "эспаньол*", "espanyol", espn="Espanyol"),
        E("Хетафе", "хетафе", "getafe", espn="Getafe"),
        E("Леванте", "леванте", "levante", espn="Levante"),
        E("Малага", "малаг*", "malaga", espn="Málaga"),
        E("Осасуна", "осасун*", "osasuna", espn="Osasuna"),
        E("Расинг", "расинг* сантандер", "racing santander", "racing de santander",
          espn="Racing Santander"),
        E("Райо Вальекано", "райо", "вальекано", "rayo vallecano", "=Rayo", espn="Rayo Vallecano"),
        E("Бетис", "бетис*", "betis", espn="Real Betis"),
        E("Реал Мадрид", "реал* мадрид*", "мадридск* реал*",
          r"q:re:реал(а|у|ом|е)?(?!\w)(?![\s\-]+(сосьедад|бетис|овьедо|сарагос|вальядолид|мальорк|солт))",
          "сливочн*", "real madrid", "los blancos", espn="Real Madrid", tag="Реал"),
        E("Реал Сосьедад", "сосьедад*", "real sociedad", espn="Real Sociedad", tag="РеалСосьедад"),
        E("Севилья", "севиль*", "sevilla", "seville", espn="Sevilla"),
        E("Валенсия", "валенси*", "valencia", espn="Valencia"),
        E("Вильярреал", "вильярреал*", "вильяреал*", "villarreal", espn="Villarreal"),
    ),
)

BUNDESLIGA = Category(
    key="bundesliga",
    name="Бундеслига",
    short="Бундеслига",
    emoji="🇩🇪",
    tag="Бундеслига",
    espn_code="ger.1",
    keywords=("бундеслиг*", "чемпионат* германии", "bundesliga", "q:борусси*"),
    entities=(
        E("Аугсбург", "аугсбург*", "augsburg", espn="FC Augsburg"),
        E("Бавария", "бавари*", "байерн*", "bayern", espn="Bayern Munich"),
        E("Байер", r"re:байер(а|у|ом|е)?(?!\w)", "леверкузен*", "leverkusen", "=Bayer",
          espn="Bayer Leverkusen"),
        E("Боруссия Дортмунд", "дортмунд*", r"re:борусси\w*\W{0,3}\(?д\)?(?!\w)", "dortmund", "=BVB",
          espn="Borussia Dortmund", tag="БоруссияД"),
        E("Боруссия М", "менхенгладбах*", "гладбах*", r"re:борусси\w*\W{0,3}\(?м\)?(?!\w)",
          "gladbach", "monchengladbach", "moenchengladbach",
          espn="Borussia Mönchengladbach", tag="БоруссияМ"),
        E("Айнтрахт", "айнтрахт*", "eintracht", "frankfurt", espn="Eintracht Frankfurt"),
        E("Кёльн", "кельн*", "cologne", "koln", espn="FC Cologne"),
        E("Гамбург", "гамбург*", "hamburg", "=HSV", espn="Hamburg SV"),
        E("Майнц", "майнц*", "mainz", espn="Mainz"),
        E("Лейпциг", "лейпциг*", "leipzig", espn="RB Leipzig"),
        E("Фрайбург", "фрайбург*", "freiburg", espn="SC Freiburg"),
        E("Падерборн", "падерборн*", "paderborn", espn="SC Paderborn 07"),
        E("Эльферсберг", "эльферсберг*", "эльверсберг*", "elversberg", espn="SV Elversberg"),
        E("Шальке", "шальке", "schalke", espn="Schalke 04"),
        E("Хоффенхайм", "хоффенхайм*", "хоффенхейм*", "hoffenheim", espn="TSG Hoffenheim"),
        E("Штутгарт", "штутгарт*", "stuttgart", espn="VfB Stuttgart"),
        E("Унион Берлин", "унион* берлин",
          r"q:re:унион(а|у|ом|е)?(?!\w)(?![\s\-]+(сен|сент|saint|st)\b)",
          "union berlin", espn="1. FC Union Berlin", tag="Унион"),
        E("Вердер", "вердер*", "werder", "bremen", espn="Werder Bremen"),
    ),
)

SERIEA = Category(
    key="seriea",
    name="Серия А",
    short="Серия А",
    emoji="🇮🇹",
    tag="СерияА",
    espn_code="ita.1",
    keywords=(r"re:сери(я|и|ю|е|ей)[\s\-]+а(?!\w)", "чемпионат* итали*", "кальчо", "serie a"),
    entities=(
        E("Милан", "q:милан*", "россонер*", "ac milan", r"re:(?<!inter\s)milan(?!\w)", espn="AC Milan"),
        E("Интер", r"re:интер(а|у|ом|е)?(?!\w)(?![\s\-]+(майами|эскальдес|турку))", "нерадзурр*",
          "internazionale", r"re:inter(?!\w)(?![\s\-]+(miami|d.escaldes|turku))",
          espn="Internazionale"),
        E("Ювентус", "ювентус*", r"re:юве(?!\w)", "juventus", "juve", espn="Juventus"),
        E("Наполи", "наполи", "napoli", espn="Napoli"),
        E("Рома", r"q:re:ром(а|ы|е|у|ой)(?!\w)", "джаллоросс*", "as roma", "=Roma", espn="AS Roma"),
        E("Лацио", "лацио", "lazio", espn="Lazio"),
        E("Аталанта", "аталант*", "atalanta", espn="Atalanta"),
        E("Фиорентина", "фиорентин*", "fiorentina", espn="Fiorentina"),
        E("Болонья", "болонь*", "bologna", espn="Bologna"),
        E("Торино", "торино", "torino", espn="Torino"),
        E("Удинезе", "удинезе", "udinese", espn="Udinese"),
        E("Дженоа", "дженоа", "genoa", espn="Genoa"),
        E("Комо", "комо", "=Como", espn="Como"),
        E("Кальяри", "кальяри", "cagliari", espn="Cagliari"),
        E("Лечче", "лечче", "lecce", espn="Lecce"),
        E("Парма", r"re:парм(а|ы|е|у|ой)(?!\w)", "parma", espn="Parma"),
        E("Сассуоло", "сассуоло", "sassuolo", espn="Sassuolo"),
        E("Фрозиноне", "фрозиноне", "frosinone", espn="Frosinone"),
        E("Монца", r"re:монц(а|ы|е|у|ой)(?!\w)", "monza", espn="Monza"),
        E("Венеция", "венеци*", "venezia", espn="Venezia"),
    ),
)

LIGUE1 = Category(
    key="ligue1",
    name="Лига 1",
    short="Лига 1",
    emoji="🇫🇷",
    tag="Лига1",
    espn_code="fra.1",
    keywords=(r"re:лиг(а|и|у|е|ой)[\s\-]+1(?!\d)", "чемпионат* франции", "ligue 1"),
    entities=(
        E("ПСЖ", "=ПСЖ", "пари сен-жермен*", "сен-жермен*", "psg", "paris saint-germain",
          "paris st-germain", "paris sg", espn="Paris Saint-Germain"),
        E("Марсель", "q:марсел*", "олимпик марсел*", "marseille", espn="Marseille"),
        E("Монако", "монако", "monaco", espn="AS Monaco"),
        E("Лион", r"re:лион(а|у|ом|е)?(?!\w)", "lyon", espn="Lyon"),
        E("Лилль", "лилл*", "lille", espn="Lille"),
        E("Ницца", "ницц*", "ogc nice", "=Nice", espn="Nice"),
        E("Ренн", r"re:ренн(а|у|ом|е)?(?!\w)", "rennes", "stade rennais", espn="Stade Rennais"),
        E("Ланс", r"re:ланс(а|у|ом|е)?(?!\w)", "=Lens", "rc lens", espn="Lens"),
        E("Страсбур", "страсбур*", "strasbourg", espn="Strasbourg"),
        E("Тулуза", "тулуз*", "toulouse", espn="Toulouse"),
        E("Брест", "q:брест*", "brest", espn="Brest",
          unless=("динамо* брест*", r"re:динамо\W{0,3}\(?брест", "белорус*")),
        E("Осер", r"re:осер(а|у|ом|е)?(?!\w)", "auxerre", espn="AJ Auxerre"),
        E("Анже", "анже", "angers", espn="Angers"),
        E("Гавр", r"re:гавр(а|у|ом|е)?(?!\w)", "le havre", espn="Le Havre AC"),
        E("Ле-Ман", "ле-ман*", "le mans", espn="Le Mans", tag="ЛеМан"),
        E("Лорьян", "лорьян*", "lorient", espn="Lorient"),
        E("Пари ФК", "пари фк", "фк пари", "фк париж", "paris fc", espn="Paris FC", tag="ПариФК"),
        E("Труа", "труа", "troyes", espn="Troyes"),
    ),
)

# ------------------------------------------------------------------- UEFA tournaments

UCL = Category(
    key="ucl",
    name="Лига чемпионов",
    short="Лига чемпионов",
    emoji="🏆",
    tag="ЛЧ",
    espn_code="uefa.champions",
    keywords=(r"=re:Лиг(а|и|у|е|ой)[\s\-]+[Чч]емпионов", "=ЛЧ", "champions league", "=UCL"),
)

UEL = Category(
    key="uel",
    name="Лига Европы",
    short="Лига Европы",
    emoji="🟠",
    tag="ЛЕ",
    espn_code="uefa.europa",
    keywords=(r"=re:Лиг(а|и|у|е|ой)[\s\-]+Европы", "=ЛЕ", "europa league", "=UEL"),
)

UECL = Category(
    key="uecl",
    name="Лига конференций",
    short="Лига конференций",
    emoji="🟢",
    tag="ЛК",
    espn_code="uefa.europa.conf",
    keywords=(r"=re:Лиг(а|и|у|е|ой)[\s\-]+[Кк]онференций", "=ЛК", "conference league", "=UECL"),
)

UNL = Category(
    key="unl",
    name="Лига наций",
    short="Лига наций",
    emoji="🌍",
    tag="ЛигаНаций",
    espn_code="uefa.nations",
    keywords=(r"=re:Лиг(а|и|у|е|ой)[\s\-]+[Нн]аций", "nations league"),
)

# ------------------------------------------------------------- star players & coaches

STARS = Category(
    key="stars",
    name="Звёзды",
    short="Звёзды",
    emoji="⭐",
    tag=None,
    entities=(
        E("Мбаппе", "мбаппе", "mbappe"),
        E("Холанд", "холанд*", "холанн*", "haaland"),
        E("Беллингем", "беллингем*", "bellingham"),
        E("Винисиус", "винисиус*", "vinicius"),
        E("Ямаль", r"re:ямал(ь|я|ю|ем|е)(?!\w)", "yamal"),
        E("Салах", "салах*", "salah"),
        E("Кейн", r"re:кейн(а|у|ом|е)?(?!\w)", "harry kane", "=Kane"),
        E("Левандовски", "левандовск*", "lewandowski"),
        E("Педри", "педри", "pedri"),
        E("Рафинья", "рафинь*", "raphinha"),
        E("Родри", "родри", "rodri"),
        E("Де Брёйне", "де брейне", "де брюйне", "de bruyne", tag="ДеБрейне"),
        E("Мусиала", "мусиал*", "musiala"),
        E("Виртц", "виртц*", r"re:вирц(а|у|ем|е)?(?!\w)", "wirtz"),
        E("Сака", r"=re:Сак(а|и|е|у|ой)(?!\w)", "bukayo saka", "=Saka"),
        E("Палмер", "палмер*", "palmer"),
        E("Фоден", "фоден*", "foden"),
        E("Эдегор", "эдегор*", "эдегаард*", "одегор*", "одегаард*", "odegaard"),
        E("Ван Дейк", "ван дейк*", "van dijk", tag="ВанДейк"),
        E("Кварацхелия", "кварацхели*", "хвич*", "kvaratskhelia"),
        E("Лаутаро", "лаутаро", "lautaro"),
        E("Дембеле", "дембеле", "dembele"),
        E("Гюлер", "гюлер*", "guler"),
        E("Йылдыз", "йылдыз*", "yildiz"),
        E("Олисе", "олисе", "olise"),
        E("Исак", r"re:исак(а|у|ом|е)?(?!\w)", "alexander isak", "=Isak"),
        E("Дьёкереш", "дьекереш*", "декереш*", "гекереш*", "gyokeres"),
        E("Шешко", "шешко", "sesko"),
        E("Месси", "месси", "messi"),
        E("Роналду", "роналду", "криштиану", "ronaldo", "cristiano"),
        E("Неймар", "неймар*", "neymar"),
        E("Гвардиола", "гвардиол*", "guardiola"),
        E("Анчелотти", "анчелотти", "ancelotti"),
        E("Клопп", "клопп*", "klopp"),
        E("Слот", r"=re:Слот(а|у|ом|е)?(?!\w)", "arne slot", "=Slot"),
        E("Артета", "артет*", "arteta"),
        E("Моуринью", "моуринь*", "mourinho"),
        E("Флик", r"re:флик(а|у|ом|е)?(?!\w)", "hansi flick", "=Flick"),
        E("Хаби Алонсо", "хаби алонсо", r"re:алонсо(?!\w)", "xabi alonso", tag="ХабиАлонсо"),
        E("Луис Энрике", "энрике", "luis enrique", tag="ЛуисЭнрике"),
        E("Симеоне", "симеоне", "simeone"),
        E("Почеттино", "почеттино", "pochettino"),
        E("Аморим", "аморим*", "amorim"),
        E("Мареска", "мареск*", "maresca"),
        E("Конте", "=Конте", "=Conte"),
        E("Компани", "компани", "kompany"),
    ),
)

OTHER = Category(key="other", name="Прочее", short="Прочее", emoji="📰", tag=None)

CATEGORIES: tuple[Category, ...] = (
    EPL, LALIGA, BUNDESLIGA, SERIEA, LIGUE1, UCL, UEL, UECL, UNL, STARS,
)
CATEGORY_BY_KEY: dict[str, Category] = {c.key: c for c in (*CATEGORIES, OTHER)}
ALL_CATEGORY_KEYS: tuple[str, ...] = tuple(CATEGORY_BY_KEY)
DEFAULT_CATEGORY_KEYS: frozenset[str] = frozenset(c.key for c in CATEGORIES)
ENTITY_BY_NAME: dict[str, Entity] = {e.name: e for c in CATEGORIES for e in c.entities}

# News that match nothing above are sent only when «Прочее» is on — and never these:
EXCLUDE_ALIASES: tuple[str, ...] = (
    "=РПЛ", "=ФНЛ", "=РФС", "российск* премьер-лиг*", "мир рпл", "кубок* росси*",
    "сборн* росси*", "медиалиг*", "=МЛС", "=MLS", "саудовск*",
    "q:зенит*", "q:спартак*", "=ЦСКА", "q:локомотив*", "q:краснодар*", "q:динамо*",
    "q:ростов*", "q:рубин*", "q:ахмат*", "крыль* советов", "q:оренбург*", "q:факел*",
    "пари нн", "q:акрон*", "q:балтик*", "q:сочи", "q:урал*", "q:торпедо", "q:родин*",
    "q:химки", "q:енисе*", "q:шинник*", "q:камаз*",
    "scottish", "=EFL", "=Championship", "league one", "league two", "saudi",
)

# Never sent, whatever the settings (checked against the title only).
HARD_EXCLUDE_ALIASES: tuple[str, ...] = (
    "женск*", r"re:women'?s?(?!\w)", "=WSL", "=NWSL", "юношеск*", "молодежн*",
    "youth league", r"re:u-?(17|19|20|21|23)(?!\d)",
)

# Russian names for teams that ESPN reports in English (top-5 league clubs come from
# the entities above). Keys are compared accent- and case-insensitively.
EXTRA_TEAM_NAMES: dict[str, str] = {
    # UEFA club competitions
    "AEK Athens": "АЕК", "Bodo/Glimt": "Будё-Глимт", "Club Brugge": "Брюгге",
    "FC Porto": "Порту", "Fenerbahce": "Фенербахче", "Feyenoord Rotterdam": "Фейеноорд",
    "Galatasaray": "Галатасарай", "LASK Linz": "ЛАСК", "PSV Eindhoven": "ПСВ",
    "Sabah FK": "Сабах", "Shakhtar Donetsk": "Шахтёр", "Slavia Prague": "Славия",
    "Slovan Bratislava": "Слован", "Sporting CP": "Спортинг", "Viking FK": "Викинг",
    "AZ Alkmaar": "АЗ", "Anderlecht": "Андерлехт", "Ararat-Armenia": "Арарат-Армения",
    "Benfica": "Бенфика", "Besiktas": "Бешикташ", "Celtic": "Селтик",
    "Dinamo Zagreb": "Динамо Загреб", "Ferencvaros": "Ференцварош",
    "Hapoel Be'er": "Хапоэль Беэр-Шева", "Jagiellonia Bialystok": "Ягеллония",
    "Lech Poznan": "Лех", "Levski Sofia": "Левски", "Lillestrom": "Лиллестрём",
    "NEC Nijmegen": "НЕК", "NK Celje": "Целе", "OFI Crete": "ОФИ", "Olympiacos": "Олимпиакос",
    "Omonia Nicosia": "Омония", "RB Salzburg": "Зальцбург", "SK Sturm Graz": "Штурм",
    "Sparta Prague": "Спарта Прага", "Torreense": "Торренсе",
    "Union St.-Gilloise": "Юнион Сент-Жилуаз", "Viktoria Plzen": "Виктория Пльзень",
    "AGF": "Орхус", "Ajax Amsterdam": "Аякс", "Borac Banja Luka": "Борац", "Braga": "Брага",
    "CSKA Sofia": "ЦСКА София", "CSU Craiova": "Университатя Крайова", "Egnatia": "Эгнатия",
    "F.C. København": "Копенгаген", "FC Lugano": "Лугано", "FC Midtjylland": "Мидтьюлланн",
    "FC Nordsjælland": "Нордшелланн", "FC Thun": "Тун", "FC Twente": "Твенте",
    "Hajduk Split": "Хайдук", "Heart of Midlothian": "Хартс", "Iberia 1999": "Иберия 1999",
    "Inter D'Escaldes": "Интер Эскальдес", "Jablonec": "Яблонец", "KAA Gent": "Гент",
    "Kairat Almaty": "Кайрат", "Kauno Zalgiris": "Каунас Жальгирис", "KuPS Kuopio": "КуПС",
    "Lincoln Red Imps": "Линкольн Ред Импс", "Mjällby AIF": "Мьельбю", "Pafos": "Пафос",
    "Panathinaikos": "Панатинаикос", "Red Star Belgrade": "Црвена Звезда", "Riga FC": "Рига",
    "SK Brann": "Бранн", "Sint-Truidense": "Синт-Трейден", "Trabzonspor": "Трабзонспор",
    "Rangers": "Рейнджерс", "Young Boys": "Янг Бойз", "Qarabag": "Карабах",
    "Dynamo Kyiv": "Динамо Киев", "Red Bull Salzburg": "Зальцбург", "Copenhagen": "Копенгаген",
    # Recently relegated clubs that may still play in Europe
    "West Ham United": "Вест Хэм", "Wolverhampton Wanderers": "Вулверхэмптон",
    "Girona": "Жирона", "Mallorca": "Мальорка", "VfL Wolfsburg": "Вольфсбург",
    "Hellas Verona": "Верона", "Nantes": "Нант",
    # National teams (Nations League)
    "Albania": "Албания", "Andorra": "Андорра", "Armenia": "Армения", "Austria": "Австрия",
    "Azerbaijan": "Азербайджан", "Belarus": "Беларусь", "Belgium": "Бельгия",
    "Bosnia-Herzegovina": "Босния и Герцеговина", "Bulgaria": "Болгария",
    "Croatia": "Хорватия", "Cyprus": "Кипр", "Czechia": "Чехия", "Czech Republic": "Чехия",
    "Denmark": "Дания", "England": "Англия", "Estonia": "Эстония",
    "Faroe Islands": "Фарерские острова", "Finland": "Финляндия", "France": "Франция",
    "Georgia": "Грузия", "Germany": "Германия", "Gibraltar": "Гибралтар", "Greece": "Греция",
    "Hungary": "Венгрия", "Iceland": "Исландия", "Israel": "Израиль", "Italy": "Италия",
    "Kazakhstan": "Казахстан", "Kosovo": "Косово", "Latvia": "Латвия",
    "Liechtenstein": "Лихтенштейн", "Lithuania": "Литва", "Luxembourg": "Люксембург",
    "Malta": "Мальта", "Moldova": "Молдова", "Montenegro": "Черногория",
    "Netherlands": "Нидерланды", "North Macedonia": "Северная Македония",
    "Northern Ireland": "Северная Ирландия", "Norway": "Норвегия", "Poland": "Польша",
    "Portugal": "Португалия", "Republic of Ireland": "Ирландия", "Romania": "Румыния",
    "San Marino": "Сан-Марино", "Scotland": "Шотландия", "Serbia": "Сербия",
    "Slovakia": "Словакия", "Slovenia": "Словения", "Spain": "Испания", "Sweden": "Швеция",
    "Switzerland": "Швейцария", "Türkiye": "Турция", "Turkey": "Турция",
    "Ukraine": "Украина", "Wales": "Уэльс", "Russia": "Россия",
}
