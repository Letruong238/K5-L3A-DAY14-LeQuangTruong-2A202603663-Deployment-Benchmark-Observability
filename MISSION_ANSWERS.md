# Day 14 Lab - Mission Answers

> Môi trường chạy: Windows 11 + WSL2 (Ubuntu), Python 3.12 (venv tạo bằng `uv`), Docker Desktop (bật WSL integration).

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

Các vấn đề tìm được trong `01-localhost-vs-production/develop/app.py`:

| # | Anti-pattern | Dòng | Vì sao nguy hiểm |
|---|---|---|---|
| 1 | API key hardcode: `OPENAI_API_KEY = "sk-hardcoded-..."` | 17 | Push code lên GitHub là lộ key, ai cũng dùng được và mình bị tính tiền |
| 2 | Mật khẩu database hardcode trong `DATABASE_URL` (`admin:password123`) | 18 | Lộ thông tin đăng nhập database cùng với code |
| 3 | Config cố định trong code: `DEBUG = True`, `MAX_TOKENS = 500` | 21–22 | Muốn đổi giữa dev/staging/production phải sửa code và deploy lại; debug bật sẵn ở production |
| 4 | Dùng `print()` thay vì logging, và **in cả secret ra log** (`[DEBUG] Using key: ...`) | 33–38 | Ai đọc được log là lấy được key; log dạng text khó tìm kiếm/phân tích |
| 5 | Không có health check endpoint (`/health`) | 42 | App treo/crash thì nền tảng cloud không biết để restart, load balancer vẫn gửi request vào |
| 6 | `host="localhost"` | 51 | Chạy trong container thì bên ngoài không kết nối được (phải là `0.0.0.0`) |
| 7 | Port cứng `8000`, không đọc biến `PORT` | 52 | Railway/Render cấp port qua biến môi trường `PORT` → app nghe sai cổng, deploy lỗi |
| 8 | `reload=True` luôn bật | 53 | Chế độ tự nạp lại code chỉ dành cho lúc dev, tốn tài nguyên và không ổn định ở production |
| 9 | Không xử lý graceful shutdown (không bắt `SIGTERM`) | cả file | Khi nền tảng tắt/redeploy, request đang xử lý bị cắt ngang |

### Exercise 1.2: Chạy bản develop

Lệnh (trong `01-localhost-vs-production/develop/`):

```bash
uv pip install -r requirements.txt
python app.py
```

Gọi thử:

```text
$ curl -X POST "http://localhost:8000/ask?question=Hello"
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
date: Wed, 25 Nov 2026 08:26:39 GMT
server: uvicorn
content-length: 22
content-type: application/json

{"detail":"Not Found"}
```

Log của server (bằng chứng secret bị in ra log):

```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.
```

**Nhận xét:** app chạy được (`/ask` trả lời), nhưng chưa sẵn sàng production: không có `/health` (404), secret bị ghi ra log, chỉ nghe trên `localhost:8000`.

### Exercise 1.3: Chạy bản production và so sánh

Lệnh (trong `01-localhost-vs-production/production/`):

```bash
cp .env.example .env
uv pip install -r requirements.txt
python app.py
```

Gọi thử:

```text
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":9.2,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:28:47.377109+00:00"}

$ curl http://localhost:8000/ready
{"ready":true}

$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","model":"gpt-4o-mini"}
```

Bảng so sánh:

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`DEBUG = True`, key, DB URL) | Đọc từ biến môi trường qua `config.py` + `.env` | Cùng một code chạy được ở nhiều môi trường chỉ bằng cách đổi biến; secret không nằm trong code nên không bị commit lên Git |
| Secrets | Key ghi thẳng trong code và bị in ra log | Lấy từ env, không log secret (chỉ log độ dài câu hỏi, IP) | Tránh lộ key qua GitHub hoặc qua hệ thống log mà nhiều người đọc được |
| Health check | ❌ Không có (`/health` trả 404) | ✅ `/health` (liveness) + `/ready` (readiness) | Nền tảng cloud dùng `/health` để biết khi nào cần restart; load balancer dùng `/ready` để chỉ gửi traffic vào instance đã sẵn sàng |
| Logging | `print()` dạng text tự do | Log JSON có `time`, `level`, `msg` | Log có cấu trúc dễ tìm kiếm, lọc, đếm lỗi trong các công cụ như Loki/Datadog |
| Shutdown | Đột ngột, không xử lý | Graceful: `lifespan` dọn dẹp + bắt `SIGTERM` | Khi redeploy/scale down, request đang chạy được hoàn thành thay vì bị cắt ngang, không mất dữ liệu |
| Host | `localhost` | `0.0.0.0` (từ env `HOST`) | Trong container phải nghe trên `0.0.0.0` thì bên ngoài mới gọi vào được |
| Port | Cứng `8000` | Đọc từ env `PORT` | Railway/Render tự cấp port qua biến `PORT` |
| Reload | Luôn `reload=True` | Chỉ bật khi `DEBUG=true` | Tránh tốn tài nguyên và hành vi khó đoán ở production |
| Input | Nhận `question` qua query string | Nhận JSON body, thiếu `question` → 422 | Kiểm tra input rõ ràng, trả lỗi có nghĩa thay vì crash |

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions (`02-docker/develop/Dockerfile`)

1. **Base image:** `python:3.11` — bản Python đầy đủ (có cả compiler, thư viện hệ thống), rất nặng (~1 GB).
2. **Working directory:** `/app` (lệnh `WORKDIR /app`) — mọi lệnh `COPY`, `RUN`, `CMD` sau đó chạy trong thư mục này của container.
3. **Tại sao COPY `requirements.txt` trước?** Docker build theo từng layer và cache layer nào không đổi. Copy `requirements.txt` rồi `pip install` trước, sau đó mới copy code: khi chỉ sửa code, layer cài thư viện vẫn dùng lại cache, build lại chỉ mất vài giây. Nếu `COPY . .` trước thì mỗi lần sửa một dòng code là phải cài lại toàn bộ thư viện.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh mặc định, bị thay thế hoàn toàn nếu truyền lệnh khác khi `docker run <image> <lệnh>`. `ENTRYPOINT` là chương trình cố định luôn được chạy; tham số truyền vào `docker run` được nối thêm vào sau nó. Hay dùng kết hợp: `ENTRYPOINT ["python"]` + `CMD ["app.py"]` → mặc định chạy `python app.py`, nhưng vẫn đổi được file cần chạy.

### Exercise 2.2: Build và chạy container

```bash
# từ gốc repo
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
docker run -d --name agent-dev -p 8000:8000 my-agent:develop
```

```text
$ curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker"
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"status":"ok","uptime_seconds":4.0,"container":true}

$ docker logs agent-dev
INFO:     Started server process [1]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     172.17.0.1:55052 - "POST /ask?question=What%20is%20Docker HTTP/1.1" 200 OK
INFO:     172.17.0.1:55068 - "GET /health HTTP/1.1" 200 OK
```

### Exercise 2.3: Multi-stage build và so sánh kích thước

`02-docker/production/Dockerfile`:

- **Stage 1 (`builder`):** từ `python:3.11-slim`, cài thêm `gcc`, `libpq-dev` (công cụ build), rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này chỉ để cài đặt, không dùng để deploy.
- **Stage 2 (`runtime`):** bắt đầu lại từ `python:3.11-slim` sạch, tạo user non-root `appuser`, chỉ `COPY --from=builder /root/.local` (thư viện đã cài) + `main.py` + `utils/`, thêm `HEALTHCHECK`, chạy `uvicorn` 2 workers.
- **Tại sao nhỏ hơn:** (1) base `slim` thay vì bản full; (2) `gcc`, header, pip cache… nằm lại ở stage builder, không vào image cuối.

Đo bằng `docker images` trên máy (Docker Desktop, containerd image store):

```text
$ docker images | grep my-agent
IMAGE                ID             DISK USAGE   CONTENT SIZE
my-agent:advanced    4d05c0f77f4d        240MB         57.4MB
my-agent:develop     834c6ce9c6b1       1.67GB          426MB
```

| | Develop (`python:3.11`, 1 stage) | Production (`slim`, multi-stage) |
|---|---|---|
| Disk usage (giải nén) | **1.67 GB** (≈ 1670 MB) | **240 MB** |
| Content size (nén) | 426 MB | 57.4 MB |

- **Chênh lệch:** (1670 − 240) / 1670 ≈ **85.6 %** nhỏ hơn (theo content size: (426 − 57.4) / 426 ≈ 86.5 %).

### Exercise 2.4: Docker Compose stack

Sơ đồ (`02-docker/production/docker-compose.yml`):

```text
                 ┌──────────────── network "internal" (bridge) ────────────────┐
 client ──:80──▶ │  nginx (reverse proxy, rate limit 10r/s, security headers)  │
                 │     │ proxy_pass http://agent_backend                        │
                 │     ▼                                                        │
                 │  agent :8000 (FastAPI, uvicorn 2 workers, healthcheck)       │
                 │     ├──▶ redis :6379  (cache / session / rate limit)         │
                 │     └──▶ qdrant :6333 (vector DB cho RAG)                    │
                 └─────────────────────────────────────────────────────────────┘
```

- Chỉ **nginx** mở cổng ra ngoài (80/443); agent, redis, qdrant chỉ nằm trong network nội bộ.
- Các service gọi nhau bằng **tên service** (Docker DNS): `redis://redis:6379/0`, `http://qdrant:6333`, `agent:8000`.
- `depends_on: condition: service_healthy`: agent chỉ khởi động khi redis và qdrant đã healthy.

Chạy (lưu ý K5: tạo `.env.local` trống, đặt `-p`):

```text
$ cd 02-docker/production && touch .env.local
$ docker compose -p k5-day14-docker up -d --build
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 6 seconds (healthy)    8000/tcp
nginx     nginx:alpine            Up 6 seconds              0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 17 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 17 seconds (healthy)   6379/tcp

$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":5.4,"version":"2.0.0","timestamp":"2026-11-25T08:40:04.431722"}

$ curl -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

Header `Server: nginx` và các security header chứng minh request đi qua Nginx rồi mới tới agent.

**Debug container:** `docker compose -p k5-day14-docker logs agent` (xem log), `docker exec -it <container> sh` (vào trong container), `docker compose ps` (xem trạng thái health).

> Ghi chú sự cố: lần đầu `docker compose up` lỗi `429 Too Many Requests` vì Docker Hub giới hạn số lần pull. Đã xử lý bằng cách pull image từ mirror `mirror.gcr.io/library/...` rồi `docker tag` về đúng tên gốc (`redis:7-alpine`, `nginx:alpine`, `qdrant/qdrant:v1.9.0`, `python:3.11-slim`).

---

## Part 3: Cloud Deployment

### Exercise 3.1 / 3.2: Deploy agent Part 6
- Nền tảng: **Railway** (deploy chính agent Part 6 trong `06-lab-complete/`, kèm Redis của Railway)
- URL: **https://agent-production-15e7bdbd.up.railway.app**
- Chi tiết lệnh, biến môi trường, kết quả test: [DEPLOYMENT.md](DEPLOYMENT.md)
- Screenshot: `screenshots/dashboard.png`, `screenshots/running.png`, `screenshots/test.png`

Kết quả 4 lệnh tự kiểm CP6 (ngày 2026-11-25):

```text
GET  /health                 → 200
GET  /ready                  → 200   (agent kết nối được Redis trên Railway)
POST /ask (không X-API-Key)  → 401
POST /ask (có X-API-Key)     → 200
```

Ghi chú: Railway CLI cảnh báo `railway.toml` (Config as Code) sắp bị thay bằng `.railway/railway.ts`, nhưng file cũ vẫn chạy tới 2026-12-01. Tôi giữ `railway.toml` vì đây là file đề bài và script chấm yêu cầu.

### So sánh `railway.toml` và `render.yaml` (bản trong `06-lab-complete/`)

| Điểm | `railway.toml` | `render.yaml` (Blueprint) |
|---|---|---|
| Định dạng / phạm vi | TOML, cấu hình **một service** (build + deploy) | YAML, mô tả **cả hạ tầng**: nhiều service trong một file (agent + Redis Key Value) |
| Cách build | `[build] builder = "DOCKERFILE"` | `runtime: docker` + `dockerfilePath`, `rootDir: 06-lab-complete` |
| Health check | `[deploy] healthcheckPath = "/health"` | `healthCheckPath: /health` |
| Redis | Không khai báo trong file — tạo Redis plugin trên dashboard rồi đặt `REDIS_URL` | Khai báo luôn `type: keyvalue`, `REDIS_URL` lấy tự động bằng `fromService … property: connectionString` |
| Secret (`AGENT_API_KEY`) | Không nằm trong file, đặt bằng `railway variables set` / dashboard | Khai báo key nhưng `sync: false` → nhập tay trên dashboard, giá trị không nằm trong Git |
| Restart | `restartPolicyType = "ON_FAILURE"` | Render tự restart khi health check fail |
| PORT | Railway cấp biến `PORT`; Dockerfile nghe `${PORT:-8000}` | Render cũng cấp `PORT`; cùng Dockerfile dùng được |

**Vì sao khác:** Railway thiên về "deploy nhanh từ CLI", cấu hình hạ tầng phụ (DB, Redis, biến) làm trên dashboard nên file chỉ cần nói cách build/health check. Render Blueprint theo hướng *infrastructure as code*: một file tái tạo được toàn bộ stack (web + Redis + liên kết biến), nên phải mô tả nhiều hơn và có cơ chế `sync: false` / `generateValue` để không lộ secret.

### Exercise 3.3: Pipeline Cloud Run (`03-cloud-deployment/production-cloud-run/`)

1. **test:** `cloudbuild.yaml` chạy container `python:3.11-slim`, cài `requirements.txt` + `pytest` và chạy `pytest tests/` — test fail thì pipeline dừng, không build.
2. **build:** `docker build` image, gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để build nhanh hơn.
3. **push:** đẩy mọi tag lên Container Registry.
4. **deploy:** `gcloud run deploy ai-agent` với image đúng `$COMMIT_SHA` (truy vết được commit nào đang chạy), region `asia-southeast1`, 1–10 instance, 512 MiB / 1 CPU, `ENVIRONMENT=production`, secret `OPENAI_API_KEY` lấy từ **Secret Manager** (không nằm trong code).
5. **`service.yaml`** mô tả trạng thái mong muốn của service (Knative): `minScale 1` (tránh cold start) / `maxScale 10`, mỗi instance nhận tối đa 80 request đồng thời, `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready`, `AGENT_API_KEY` và `OPENAI_API_KEY` lấy qua `secretKeyRef`.

---

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop/app.py`)

- **Key được check ở đâu?** Trong dependency `verify_api_key()` (dòng 39–54): `APIKeyHeader(name="X-API-Key")` đọc header, so với `API_KEY = os.getenv("AGENT_API_KEY", ...)`. Endpoint `/ask` gắn `Depends(verify_api_key)` nên request phải qua bước này trước khi vào hàm.
- **Sai key thì sao?** Thiếu header → **401** "Missing API key"; có header nhưng sai → **403** "Invalid API key". (Ở Part 6, theo hợp đồng của script chấm, cả thiếu lẫn sai đều trả 401.)
- **Rotate key thế nào?** Key nằm trong biến môi trường, không nằm trong code → đổi `AGENT_API_KEY` trên server/nền tảng rồi restart (redeploy), báo client dùng key mới. Muốn không gián đoạn thì cho phép tạm thời 2 key (cũ + mới) trong một khoảng chuyển tiếp rồi bỏ key cũ.

Chạy: `AGENT_API_KEY=secret-key-123 python app.py`

```text
$ curl -i http://localhost:8000/ask -X POST -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2: JWT authentication (`04-api-gateway/production/`)

**JWT flow:** client gửi username/password tới `POST /auth/token` → server kiểm tra (`authenticate_user`) rồi `create_token()` ký một token HS256 chứa `sub` (username), `role`, `iat`, `exp` (hết hạn sau 60 phút) bằng `JWT_SECRET` → client gửi `Authorization: Bearer <token>` ở mọi request → `verify_token()` kiểm tra chữ ký + hạn, lấy ra username/role mà **không cần tra database** (stateless). Token hết hạn → 401, chữ ký sai → 403.

Tài khoản K5: `student` / `demo123` (token đã che):

```text
$ curl -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username": "student", "password": "demo123"}'
{"access_token":"<TOKEN_DA_CHE>","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer <TOKEN_DA_CHE>..."}

$ curl -i http://localhost:8000/ask -X POST -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i http://localhost:8000/ask -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
{"question":"Explain JWT","answer":"Đây là câu trả lời từ AI agent (mock). ...","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

### Exercise 4.3: Rate limiting (`rate_limiter.py`)

- **Thuật toán:** **Sliding window** — mỗi user một `deque` lưu thời điểm các request; mỗi lần gọi bỏ các mốc cũ hơn 60 giây rồi đếm. Đếm ≥ giới hạn → 429 kèm `Retry-After`.
- **Giới hạn:** user thường **10 request/phút** (`rate_limiter_user`), admin 100 request/phút (`rate_limiter_admin`).
- **Bypass cho admin:** `app.py` chọn limiter theo `role` trong JWT: `role == "admin"` (tài khoản `teacher`) dùng limiter 100/phút. Muốn bỏ hẳn giới hạn thì bỏ qua `limiter.check()` khi role là admin.
- **Hạn chế:** state nằm trong RAM của process → chạy nhiều instance thì mỗi instance đếm riêng, restart là mất. Part 6 đã chuyển sang Redis.

Vòng lặp 20 request với cùng token (trước đó đã dùng 1 request ở bước 4.2, nên còn 9 lượt):

```text
Request 1 -> HTTP 200 ... "requests_remaining":8
Request 2 -> HTTP 200 ... "requests_remaining":7
...
Request 9 -> HTTP 200 ... "requests_remaining":0
Request 10 -> HTTP 429 {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
...
Request 20 -> HTTP 429 {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}

$ curl -i http://localhost:8000/ask -X POST -H "Authorization: Bearer $TOKEN" ... -d '{"question": "one more"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

→ Đúng 10 request thành công trong 60 giây (1 ở bước 4.2 + 9 trong vòng lặp), từ request thứ 11 bị **429**.

### Exercise 4.4: Cost guard implementation

**Bản mẫu `04-api-gateway/production/cost_guard.py`:** đếm token input/output mỗi user mỗi ngày, quy ra USD theo giá gpt-4o-mini ($0.15/1M input, $0.60/1M output). `check_budget()` gọi **trước** khi gọi LLM: user vượt $1/ngày → **402 Payment Required**; tổng toàn hệ thống vượt $10/ngày → 503; dùng ≥ 80 % → log cảnh báo. `record_usage()` gọi **sau** khi LLM trả lời để cộng chi phí. Kết quả sau 10 request của `student`:

```text
$ curl http://localhost:8000/me/usage -H "Authorization: Bearer $TOKEN"
{"user_id":"student","date":"2026-11-25","requests":10,"input_tokens":40,"output_tokens":308,"cost_usd":0.000191,"budget_usd":1.0,"budget_remaining_usd":0.999809,"budget_used_pct":0.0}
```

**Cách tôi làm ở Part 6 (`06-lab-complete/app/cost_guard.py`) — lưu trong Redis:**

- Key theo ngày UTC: `cost:daily:YYYY-MM-DD`, có TTL 2 ngày → sang ngày mới tự reset, Redis tự dọn key cũ.
- `check_budget()` (trước khi gọi LLM): đọc tổng đã tiêu; `>= DAILY_BUDGET_USD` → `HTTPException(402)` kèm `spent_usd`, `budget_usd`.
- `record_cost()` (sau khi gọi LLM): ước lượng token (~2 token/từ), tính USD, cộng bằng `INCRBYFLOAT` trong một transaction (MULTI/EXEC) → nhiều worker/instance cùng ghi vẫn không lệch số.
- Budget đọc từ biến môi trường `DAILY_BUDGET_USD`, không hard-code.

**Vì sao cần cả rate limit và cost guard:** rate limit chặn **tốc độ** (1 key không spam quá N request/phút), cost guard chặn **tổng tiền** (dù gọi chậm nhưng đều đặn cả ngày, hoặc nhiều key cộng lại, vẫn không vượt ngân sách).

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/app.py`)

- **`/health` (liveness)** — "process còn sống không?": luôn 200 khi app chạy, kèm uptime, version, kiểm tra RAM (`psutil`). Nền tảng thấy non-200/timeout → **restart** container.
- **`/ready` (readiness)** — "đã sẵn sàng nhận traffic chưa?": 503 khi đang khởi động, đang shutdown hoặc dependency (DB/Redis) chưa kết nối. Load balancer thấy 503 → **tạm không gửi request** vào instance đó, nhưng không restart.
- Khác nhau: Redis chết thì restart agent cũng không giúp gì → `/health` vẫn phải 200 (tránh restart vô ích), còn `/ready` trả 503 để rút instance khỏi load balancer.

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":3.7,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:40:39.890437+00:00","checks":{"memory":{"status":"ok","used_percent":17.7}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

### Exercise 5.2: Graceful shutdown

Cơ chế trong `develop/app.py`: middleware đếm `_in_flight_requests`; khi nhận **SIGTERM**, uvicorn ngừng nhận kết nối mới, chờ request đang chạy xong (`timeout_graceful_shutdown=30`), rồi chạy phần shutdown của `lifespan` (đặt `_is_ready=False`, chờ in-flight về 0, dọn dẹp) và thoát.

Test: gửi `/ask` (mock LLM trễ 3 giây), 0.5 giây sau gửi `kill -TERM`:

```text
Gui SIGTERM toi PID 20470 luc 15:40:40.414
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
[curl] HTTP 200, time 3.006316s
Process exit code: 0

--- log server
INFO:     Shutting down
INFO:     127.0.0.1:36146 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 15:40:43,023 INFO 🔄 Graceful shutdown initiated...
2026-11-25 15:40:43,023 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [20470]
```

→ Request đang chạy **vẫn hoàn thành (200, 3 giây)** dù SIGTERM tới giữa chừng; process thoát với **exit code 0**. Thứ tự log: "Shutting down" → request 200 → shutdown complete.

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/app.py`)

- Anti-pattern: `conversation_history = {}` trong RAM → chạy 3 instance thì mỗi instance có dict riêng; request 2 của user rơi vào instance khác là **mất history**; restart/redeploy là mất sạch.
- Bản production lưu session vào **Redis**: `save_session()` dùng `SETEX session:<id>` (TTL 1 giờ), `load_session()` đọc lại, `append_to_history()` giữ tối đa 20 message. Instance nào cũng đọc/ghi cùng một chỗ → agent **stateless**, scale ngang thoải mái.

### Exercise 5.4: Load balancing (3 instance + Nginx)

```text
$ cd 05-scaling-reliability/production && touch .env.local
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
$ docker compose -p k5-day14-scaling ps
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 6 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-2   Up 6 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-3   Up 6 seconds (healthy)    8000/tcp
k5-day14-scaling-nginx-1   Up 6 seconds              0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 17 seconds (healthy)   6379/tcp
```

10 request `POST /chat` qua Nginx (cổng 8080):

```text
served_by: instance-8fd155 | storage: redis
served_by: instance-d4ee7a | storage: redis
served_by: instance-157b97 | storage: redis
served_by: instance-8fd155 | storage: redis
served_by: instance-d4ee7a | storage: redis
served_by: instance-157b97 | storage: redis
served_by: instance-8fd155 | storage: redis
served_by: instance-d4ee7a | storage: redis
served_by: instance-157b97 | storage: redis
served_by: instance-8fd155 | storage: redis
```

→ Nginx chia **round-robin** đều cho 3 instance (8fd155 → d4ee7a → 157b97 → …). Docker DNS phân giải tên `agent` thành IP của cả 3 container. Nếu một instance chết, `proxy_next_upstream error timeout http_503` cho Nginx thử instance khác.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
Session ID: 7b9193c2-bcec-40ce-b3c3-27297922fba6

Request 1: [instance-d4ee7a]  Q: What is Docker?
Request 2: [instance-157b97]  Q: Why do we need containers?
Request 3: [instance-8fd155]  Q: What is Kubernetes?
Request 4: [instance-d4ee7a]  Q: How does load balancing work?
Request 5: [instance-157b97]  Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-d4ee7a', 'instance-157b97', 'instance-8fd155'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ...
  [user]: What is Redis used for?...
  [assistant]: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đ...

✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu của cùng một session được **3 instance khác nhau** xử lý, nhưng history đọc lại vẫn đủ **10 message** (5 user + 5 assistant) theo đúng thứ tự. Lý do: không instance nào giữ history trong RAM, tất cả đọc/ghi key `session:<id>` trong Redis dùng chung. (Script không tự tắt instance nào; nhưng vì state nằm ngoài agent nên tắt bớt một instance cũng không mất history.) Sau khi test: `docker compose -p k5-day14-scaling down`.

---

## Part 6: Final Project — output `check_production_ready.py`

Chạy trong `06-lab-complete/`, WSL2 (`os.name = posix`), Docker đang chạy, chế độ **đầy đủ**:

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số chi tiết từ output: image **56 MB**, build 20s; rate limit 429 sau 5 request (`RATE_LIMIT_PER_MINUTE=5`), restart agent vẫn 429 (state trong Redis); cost guard 402; 17 dòng log JSON hợp lệ; SIGTERM khi đang có request → request 200, exit code 0; `docker stop` exit code 0 trong 0.7s.

### Tóm tắt cách làm

| File | Làm gì |
|---|---|
| `app/config.py` | Đọc `PORT`, `ENVIRONMENT`, `LOG_LEVEL`, `AGENT_API_KEY`, `REDIS_URL`, `RATE_LIMIT_PER_MINUTE`, `DAILY_BUDGET_USD` bằng `os.getenv`; `AGENT_API_KEY` không có mặc định (trống → mọi request 401) |
| `app/redis_client.py` | `redis.from_url(...)` không kết nối lúc import (app vẫn khởi động khi chưa có Redis), timeout 2s; `redis_healthy()` = ping |
| `app/auth.py` | `APIKeyHeader("X-API-Key", auto_error=False)`, so sánh bằng `hmac.compare_digest`; thiếu/sai → 401 |
| `app/rate_limiter.py` | Sliding window bằng Redis sorted set (`ZREMRANGEBYSCORE` → `ZADD` → `ZCARD` → `EXPIRE` trong MULTI/EXEC), key là hash của API key; vượt → 429 + `Retry-After` |
| `app/cost_guard.py` | Chi phí theo ngày trong Redis (`INCRBYFLOAT`), vượt `DAILY_BUDGET_USD` → 402 |
| `app/main.py` | `JsonFormatter` (mỗi dòng log 1 JSON); middleware log mỗi request (method, path, status, ms — không log key/câu hỏi); `/health` không phụ thuộc Redis; `/ready` 503 khi Redis down; `/ask` = auth → rate limit → budget → LLM → ghi chi phí; exception handler trả 422/503/500 gọn, không lộ stack trace; `lifespan` để shutdown (không tự đăng ký SIGTERM để không đè handler của uvicorn) |
| `Dockerfile` | 2 stage `python:3.11-slim`, user non-root `app`, `HEALTHCHECK` gọi `/health`, `CMD ["sh","-c","exec uvicorn ... --port ${PORT:-8000} --workers 2"]` → uvicorn là PID 1 |
| `docker-compose.yml` | `agent` (build, `env_file .env required: false`, `REDIS_URL=redis://redis:6379/0`, `depends_on redis: service_healthy`) + `redis:7-alpine` có healthcheck + volume |
| `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Loại `.env`/cache/venv khỏi image; liệt kê đủ biến; config Railway (DOCKERFILE + `/health`) và Render (web + keyvalue, secret `sync: false`) |

Kiểm tra thêm bằng `docker compose up` (agent + Redis, `RATE_LIMIT_PER_MINUTE=3`):

```text
/health                          → 200 {"status":"ok",...}
/ready                           → 200 {"ready":true}
/ask không key                   → 401
/ask có key                      → 200 {"question":"What is deployment?","answer":"Deployment là quá trình ...","usage":{...}}
/ask {"question":""}             → 422 {"error":"Invalid request body","detail":[{"field":"question","msg":"String should have at least 1 character"}]}
/ask JSON hỏng                   → 422 {"error":"Invalid request body",...}   (không lộ stack trace)
3 request liên tiếp              → 200, 200, 429 (retry-after: 60)
docker compose stop redis        → /health 200, /ready 503 {"ready":false,"reason":"redis unavailable"}
```

Log mẫu của agent (mỗi dòng 1 JSON):

```text
{"ts": "2026-11-25T08:43:01.125+00:00", "lvl": "INFO", "logger": "agent", "msg": "llm_call", "event": "llm_call", "input_tokens": 2, "output_tokens": 34, "cost_usd": 2.07e-05}
{"ts": "2026-11-25T08:43:01.206+00:00", "lvl": "INFO", "logger": "agent", "msg": "request", "event": "request", "method": "POST", "path": "/ask", "status": 429, "ms": 64.8}
{"ts": "2026-11-25T08:43:05.600+00:00", "lvl": "INFO", "logger": "agent", "msg": "request", "event": "request", "method": "GET", "path": "/ready", "status": 503, "ms": 4003.1}
```

---

## AI log

| # | Hỏi AI gì | Nhận được gì | Tôi tự làm / tự kiểm tra thế nào |
|---|---|---|---|
| 1 | Nhờ Claude Code đọc repo và liệt kê các việc cần làm | Danh sách việc theo CP0–CP8 | Đối chiếu với `CHECKPOINTS.md`, `SUBMISSION.md`, `RUBRIC.md` |
| 2 | Hỏi cách cài môi trường (WSL2, `uv`, venv Python 3.12, bật Docker WSL integration) | Các lệnh cài đặt từng bước | Tự chạy; kiểm tra `python --version` ra 3.12 và `docker run --rm hello-world` in "Hello from Docker!" |
| 3 | Part 1: nhờ chỉ ra anti-pattern trong `develop/app.py` và hướng dẫn so sánh với `production/app.py` | Danh sách anti-pattern kèm số dòng, gợi ý bảng so sánh | Tự chạy cả hai bản, tự gọi curl; mọi output trong Part 1 là từ máy tôi |
| 4 | Part 2, 4, 5: nhờ Claude Code chạy các lệnh build/run/test trên máy tôi (WSL2) và soạn câu trả lời | Script chạy lệnh, output thật của `docker images`, `curl -i`, `test_stateless.py`; bản nháp câu trả lời | Output dán vào bài là output thật chạy trên máy tôi (không bịa số); tôi đọc lại từng câu trả lời, đối chiếu với code trong từng thư mục |
| 5 | Docker Hub báo `429 Too Many Requests` khi pull image | Gợi ý pull từ `mirror.gcr.io` rồi `docker tag` về tên gốc | Kiểm tra `docker images` có đủ `redis:7-alpine`, `nginx:alpine`, `qdrant/qdrant:v1.9.0`, `python:3.11-slim`; compose chạy được |
| 6 | Part 6: nhờ Claude Code viết code cho các TODO trong `06-lab-complete/` (config, auth, rate limit Redis, cost guard Redis, log JSON, health/ready, graceful shutdown, Dockerfile, compose, railway/render) | Code hoàn chỉnh + giải thích từng phần | Chạy `python check_production_ready.py` bản đầy đủ → `PASS 12/12`; tự test thêm bằng `docker compose up` (401/200/422/429, Redis down → /ready 503); đọc hiểu từng file để giải thích được với Lab Coach |
| 7 | CP6: hỏi cách deploy agent Part 6 lên Railway (CLI, Redis, biến môi trường, domain) | Hướng dẫn từng lệnh `railway ...`; Claude Code kiểm tra trạng thái deploy, chạy 3 lệnh test không cần key, soạn `DEPLOYMENT.md` | Tôi tự tạo tài khoản, đăng nhập, đặt `AGENT_API_KEY` (không đưa key cho AI; key lỡ lộ trong chat đã được đổi mới), tự chạy đủ 4 lệnh tự kiểm → 200/200/401/200 và chụp ảnh |
| 8 | CP7 (Part 7 Bước 1): nhờ Claude Code chạy load test 4 mức concurrency + 3 mức `MOCK_SLOW_RATE`, eval v1/v2, tìm keyword thiếu, sửa `KNOWLEDGE_BASE`, thêm metric `agent_question_length_chars`, viết job CI và soạn `BENCHMARK_REPORT.md` mục 1–3 | Script chạy lệnh, 10 file JSON trong `reports/`, 2 entry KB mới, file `.github/workflows/quality-gate.yml`, bản nháp nhận xét | Mọi số trong báo cáo được đọc trực tiếp từ file JSON (không ước lượng); kiểm tra entry KB mới chỉ khớp đúng 2 câu sai để không gây regression; eval cuối 23/23, 0 regression, exit code 0; không sửa `golden_dataset.jsonl` |
| 9 | CP8 (Part 7 Bước 2): nhờ Claude Code bật stack Prometheus/Grafana/Jaeger/Locust, sửa lỗi container agent restart (`No module named 'uvicorn'`), chạy Locust scale 1/3, thí nghiệm A–E đo thời gian tới firing, lấy span/log theo `trace_id`, soạn `BENCHMARK_REPORT.md` mục 4–6 | Chẩn đoán lỗi Dockerfile (thiếu `PYTHONUSERBASE`), script đo alert qua Prometheus API, CSV scale_1/scale_3, bản nháp phân tích | Tôi tự mở Grafana/Jaeger/Prometheus trên trình duyệt và chụp 4 ảnh; CSV lỗi (toàn 502) đã xoá và chạy lại; số trong báo cáo lấy từ CSV, Prometheus API và Jaeger API, đối chiếu với ảnh chụp |
