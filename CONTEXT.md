# Project Context - AI News Agent

## Goal

Tao mot workflow LangGraph tu dong tim cac tin tuc AI moi nhat, uu tien tin co tuong tac cao, quan trong va co anh huong. Workflow tong hop 3 bai noi bat, viet mot bai post Facebook bang model OpenAI gon nhe, gui noi dung qua Telegram de doi duyet, va co the publish len Facebook Page sau khi approve.

## Current implementation

Project da duoc scaffold tai workspace nay voi Python package `ai_news_agent`.

Main files:

- `src/ai_news_agent/workflow.py`: LangGraph workflow end-to-end.
- `src/ai_news_agent/news.py`: thu thap tin tu RSS, Hacker News, Tavily, NewsAPI; enrich metadata; ranking.
- `src/ai_news_agent/llm.py`: viet va revise post bang OpenAI-compatible Chat Completions. Mac dinh dung NVIDIA NIM `openai/gpt-oss-120b`.
- `src/ai_news_agent/telegram.py`: gui post den Telegram va poll approval.
- `src/ai_news_agent/facebook.py`: optional publish Facebook Page.
- `src/ai_news_agent/memory.py`: SQLite domain memory de chong trung bai va luu audit trail.
- `src/ai_news_agent/config.py`: cau hinh qua `.env`.
- `src/ai_news_agent/main.py`: CLI `run-once` va `daemon`.
- `docs/architecture.md`: giai thich kien truc agentic va memory.
- `.env.example`: mau bien moi truong.
- `tests/`: test scoring va parse approval.

## Workflow nodes

1. `load_memory`: doc cac post gan day.
2. `collect_news`: lay candidate tu RSS, Hacker News, Tavily, NewsAPI.
3. `enrich_articles`: lay description/image metadata neu co.
4. `rank_articles`: cham diem recency, engagement, relevance, novelty.
5. `select_ranked_articles`: loai URL da dang va chon so bai theo `POST_ARTICLE_COUNT`.
6. `draft_post`: model OpenAI-compatible viet bai phan tich Facebook tieng Viet, kem `image_url` neu co.
7. `check_duplicate_post`: so sanh ban nhap voi post history, bo qua neu qua giong.
8. `send_telegram`: gui ban nhap qua Telegram.
9. `wait_approval`: doi reply `APPROVE`, `REJECT:`, hoac `EDIT:`.
10. `revise_post`: neu `EDIT:`, tu sua toi da 2 vong.
11. `publish_facebook`: publish neu `FACEBOOK_ENABLED=true`.
12. `persist_memory`: luu article/post/feedback vao SQLite.

## Memory design

- LangGraph checkpoint: dung `InMemorySaver` trong code hien tai, gan voi `thread_id`.
- SQLite domain memory: `.data/ai_news_agent.sqlite3`, luu article fingerprint, post history, approval status, feedback, Facebook post id.
- Prompt memory: cac post gan day duoc dua vao prompt de tranh lap goc nhin.
- Duplicate guard: URL canonical da dang se bi loai truoc khi draft; draft qua giong 20 post gan nhat se duoc luu status `skipped_duplicate` va khong gui Telegram/Facebook.

## How to run

Can Python 3.11+.

```bash
pip install -e ".[dev]"
copy .env.example .env
```

Dien toi thieu:

- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_APPROVER_CHAT_ID`

Chay mot lan:

```bash
ai-news-agent run-once
```

Chay automation:

```bash
ai-news-agent daemon
```

Test:

```bash
pytest
ruff check .
```

## Current environment note

Trong shell hien tai, `python`, `py`, va `git` chua co trong PATH, nen chua verify duoc bang test runtime. Code va cau truc file da duoc tao day du.
