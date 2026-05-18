# Agentic AI Portfolio Report - AI News Agent

## 1. Executive summary

Dự án `AI News Agent` là một hệ thống Agentic AI tự động tìm kiếm tin tức mới nhất về AI, đánh giá mức độ quan trọng, viết bài phân tích chuyên sâu cho Facebook, gửi qua Telegram để người dùng duyệt, và có thể tự động đăng lên Facebook Page sau khi được chấp thuận.

Dự án thể hiện các năng lực quan trọng của một sản phẩm Agentic AI hiện đại:

- Multi-step workflow với LangGraph.
- LLM reasoning để viết và sửa nội dung.
- Tool integration với RSS, Hacker News, Tavily, NewsAPI, Telegram, Facebook.
- Human-in-the-loop approval.
- Memory để tránh trùng lặp và tạo audit trail.
- UI quản trị để cấu hình automation.
- Scheduler để chạy tự động theo giờ.
- Test, lint, config management và separation of concerns.

Giá trị portfolio: đây không chỉ là demo gọi API LLM, mà là một automation agent có quy trình, memory, guardrails, approval gate và khả năng vận hành gần với nhu cầu thực tế của doanh nghiệp.

## 2. Problem statement

Bài toán cần giải quyết:

- Mỗi ngày có quá nhiều tin AI mới.
- Người làm content cần lọc tin nhanh, chọn tin có ảnh hưởng, viết phân tích có chiều sâu.
- Nếu làm thủ công sẽ tốn thời gian, dễ lặp lại chủ đề, dễ bỏ sót tin quan trọng.
- Nếu tự động hoàn toàn không có kiểm soát thì có rủi ro đăng sai, trùng lặp, hoặc chưa đúng giọng thương hiệu.

Mục tiêu hệ thống:

- Thu thập tin AI mới nhất từ nhiều nguồn.
- Chấm điểm theo recency, engagement, relevance, novelty.
- Chọn bài tốt nhất hoặc nhiều bài sau khi rank.
- Viết bài Facebook bằng tiếng Việt theo phong cách phân tích chuyên nghiệp.
- Gửi draft qua Telegram để duyệt.
- Tự động approve nếu quá timeout theo cấu hình.
- Đăng Facebook nếu được bật.
- Ghi memory để tránh trùng nội dung và phục vụ audit.

## 3. Kiến thức nền tảng cần nắm

### 3.1 LLM và OpenAI-compatible API

LLM là mô hình ngôn ngữ lớn có khả năng đọc, tổng hợp, suy luận và sinh nội dung. Trong dự án này, LLM không làm tất cả mọi việc. LLM chỉ đảm nhận những phần cần editorial judgment:

- Viết bài post.
- Tổng hợp insight.
- Điều chỉnh nội dung theo feedback.
- Tạo cấu trúc hook, body, hashtag, source.

Project hiện dùng NVIDIA NIM OpenAI-compatible API:

- Provider: `nvidia`
- Base URL: `https://integrate.api.nvidia.com/v1`
- Model: `openai/gpt-oss-120b`

Điểm quan trọng: `openai/gpt-oss-120b` là text model, không tạo ảnh trực tiếp. Ảnh minh họa hiện lấy từ metadata bài viết gốc, ví dụ `og:image`.

### 3.2 Agentic AI

Agentic AI khác chatbot thông thường ở chỗ nó có:

- Goal: mục tiêu rõ ràng.
- State: trạng thái workflow.
- Tools: gọi API, đọc memory, publish nội dung.
- Planning or routing: đi qua các node tùy điều kiện.
- Memory: nhớ dữ liệu trong quá khứ.
- Governance: có approval gate và audit.
- Autonomy: có thể chạy theo lịch, không cần người gọi từng bước.

Trong project này, agent không được thiết kế như một "LLM tự do làm mọi thứ". Thay vào đó, hệ thống dùng workflow có kiểm soát:

- Code thu thập và chấm điểm tin.
- LLM viết nội dung.
- Telegram làm approval gate.
- Memory kiểm tra trùng lặp.
- Facebook chỉ publish khi điều kiện hợp lệ.

Đây là kiến trúc phù hợp cho môi trường công ty vì dễ debug, audit và mở rộng.

### 3.3 LangGraph

LangGraph là framework xây dựng workflow có state cho agent. Mỗi node là một bước xử lý. Các edge định nghĩa thứ tự hoặc điều kiện chuyển bước.

Trong dự án:

- `StateGraph` quản lý luồng xử lý.
- `AgentState` lưu run id, candidates, ranked articles, selected articles, draft, approval, revision count.
- Conditional edges xử lý các nhánh như duplicate, approved, rejected, edit request.
- `InMemorySaver` đang dùng làm checkpoint trong code hiện tại.

LangGraph phù hợp vì:

- Workflow có nhiều bước rõ ràng.
- Cần branch logic.
- Cần lặp revise theo feedback.
- Cần memory và audit.
- Cần dễ mở rộng thêm node sau này.

### 3.4 Memory trong Agentic AI

Memory trong project có 3 lớp:

- LangGraph checkpoint: lưu trạng thái execution theo `thread_id`.
- SQLite domain memory: lưu article fingerprint, post history, approval status, feedback, Facebook post id.
- Prompt memory: đưa các post gần đây vào prompt để giảm lặp lại góc nhìn và cách viết.

Memory không chỉ để "nhớ". Memory còn dùng để:

- Chấm điểm novelty.
- Tránh đăng lại bài cũ.
- Ghi audit trail.
- Phân tích lịch sử vận hành.
- Cải thiện nội dung theo feedback cũ.

### 3.5 Human-in-the-loop

Human-in-the-loop là cơ chế để con người kiểm soát quyết định quan trọng. Trong automation content, đây là bước rất cần thiết vì:

- LLM có thể viết sai.
- Tin tức có thể nhạy cảm.
- Nội dung cần đúng brand voice.
- Publish lên social media có ảnh hưởng công khai.

Trong project:

- Agent gửi draft qua Telegram.
- Approver reply `APPROVE`, `REJECT: lý do`, hoặc `EDIT: yêu cầu`.
- Nếu `EDIT`, agent revise tối đa 2 vòng.
- Nếu timeout, có thể auto approve tùy cấu hình.

## 4. Kiến trúc hệ thống

### 4.1 High-level architecture

```text
News Sources
  -> Collector
  -> Metadata Enrichment
  -> Ranking
  -> Article Selection
  -> LLM Drafting
  -> Duplicate Guard
  -> Telegram Approval
  -> Optional Revision
  -> Optional Facebook Publishing
  -> SQLite Memory
```

### 4.2 LangGraph nodes

| Node | Nhiệm vụ |
| --- | --- |
| `load_memory` | Lấy post gần đây để đưa vào prompt và tránh lặp lại |
| `collect_news` | Lấy candidate từ RSS, Hacker News, Tavily, NewsAPI |
| `enrich_articles` | Lấy metadata, description, image URL |
| `rank_articles` | Chấm điểm recency, engagement, relevance, novelty |
| `select_ranked_articles` | Loại URL đã đăng và chọn số bài theo `POST_ARTICLE_COUNT` |
| `draft_post` | Viết bài Facebook bằng LLM |
| `check_duplicate_post` | Kiểm tra nội dung có quá giống post cũ không |
| `send_telegram` | Gửi bản nháp cho người duyệt |
| `wait_approval` | Chờ reply Telegram hoặc timeout |
| `revise_post` | Sửa nội dung theo feedback |
| `publish_facebook` | Đăng lên Facebook Page nếu được bật |
| `persist_memory` | Lưu kết quả vào SQLite |

### 4.3 Design principles

- Mỗi node có một trách nhiệm riêng.
- LLM chỉ dùng cho bước cần ngôn ngữ và suy luận.
- API side effect như Telegram/Facebook được tách module.
- Config đọc từ `.env`.
- Memory là thành phần bắt buộc, không phải tính năng phụ.
- Publish cần có approval hoặc auto approval rõ ràng.
- Mỗi run cần có audit trail.

## 5. Công nghệ sử dụng

### 5.1 Backend và workflow

- Python: ngôn ngữ chính.
- LangGraph: orchestration agent workflow.
- Pydantic Settings: quản lý config từ `.env`.
- SQLite: domain memory và audit trail.
- APScheduler: chạy lịch đăng bài.
- FastAPI/Uvicorn: UI admin local.
- HTTPX: gọi HTTP API.
- BeautifulSoup: parse metadata bài viết.
- Pytest: test.
- Ruff: lint.

### 5.2 AI model

- NVIDIA NIM OpenAI-compatible API.
- Model text: `openai/gpt-oss-120b`.
- Có thể chuyển qua OpenAI native bằng `LLM_PROVIDER=openai`.

### 5.3 External services

- Telegram Bot API: gửi draft và nhận approval.
- Facebook Graph API: publish lên Page.
- RSS feeds: nguồn tin cơ bản.
- Hacker News API: tin có engagement signal.
- Tavily Search API: optional search web.
- NewsAPI: optional news provider.

### 5.4 UI

UI local cho người dùng:

- Run workflow thủ công.
- Set giờ đăng bài.
- Chọn số lượng bài sau khi rank.
- Cấu hình provider/model/base URL.
- Cấu hình timeout Telegram và auto approve.
- Bật/tắt Facebook publishing.
- Chọn light/dark mode và theme color.
- Xem run status và lịch sử post.

## 6. Ranking và content intelligence

### 6.1 Scoring

Hệ thống chấm điểm bài viết theo công thức:

```text
final_score = 0.34 * recency + 0.30 * engagement + 0.24 * relevance + 0.12 * novelty
```

Ý nghĩa:

- `recency`: bài mới được ưu tiên.
- `engagement`: bài có nhiều point/comment hoặc tín hiệu quan tâm được ưu tiên.
- `relevance`: bài liên quan AI, agent, model, OpenAI, NVIDIA, regulation, enterprise AI.
- `novelty`: bài mới với memory được ưu tiên hơn bài đã gặp.

### 6.2 Selection

Ban đầu hệ thống tổng hợp nhiều bài. Sau đó đã cải tiến để:

- Chọn 1 bài tốt nhất để viết phân tích chuyên sâu.
- Hoặc chọn nhiều bài sau ranking theo option `POST_ARTICLE_COUNT`.

Hướng thiết kế tốt cho content agent:

- Nếu mục tiêu là thought leadership: chọn 1 bài và phân tích sâu.
- Nếu mục tiêu là newsletter: chọn 3-5 bài và tổng hợp.
- Nếu mục tiêu là social quick update: chọn 1 tin có impact cao, viết ngắn gọn.

## 7. Duplicate prevention

Đây là phần rất quan trọng nếu muốn automation chạy lâu dài.

### 7.1 Vấn đề

Nếu không có memory, agent có thể:

- Đăng lại cùng một bài từ URL khác nhau.
- Viết lại cùng một góc nhìn.
- Lặp lại format quá nhiều lần.
- Làm giảm chất lượng Page và niềm tin người đọc.

### 7.2 Giải pháp đã có

Hệ thống chống trùng lặp theo 3 lớp:

- URL canonical: bỏ query tracking, slash cuối, lower-case URL. Ví dụ `?utm_source=...` không làm hệ thống tưởng đó là bài mới.
- Posted URL memory: bài đã từng nằm trong post history sẽ bị loại trước khi draft.
- Content similarity: draft mới được so với 20 post gần nhất bằng token overlap. Nếu quá giống, status là `skipped_duplicate`, không gửi Telegram và không publish.

### 7.3 Điểm có thể nâng cấp

- Dùng embedding similarity thay vì token overlap.
- Lưu topic fingerprint riêng, ví dụ `company + product + event`.
- Giảm điểm các chủ đề mới vừa đăng trong 7 ngày.
- Thêm UI hiện lý do skip duplicate.
- Tạo "angle generator" để cùng một tin nhưng chọn góc nhìn mới nếu cần.

## 8. Telegram approval workflow

### 8.1 Luồng approval

```text
Draft created
  -> Send Telegram
  -> Wait for reply
  -> APPROVE: publish
  -> REJECT: store feedback, stop
  -> EDIT: revise and resend
  -> timeout: auto approve if configured
```

### 8.2 Lệnh Telegram

- `APPROVE`: chấp thuận.
- `REJECT: lý do`: từ chối và lưu feedback.
- `EDIT: yêu cầu`: yêu cầu agent sửa lại.

### 8.3 Auto approve

Hệ thống có biến:

```env
TELEGRAM_APPROVAL_TIMEOUT_MINUTES=2
TELEGRAM_AUTO_APPROVE_ON_TIMEOUT=true
```

Nếu quá timeout và auto approve bật, agent coi như approved. Chức năng này phù hợp khi người dùng muốn automation fully, nhưng vẫn cần xem xét rủi ro nếu Page có tính nhạy cảm cao.

## 9. Facebook publishing

### 9.1 Cách hoạt động

Nếu `FACEBOOK_ENABLED=true`, sau approval hệ thống gọi Facebook Graph API:

- Có `image_url`: publish photo với caption.
- Không có `image_url`: publish feed post text.

### 9.2 Biến cấu hình

```env
FACEBOOK_ENABLED=true
FACEBOOK_PAGE_ID=
FACEBOOK_PAGE_ACCESS_TOKEN=
```

### 9.3 Lưu ý sản xuất

- Page Access Token cần quyền đúng.
- Token cần được bảo mật và rotate định kỳ.
- Nên có log và retry cho lỗi tạm thời.
- Nên tách staging Page và production Page.

## 10. Admin UI và automation

UI giúp dự án chuyển từ script thành sản phẩm có khả năng vận hành.

Tính năng đã có:

- Dashboard local tại `http://127.0.0.1:8787`.
- Run now.
- Schedule daily posting time.
- Embedded scheduler bằng APScheduler.
- Cấu hình LLM, news, Telegram, Facebook.
- Theme light/dark và theme color.
- Recent post memory.

Lưu ý quan trọng: set giờ trong UI chỉ có tác dụng khi process UI hoặc daemon đang chạy. Nếu đóng terminal/process, scheduler không chạy. Muốn production nên dùng:

- Windows Task Scheduler.
- systemd service.
- Docker container.
- Cloud VM.
- GitHub Actions self-hosted runner.

## 11. Khó khăn gặp phải và cách xử lý

### 11.1 Config và secret

Khó khăn:

- Thiếu `NVIDIA_API_KEY`.
- Nhập key vào `.env.example` có nguy cơ leak secret.
- Provider OpenAI và NVIDIA có cấu hình khác nhau.

Cách xử lý:

- Validate config bằng Pydantic.
- `.env.example` chỉ dùng placeholder.
- Tách `LLM_PROVIDER`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `NVIDIA_API_KEY`.

### 11.2 Model compatibility

Khó khăn:

- NVIDIA NIM dùng OpenAI-compatible API nhưng response streaming có thể có chunk không có choices.
- Một số model có reasoning content riêng.

Cách xử lý:

- Code client xử lý OpenAI-compatible Chat Completions.
- Bỏ qua chunk không có choices khi cần.
- Có retry và explain error.

### 11.3 Scheduler

Khó khăn:

- User set giờ đăng bài nhưng automation không chạy nếu không có process nền.

Cách xử lý:

- Thêm scheduler vào UI.
- Hiện `Next run`.
- Giải thích rõ cần để UI/daemon chạy liên tục.

### 11.4 Duplicate content

Khó khăn:

- News provider có thể trả về cùng một bài với URL khác.
- LLM có thể viết giống post cũ.

Cách xử lý:

- Canonical URL.
- Posted URL memory.
- Similarity guard.
- Status `skipped_duplicate`.

### 11.5 Governance

Khó khăn:

- Fully automation có rủi ro publish nội dung chưa được kiểm chứng.

Cách xử lý:

- Telegram approval.
- Reject/edit feedback.
- Auto approve có config timeout.
- Audit trail trong SQLite.

### 11.6 Ảnh minh họa

Khó khăn:

- `gpt-oss-120b` không tạo ảnh.
- Metadata bài viết có thể không có image.

Cách xử lý hiện tại:

- Lấy image từ metadata bài gốc.

Cải tiến:

- Thêm node `generate_image_prompt`.
- Tích hợp image generation provider riêng.
- Lưu image URL/file vào memory.
- Gửi preview ảnh qua Telegram trước khi publish.

## 12. Testing và quality assurance

Đã có test cho:

- LLM JSON parsing/error behavior.
- Memory duplicate.
- Scoring.
- Telegram approval parsing.
- Telegram auto timeout.

Lệnh:

```powershell
pytest
ruff check .
```

Chất lượng nên tiếp tục nâng cấp:

- Integration test với fake Telegram/Facebook.
- Test scheduler cron conversion.
- Test UI form save settings.
- Test duplicate route trong workflow.
- Contract test cho LLM output schema.

## 13. Bảo mật và compliance

Những điểm cần thể hiện trong portfolio:

- Secret không commit vào repo.
- `.env.example` chỉ có placeholder.
- Telegram bot chỉ nên dùng cho approver nội bộ.
- Facebook token cần scope tối thiểu.
- Cần log nhưng không log secret.
- Cần có approval gate trước khi publish nội dung công khai.
- Cần lưu audit trail: ai approve, nội dung nào, lúc nào, publish id nào.

Nâng cấp để đạt chuẩn công ty:

- Secret manager thay cho `.env` trong production.
- Role-based access cho UI.
- Signed webhook thay cho polling nếu deploy public.
- Rate limit và retry policy.
- Observability: structured logs, metrics, alerts.
- Data retention policy cho memory.

## 14. Các cải tiến nên làm tiếp

### 14.1 Kiến trúc

- Đổi `InMemorySaver` sang persistent checkpoint.
- Thêm queue cho run background.
- Tách worker và UI process.
- Thêm retry/backoff cho API external.
- Thêm `dry-run` mode và staging mode.

### 14.2 AI quality

- Thêm evaluator node để chấm draft trước khi gửi Telegram.
- Thêm fact-check node kiểm tra claim quan trọng.
- Thêm brand voice memory.
- Thêm content policy guardrails.
- Thêm embedding memory để so sánh topic/semantic duplicate.

### 14.3 Product

- UI hiện top ranked articles trước khi chọn.
- Cho user override selected article.
- Cho user edit post trực tiếp trên UI.
- Lịch đăng nhiều khung giờ.
- Calendar content plan.
- Analytics: post nào approved/rejected/duplicate, topic nào hiệu quả.

### 14.4 Deployment

- Dockerfile và docker-compose.
- Health check endpoint.
- Windows service/systemd config.
- Cloud deploy guide.
- Backup SQLite.
- CI pipeline chạy test/lint.

### 14.5 Image generation

Nếu muốn AI tạo ảnh minh họa:

- Thêm provider riêng vì `gpt-oss-120b` không tạo ảnh.
- Tạo node:

```text
draft_post
  -> generate_image_prompt
  -> generate_image
  -> send_telegram_preview
  -> publish_facebook
```

Provider có thể dùng:

- OpenAI Images API.
- Stability AI.
- Replicate/Fal/Together với Flux.
- NVIDIA image model nếu có endpoint phù hợp.

## 15. Cách trình bày trong portfolio

### 15.1 Tên dự án

AI News Agent - Agentic Workflow for Social Media Intelligence and Publishing

### 15.2 One-liner

Built an end-to-end Agentic AI system that discovers high-impact AI news, ranks articles, writes Vietnamese Facebook analysis posts, routes drafts through Telegram approval, prevents duplicate publishing, and optionally posts to Facebook automatically.

### 15.3 Key highlights

- LangGraph multi-node agent workflow.
- Human-in-the-loop approval via Telegram.
- Memory-driven duplicate prevention.
- Configurable LLM provider with NVIDIA NIM/OpenAI-compatible API.
- Automated scheduling with admin UI.
- Facebook Graph API publishing.
- SQLite audit trail.
- Test coverage for core logic.

### 15.4 Resume bullet examples

- Designed and implemented a LangGraph-based Agentic AI workflow for automated AI news discovery, ranking, drafting, approval, and publishing.
- Integrated NVIDIA NIM OpenAI-compatible LLMs with Telegram and Facebook APIs to create a human-governed social publishing agent.
- Built persistent SQLite memory for article fingerprinting, post history, duplicate prevention, and auditability.
- Developed a FastAPI admin dashboard for scheduling, provider configuration, theme controls, manual runs, and post history monitoring.
- Added automated approval timeout logic, content revision loop, and duplicate guardrails for production-oriented reliability.

### 15.5 Demo script

1. Mở UI tại `http://127.0.0.1:8787`.
2. Show config: model, post count, approval timeout, Facebook enable.
3. Bấm Run Now.
4. Giải thích workflow đang collect, rank, draft.
5. Show Telegram message.
6. Reply `EDIT:` để demo revision hoặc `APPROVE` để demo publish.
7. Show recent memory và duplicate prevention.
8. Giải thích nếu chạy lại cùng tin, agent sẽ skip duplicate.

### 15.6 GitHub README nên có

- Problem.
- Architecture diagram.
- Tech stack.
- Setup.
- Environment variables.
- Running modes: UI, run-once, daemon.
- Telegram approval flow.
- Duplicate prevention.
- Production roadmap.
- Screenshots/GIF demo.

## 16. Những điều cần nói khi phỏng vấn

Nếu được hỏi "tại sao dùng LangGraph?":

- Vì workflow có state, branching, approval, revision loop và memory. LangGraph giúp mỗi bước rõ ràng, debug được, mở rộng được.

Nếu được hỏi "agent này khác script automation ở đâu?":

- Nó có reasoning node bằng LLM, memory, tool use, conditional routing, approval gate, scheduler và audit trail. Script thông thường chỉ thực hiện tuần tự.

Nếu được hỏi "làm sao tránh hallucination?":

- Đưa source vào prompt, yêu cầu trích nguồn, dùng human approval, có thể nâng cấp bằng fact-check node và source verification.

Nếu được hỏi "làm sao scale?":

- Tách UI và worker, dùng queue, persistent checkpoint, database production, retry/backoff, observability và secret manager.

Nếu được hỏi "rủi ro lớn nhất là gì?":

- Publish nội dung sai hoặc trùng lặp. Dự án xử lý bằng approval, memory, duplicate guard và audit trail. Cần nâng cấp fact-check và policy guard.

## 17. Roadmap để thành sản phẩm chuẩn công ty

Phase 1 - Stable local automation:

- UI, scheduler, Telegram approval, Facebook publish.
- Duplicate guard.
- Tests core logic.

Phase 2 - Production hardening:

- Docker deploy.
- Persistent checkpoint.
- Queue worker.
- Structured logging.
- Health checks.
- Secret manager.

Phase 3 - Content intelligence:

- Embedding memory.
- Fact-check node.
- Brand voice profile.
- Quality evaluator.
- Topic diversification.

Phase 4 - Multi-channel publishing:

- LinkedIn, X, Threads.
- Post format per channel.
- Image generation.
- Calendar planner.
- Analytics feedback loop.

## 18. Kết luận

Dự án này là một portfolio tốt về Agentic AI vì nó thể hiện đầy đủ vòng đời của một agent thực tế:

- Thu thập thông tin.
- Đánh giá và ra quyết định.
- Dùng LLM để tạo output có giá trị.
- Gọi công cụ bên ngoài.
- Có con người phê duyệt.
- Có memory và audit.
- Có automation theo lịch.
- Có UI để vận hành.
- Có guardrails để giảm rủi ro.

Thông điệp chính khi trình bày: "Tôi không chỉ build chatbot. Tôi build một agent workflow có khả năng vận hành, kiểm soát, ghi nhớ, tự động hóa và mở rộng theo chuẩn sản phẩm."
