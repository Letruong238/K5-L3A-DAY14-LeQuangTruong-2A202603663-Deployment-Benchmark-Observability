# Deployment Information

## Public URL

https://agent-production-15e7bdbd.up.railway.app

## Platform

**Railway** — deploy thư mục `06-lab-complete/` (agent Part 6) bằng Railway CLI.

| Thành phần | Chi tiết |
|---|---|
| Project | `day14-agent` (environment `production`) |
| Service `agent` | Build từ `06-lab-complete/Dockerfile` theo `railway.toml` (`builder = "DOCKERFILE"`, `healthcheckPath = "/health"`) |
| Service `Redis` | Redis database của Railway (có volume), dùng cho rate limit + cost guard |
| Cổng | Railway cấp biến `PORT` (đang là 8080); `CMD` của Dockerfile lắng nghe `${PORT:-8000}` |

Các lệnh đã dùng (chạy trong `06-lab-complete/`):

```bash
npm i -g @railway/cli
railway login
railway init                          # tạo project day14-agent
railway add --database redis          # tạo Redis
railway add --service agent           # tạo service trống cho agent
railway variables --service agent --set "AGENT_API_KEY=<secret>" \
  --set 'REDIS_URL=${{Redis.REDIS_URL}}' --set "ENVIRONMENT=production" \
  --set "RATE_LIMIT_PER_MINUTE=10" --set "DAILY_BUDGET_USD=1.0"
railway up --service agent            # build + deploy
railway domain --service agent        # tạo URL public
```

## Test Commands

Đặt biến trước khi test (key là key đã đặt trên Railway, không ghi vào repo):

```bash
export BASE="https://agent-production-15e7bdbd.up.railway.app"
export AGENT_API_KEY="<key đã đặt trên Railway>"
```

### Health Check

```bash
curl "$BASE/health"
# {"status":"ok","uptime_seconds":39.6,"environment":"production","version":"1.0.0"}
```

### Readiness (Redis đã kết nối)

```bash
curl "$BASE/ready"
# {"ready":true}
```

### API Test (with authentication)

```bash
# Không có key → 401
curl -X POST "$BASE/ask" -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "Hello"}'

# Có key → 200
curl -X POST "$BASE/ask" -H "X-API-Key: $AGENT_API_KEY" -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "Hello"}'
```

### 4 lệnh tự kiểm (CP6) — chỉ in mã HTTP

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả đo ngày 2026-11-25:

```text
health: 200
ready:  200
ask (không key): 401
ask (có key): 200
```

`python check_production_ready.py --static-only --public-url https://agent-production-15e7bdbd.up.railway.app`:

```text
✅ Public URL: GET https://agent-production-15e7bdbd.up.railway.app/health → 200
```

## Environment Variables Set

Đặt trên Railway (service `agent` → Variables), **không** ghi trong code hay repo:

| Biến | Giá trị |
|---|---|
| `AGENT_API_KEY` | secret ngẫu nhiên (`secrets.token_urlsafe`) — không công khai |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` (tham chiếu tới service Redis, mạng nội bộ Railway) |
| `ENVIRONMENT` | `production` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `PORT` | Railway tự cấp |
| `LOG_LEVEL` | không đặt → mặc định `INFO` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
