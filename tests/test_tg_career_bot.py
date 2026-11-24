from ubermensch.tg_career_bot import (
    detect_language,
    extract_hr_email,
    extract_hr_username,
    parse_and_filter_vacancy,
    render_cover_letter,
    resume_paths,
)


def test_detect_language_ru_and_en():
    ru_text = (
        "Вакансия: Middle Python Developer (FastAPI)\n"
        "Компания: АльфаТех\n"
        "Обязанности: разработка микросервисов на Python, PostgreSQL, Redis, Celery.\n"
        "Формат: удалёнка.\n"
        "Контакты: @hr_anna_recruiter"
    )
    en_text = (
        "Title: Python Backend Engineer\n"
        "Company: FinCore EU\n"
        "We are looking for a Python engineer experienced with FastAPI, PostgreSQL, Redis, "
        "and asynchronous services to join our remote engineering team.\n"
        "Contact: @eu_tech_recruiter"
    )
    assert detect_language(ru_text) == "ru"
    assert detect_language(en_text) == "en"

    parsed_ru = parse_and_filter_vacancy(ru_text, channel_username="pydevjob")
    assert parsed_ru is not None
    assert parsed_ru.language == "ru"
    assert parsed_ru.position == "Middle Python Developer (FastAPI)"
    assert parsed_ru.company == "АльфаТех"
    assert parsed_ru.hr_username == "hr_anna_recruiter"

    parsed_en = parse_and_filter_vacancy(en_text, channel_username="remote_python_jobs")
    assert parsed_en is not None
    assert parsed_en.language == "en"
    assert parsed_en.position == "Python Backend Engineer"
    assert parsed_en.company == "FinCore EU"
    assert parsed_en.hr_username == "eu_tech_recruiter"


def test_filters_out_lead_and_ads():
    lead_post = (
        "Team Lead Python Developer\n"
        "Компания: Enfint\n"
        "Уровень: #lead\n"
        "Требования: Strong Python experience (5+ years), FastAPI, SQLAlchemy.\n"
        "Контакты: @Liz_Kostina"
    )
    senior_post = (
        "Senior Python Backend Developer\n"
        "Компания: FinTech\n"
        "Требования: Python, FastAPI, PostgreSQL, 5+ лет опыта.\n"
        "Контакты: @some_hr"
    )
    tutor_post = (
        "ENGLISH FOR DEVS & IT | 1-ON-1 | REMOTE\n"
        "Английский для Python и Backend разработчиков, подготовка к собеседованиям.\n"
        "Писать: @alexchddx"
    )
    crypto_post = (
        "Crypto / DeFi / Microstructure\n"
        "Ищем разработчика со знанием Python и asyncio в высокочастотный трейдинг.\n"
        "Контакты: @annanapreeva"
    )
    agency_title_post = (
        "Hireway\n"
        "Ищем разработчика Python, FastAPI, PostgreSQL на удалёнку.\n"
        "Контакт: @some_hr_person"
    )
    ad_post = (
        "Нужны 8 человек для работы с искусственным интеллектом Python\n"
        "Мы всему обучаем с нуля, жмите для бесплатной регистрации\n"
        "#реклама @some_manager"
    )
    assert parse_and_filter_vacancy(lead_post) is None
    assert parse_and_filter_vacancy(senior_post) is None
    assert parse_and_filter_vacancy(tutor_post) is None
    assert parse_and_filter_vacancy(crypto_post) is None
    assert parse_and_filter_vacancy(agency_title_post) is None
    assert parse_and_filter_vacancy(ad_post) is None


def test_extract_hr_ignores_channel_and_bots():
    text = (
        "Python Backend-разработчик (FastAPI / PostgreSQL)\n"
        "Удалёнка, полная занятость, стек: Python, FastAPI, Redis, Docker.\n"
        "Откликнуться: @ivanova_hr или через бота @helper_job_bot\n"
        "Канал: @pydevjob"
    )
    assert extract_hr_username(text, channel_username="pydevjob") == "ivanova_hr"


def test_render_cover_letter_picks_language_and_substitutes():
    templates = {
        "ru": [{"id": "ru1", "text": "Здравствуйте! Интересна вакансия «{position}» в {company}."}],
        "en": [{"id": "en1", "text": "Hello! I'm interested in the {position} role at {company}."}],
    }
    ru_vac = parse_and_filter_vacancy(
        "Позиция: Python-разработчик\nКомпания: Ромашка\n"
        "Разработка сервисов на FastAPI, PostgreSQL, Redis, Docker, pytest.\nКонтакт: @hr_romashka"
    )
    assert ru_vac is not None
    tid, text = render_cover_letter(ru_vac, templates, "seed1")
    assert tid == "ru1"
    assert "«Python-разработчик» в Ромашка" in text

    en_vac = parse_and_filter_vacancy(
        "Role: Python Backend Developer\n"
        "Building backend microservices with FastAPI, PostgreSQL, Redis and Docker in an international team.\n"
        "Apply to @hr_global"
    )
    assert en_vac is not None
    tid_en, text_en = render_cover_letter(en_vac, templates, "seed2")
    assert tid_en == "en1"
    assert "Python Backend Developer role" in text_en
    assert "{company}" not in text_en


def test_resume_paths_exist():
    paths = resume_paths()
    assert paths["ru"].suffix == ".pdf"
    assert paths["en"].suffix == ".pdf"


import unittest


def test_extract_hr_email_and_middle_senior_combo():
    email_post = (
        "Вакансия: Python Developer Middle+/Senior\n"
        "Компания: Синьор Софт\n"
        "Стек: Python, FastAPI, PostgreSQL, Redis, Docker, asyncio.\n"
        "Резюме отправлять на hr@seniorsoft-demo.ru\n"
        "Не писать на user@example.com"
    )
    assert extract_hr_email(email_post) == "hr@seniorsoft-demo.ru"
    parsed = parse_and_filter_vacancy(email_post)
    assert parsed is not None
    assert parsed.hr_username == ""
    assert parsed.hr_email == "hr@seniorsoft-demo.ru"
    assert parsed.position == "Python Developer Middle+/Senior"


class TGCareerBotTests(unittest.TestCase):
    def test_all(self):
        test_detect_language_ru_and_en()
        test_filters_out_lead_and_ads()
        test_extract_hr_ignores_channel_and_bots()
        test_render_cover_letter_picks_language_and_substitutes()
        test_resume_paths_exist()
        test_extract_hr_email_and_middle_senior_combo()


