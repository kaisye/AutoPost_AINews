# Agentic Architecture

## Design principles

- Mỗi node LangGraph có một trách nhiệm rõ ràng và idempotent nhất có thể.
- LLM chỉ làm việc cần reasoning/editorial judgment; tìm kiếm, ranking, memory và approval dùng code quyết định.
- Human approval là governance gate bắt buộc trước khi publish.
- Memory có schema rõ ràng, dùng được cho audit và chống trùng lặp nội dung.

## Graph nodes

| Node | Vai trò |
| --- | --- |
| `load_memory` | Lấy các post gần nhất để tránh lặp giọng và góc nhìn. |
| `collect_news` | Thu thập ứng viên từ RSS, HN, Tavily, NewsAPI. |
| `enrich_articles` | Lấy metadata bổ sung như description và image. |
| `rank_articles` | Tính điểm dựa trên recency, engagement, relevance, novelty. |
| `select_top3` | Chọn đúng 3 bài có điểm cao nhất. |
| `draft_post` | GPT-5.5 viết bài Facebook bằng tiếng Việt. |
| `send_telegram` | Gửi bản nháp cho người duyệt. |
| `wait_approval` | Poll Telegram cho APPROVE, REJECT hoặc EDIT. |
| `revise_post` | Tự sửa theo feedback, tối đa 2 vòng. |
| `publish_facebook` | Publish Facebook Page nếu được bật. |
| `persist_memory` | Ghi memory sau mỗi run để audit và tránh trùng. |

## Scoring

Điểm cuối cùng:

```text
final_score = 0.34 * recency + 0.30 * engagement + 0.24 * relevance + 0.12 * novelty
```

- `recency`: ưu tiên bài mới trong `NEWS_LOOKBACK_HOURS`.
- `engagement`: lấy tín hiệu points/comments từ HN, search score từ Tavily, popularity rank từ NewsAPI.
- `relevance`: đếm keyword liên quan AI trong title/summary.
- `novelty`: giảm điểm các URL đã thấy trong SQLite memory.

## Production notes

- Chạy bằng systemd, Docker, GitHub Actions self-hosted runner, hoặc Windows Task Scheduler đều được.
- Nên tách bot Telegram chỉ cho approver/team nội bộ.
- Nếu publish Facebook, dùng Page Access Token có scope tối thiểu cần thiết và rotate định kỳ.
- Nên backup `.data/ai_news_agent.sqlite3` vì đó là audit trail của automation.
