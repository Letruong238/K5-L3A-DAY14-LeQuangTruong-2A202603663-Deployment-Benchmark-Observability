# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON / CSV / screenshot thật trong repo (`reports/`, `benchmark-results/`, `screenshots/`), không ước lượng.

**Họ tên:** Le Quang Truong — MSSV 2A202603663
**Ngày chạy:** 2026-11-25
**Môi trường:** Laptop Windows 11, Intel Core i5-12500H, 16 GB RAM · chạy trong WSL2 Ubuntu (16 vCPU, 7.6 GB RAM cấp cho WSL) · Python 3.12 · Docker Desktop (WSL2 backend). Load generator (`load_test.py`) và agent chạy **trên cùng một máy**.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500** ms | Khi chưa quá tải (concurrency ≤ 50) p95 đo được ≈ 220–240 ms (`load_v2_c1/c10/c50.json`) → 500 ms cho khoảng dự phòng ~2 lần cho mạng, cold start, model chậm hơn. Đặt theo p95 chứ không theo avg vì avg che tail latency (mục 2.2). Cùng ngưỡng với `load_test.py` và alert p95 ở Bước 2. |
| Error rate `/ask` | < **2** % | Mock LLM có ~1 % lỗi upstream (`MOCK_ERROR_RATE=0.01`), đo được 0.33–1.33 % ở hầu hết các lần chạy → error budget 2 % ≈ gấp đôi mức nền, đủ chịu dao động ngẫu nhiên nhưng vẫn bắt được sự cố thật (alert ở Bước 2 đặt lỏng hơn: 5 %). |
| Accuracy trên golden dataset | ≥ **85** % | Bản v2 ban đầu đạt 91.3 %, sau khi sửa KB đạt 100 % → 85 % là mức sàn cho CI (job ở mục 3.3): đủ cao để chặn bản tệ hẳn (v1 chỉ 4.3 %) mà không fail vì 1–2 câu biên. Kèm điều kiện **không có câu regression** so với baseline. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

Lệnh (agent v2 ở cổng 8002, mặc định `MOCK_SLOW_RATE=0.03`, `MOCK_ERROR_RATE=0.01`):

```bash
for c in 1 10 50 100; do
  python load_test.py --url http://localhost:8002 --requests 300 --concurrency "$c" --out "reports/load_v2_c${c}.json"
done
```

### 2.1 Concurrency vs latency (agent v2)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.61 | 189.9 | 218.5 | 1678.6 | 2.33 % (7/300 lỗi 503) | ❌ FAIL (error rate) |
| 10 | 38.45 | 192.6 | 220.9 | 1693.0 | 1.00 % (3/300) | ✅ PASS |
| 50 | 124.19 | 195.2 | 240.1 | 1678.0 | 0.33 % (1/300) | ✅ PASS |
| 100 | 109.74 | 269.8 | 1959.8 | 2126.4 | 1.33 % (4/300) | ❌ FAIL (p95) |

Nguồn: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`.

**Nhận xét:** RPS **không tăng tuyến tính**. Từ 1 → 10 gần tuyến tính (4.61 → 38.45, ×8.3) vì mỗi request vẫn chỉ mất ~190 ms, nên RPS ≈ concurrency / latency (10 / 0.24 s ≈ 41). Từ 10 → 50 RPS chỉ tăng ×3.2 (lý thuyết 50 / 0.23 ≈ 217 req/s) và từ 50 → 100 còn **giảm** (124.19 → 109.74), trong khi p50 tăng từ 195 lên 270 ms và p95 vọt lên 1959.8 ms. Lý do: agent chỉ chạy **1 process uvicorn** (1 event loop), mọi request còn phải qua middleware đo metrics, ghi log JSON, serialize response — phần CPU này tuần tự. Ở concurrency cao, request phải **xếp hàng** chờ event loop (saturation), thời gian chờ cộng vào latency. Thêm nữa load generator chạy cùng máy nên tranh CPU với agent. Kết luận: điểm "ngon" của một instance là khoảng concurrency 50; muốn hơn phải scale ngang (nhiều worker/instance — xem mục 5).
Riêng mức concurrency 1 fail SLO error rate (2.33 %) không phải do tải mà do **ngẫu nhiên**: 7 trên 300 request trúng lỗi upstream 1 % của mock (300 mẫu thì dao động 1–2 % là bình thường) → SLO nên đánh giá trên cửa sổ dài/nhiều request.

### 2.2 Tail latency

Khởi động lại v2 với từng giá trị `MOCK_SLOW_RATE`, mỗi lần `--requests 300 --concurrency 20`:

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 190.9 | 219.9 | 227.1 |
| 0.03 (mặc định) | 230.0 | 220.7 | 1696.2 |
| 0.1 | 410.4 | 1697.2 | 1718.4 |

Nguồn: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json` (RPS lần lượt 101.36 / 63.67 / 41.14).

**Nhận xét:** Mỗi request "slow" bị cộng thêm ~1500 ms. Với 3 % request chậm, **p99 nhạy nhất**: tăng từ 227.1 lên 1696.2 ms (×7.5) vì 3 % > 1 % nên p99 rơi đúng vào nhóm request chậm, trong khi p95 gần như không đổi (220.7 ms, vì 3 % < 5 %) và avg chỉ tăng 20 % (190.9 → 230.0). Lên 10 % thì cả **p95** cũng vọt lên 1697.2 ms (10 % > 5 %) và SLO p95 fail, còn avg mới tăng gấp đôi (410.4 ms) — avg vẫn "trông chấp nhận được" dù cứ 10 người thì 1 người chờ gần 2 giây. Vì vậy **SLO không nên đặt theo average**: avg bị số đông request nhanh kéo xuống, che mất trải nghiệm tệ của nhóm đuôi; percentile (p95/p99) nói thẳng "x % người dùng phải chờ lâu hơn ngưỡng". Ngoài ra RPS giảm từ 101 xuống 41 vì request chậm chiếm chỗ trong 20 slot đồng thời.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

```bash
python eval/run_eval.py --url http://localhost:8001 --out reports/eval_v1.json
python eval/run_eval.py --url http://localhost:8002 --out reports/eval_v2.json --baseline reports/eval_v1.json
# sau khi sửa KNOWLEDGE_BASE và restart v2:
python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json
```

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | **100 % (23/23)** |
| Avg tokens / câu | 56.6 | 108.8 | 116.1 |
| Tổng cost (USD) | 0.000397 | 0.000758 | 0.000811 |
| Cost / câu đúng (USD) | 0.00039735 | 0.00003607 | 0.00003528 |
| Latency p50 / p95 (ms) | 160.8 / 183.8 | 184.4 / 211.4 | 190.1 / 213.0 |
| Quality gate | ❌ FAIL (accuracy ≥ 80 %) | ✅ PASS (≥ 80 %, 0 regression) | ✅ PASS (≥ 100 %, 0 regression) |

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` — "JWT khác gì API key trong xác thực?" | `token`, `hết hạn`, `chữ ký` | KB không có tài liệu nào chứa "jwt" → retrieval rỗng, agent trả câu chung chung. Thêm entry `(["jwt"], "API key là chuỗi bí mật cố định, không tự hết hạn… JWT là token có chữ ký số… thời điểm hết hạn (exp)…")` vào cuối `KNOWLEDGE_BASE` trong `develop/utils/mock_llm.py` |
| `scaling-03` — "Kubernetes HPA scale dựa trên những chỉ số nào?" | `cpu`, `memory`, `custom metrics` | Tương tự, thêm entry `(["hpa", "autoscal"], "Kubernetes HPA… dựa trên mức dùng CPU, memory hoặc custom metrics như RPS…")` |

Trước khi thêm, đã kiểm tra trong `golden_dataset.jsonl` chỉ có đúng 2 câu này chứa các từ khoá `jwt` / `hpa` / `kubernetes` / `autoscal` → entry mới không thể "cướp" retrieval của câu khác. Kết quả eval cuối: **+2 câu cải thiện, 0 regression**, quality gate với `--min-accuracy 1.0` ✅ PASS (exit code 0). Không sửa `golden_dataset.jsonl`.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**
v2 thêm bước retrieval nên prompt dài hơn: token/câu **×1.92** (56.6 → 108.8), tổng cost **×1.91** (0.000397 → 0.000758 USD), latency p95 trong eval **+27.6 ms** (183.8 → 211.4 ms, gồm ~20 ms retrieval + thêm token). Đổi lại accuracy tăng từ 4.3 % lên 91.3 %. Tính **cost / câu đúng** thì v2 rẻ hơn khoảng **11 lần** (0.00039735 → 0.00003607 USD): v1 rẻ mỗi request nhưng gần như toàn trả lời sai, nên tiền bỏ ra là phí. Đây là lý do so 2 phiên bản nên dùng "cost / câu đúng" chứ không phải "cost / câu". Sửa KB làm token tăng thêm một chút (116.1) nhưng cost / câu đúng còn giảm tiếp (0.00003528). Kết luận: trade-off của v2 hoàn toàn đáng — latency chỉ tăng ~15 %, vẫn xa SLO 500 ms.

### 3.1 Exercise 7.1 — đọc metrics

Metric từ `curl http://localhost:8002/metrics` sau 1 request:

```text
http_requests_total{method="POST",route="/ask",status="200"} 1.0
http_request_duration_seconds_bucket{le="0.2",method="POST",route="/ask"} 1.0
http_request_duration_seconds_count{method="POST",route="/ask"} 1.0
http_request_duration_seconds_sum{method="POST",route="/ask"} 0.16968273000020417
agent_in_flight_requests 0.0
llm_tokens_total{model="mock-llm-small",type="input"} 91.0
llm_cost_usd_total{model="mock-llm-small"} 4.6649999999999996e-05
agent_info{model="mock-llm-small",version="v2"} 1.0
agent_question_length_chars_count 1.0
agent_question_length_chars_sum 40.0
```

- **Counter:** `http_requests_total`, `llm_tokens_total`, `llm_cost_usd_total` (chỉ tăng, dùng với `rate()`).
- **Gauge:** `agent_in_flight_requests` (lên xuống theo số request đang xử lý), `agent_info`.
- **Histogram:** `http_request_duration_seconds` (gồm `_bucket{le=...}`, `_count`, `_sum`) → tính p95 bằng `histogram_quantile`.
- **Nếu label `route` dùng URL thật** (`request.url.path`): mỗi URL lạ bot quét (`/wp-admin`, `/.env`, `/a1b2c3`…) tạo **một time series mới** cho mỗi metric × method × status → số series tăng không giới hạn (cardinality bùng nổ), Prometheus tốn RAM/disk tới mức sập, query chậm; kẻ tấn công có thể cố tình làm sập hệ thống giám sát. Dùng `route.path` (template) thì số giá trị bị chặn bằng số route khai báo, mọi URL lạ gộp vào `unmatched`.
- **Nhiệm vụ:** đã thêm `QUESTION_LENGTH = Histogram("agent_question_length_chars", ..., buckets=(20, 50, 100, 200, 500, 1000, 2000))` trong `develop/app.py` và gọi `.observe(len(body.question))` trong `/ask` (thấy `agent_question_length_chars_sum 40.0` ở trên). Dùng Histogram, không dùng label `length=...`, vì độ dài có vô số giá trị.

### 3.2 Quality gate bằng exit code

Chạy eval v1 với baseline là v2:

```text
$ python eval/run_eval.py --url http://localhost:8001 --baseline reports/eval_v2.json
  Accuracy      : 4.3%  (1/23)
  Accuracy                     91.3%        4.3%    -87.0 điểm %
  ⚠️  Câu bị regression : 20 ['docker-01', 'docker-02', 'probe-01', ...]
  Quality gate (accuracy ≥ 80%, không regression): ❌ FAIL
EXIT CODE = 1
```

`run_eval.py` trả **exit code 1** khi accuracy dưới `--min-accuracy` hoặc có câu regression so với baseline, **exit code 0** khi đạt (v2 với `--min-accuracy 0.85` → exit code 0). CI chỉ cần chạy lệnh này như một bước: exit code ≠ 0 làm job fail → đặt job là *required status check* trong branch protection thì PR không merge được.

### 3.3 Job CI chặn merge

File [`.github/workflows/quality-gate.yml`](.github/workflows/quality-gate.yml):

```yaml
name: quality-gate
on:
  pull_request:
  workflow_dispatch:
jobs:
  eval:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: 06-lab-complete/benchmark-observability/develop
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - name: Start agent v2
        run: |
          AGENT_VERSION=v2 MOCK_ERROR_RATE=0 PORT=8000 python app.py &
          for i in $(seq 1 30); do curl -sf localhost:8000/health && break; sleep 1; done
      - name: Eval + quality gate (fail nếu accuracy < 85% hoặc có regression)
        run: python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json
```

`MOCK_ERROR_RATE=0` để lỗi ngẫu nhiên của mock không làm CI fail oan; `--baseline` bắt regression so với bản đã được duyệt.

---

## 4. Observability stack (production)

Stack chạy bằng `docker compose -p k5-day14-observability up -d --build` trong `06-lab-complete/benchmark-observability/production/`, tải nền bằng Locust headless 30 user (`-u 30 -r 5`).

> Ghi chú sự cố: lần đầu bật stack, container agent restart liên tục với lỗi `ModuleNotFoundError: No module named 'uvicorn'` (mọi request qua Nginx trả 502). Nguyên nhân: `Dockerfile` copy thư viện vào `/home/agent/.local` nhưng user `agent` có HOME là `/app` (`useradd -d /app`), nên Python tìm user site-packages ở `/app/.local`. Đã sửa bằng 1 dòng `ENV PYTHONUSERBASE=/home/agent/.local` trong `production/Dockerfile` (giống Dockerfile Part 5), xoá CSV lỗi và chạy lại benchmark. `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals) — [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png)
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian) — [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`) — [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với **2 alert cùng firing** (`AgentHighErrorRate` + `AgentInstanceDown`) — [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png)

**Trả lời Exercise 7.4** (đọc từ dashboard lúc có tải 30 user, ảnh `grafana-dashboard.png`):

1. **p95 = 292.84 ms**, avg ≈ 200 ms (đường avg/p50 nằm sát nhau quanh 0.2 s), còn **p99 ≈ 1.9 s**. avg và p95 gần nhau nghĩa là phần lớn request đồng đều; p99 cách xa gấp ~9 lần là **tail latency** do ~3 % request bị mock làm chậm thêm 1.5 s — avg hầu như không lộ ra điều này.
2. **Nginx có chia đều:** đo bằng `sum by (instance) (rate(http_requests_total{job="agent",route="/ask"}[1m]))` khi chạy Locust 30 user: `172.20.0.7:8000` → **9.36 req/s**, `172.20.0.10:8000` → **9.27 req/s** — gần như bằng nhau (round-robin của Nginx). Header `X-Served-By` trong response cho biết request đi vào instance nào.
3. **Input tokens/s > output tokens/s** vì prompt gửi vào LLM gồm system prompt + tài liệu retrieval (RAG) + câu hỏi (ví dụ trace chậm: `input_tokens = 73`, `output_tokens = 35`), còn câu trả lời ngắn.
4. **`curl http://localhost:8080/metrics` → `HTTP/1.1 404 Not Found`**: Nginx cố ý chặn `/metrics` từ bên ngoài (metric lộ thông tin nội bộ: số request, chi phí, version). Prometheus scrape trực tiếp từng container agent trong network nội bộ `obs_net` qua DNS service discovery, không đi qua Nginx.

### 4.2 Thí nghiệm "phá hệ thống"

Cách làm: giữ Locust headless 30 user chạy liên tục, đổi cấu hình bằng biến môi trường khi chạy compose (vd `env MOCK_ERROR_RATE=0.2 docker compose -p k5-day14-observability up -d --scale agent=3 agent`), rồi một script hỏi `http://localhost:9090/api/v1/alerts` mỗi 3 giây để ghi thời điểm alert chuyển `pending` → `firing`. Thời gian tính từ lúc lệnh đổi cấu hình chạy xong. Số liệu metric lấy bằng PromQL trên Prometheus (cửa sổ 1 phút).

Mốc so sánh (cấu hình mặc định, 30 user): error rate 1.29 %, p50 174 ms, p95 291 ms, avg 220 ms, cost 2.27 USD/giờ (cửa sổ 1 phút), input 1341 / output 714 tokens/s, retrieval hit 16.4/s.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | pending sau 30 s, **firing sau 90 s** | **Error rate** (1.3 % → 20.8 %, chuyển đỏ) và "Requests/s theo status code" (đường 503 tăng vọt); trace lỗi tag `error=true` xuất hiện dày đặc trong Jaeger |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` | pending sau 24 s, **firing sau 144 s** | **Latency p50/p95/p99**: p95 291 ms → **1917 ms**, avg chỉ 220 → 643 ms, p50 gần như không đổi (174 → 203 ms) |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | pending sau 27 s, **firing sau 147 s** | **Cost burn rate (USD/giờ)**: 2.27 → **32.93 USD/giờ** (×14.5, cửa sổ 1 phút) và "LLM latency p95 theo model" (p50 174 → 410 ms, p95 291 → 681 ms; chưa đủ vượt ngưỡng p95 1 s) |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` | pending sau 9 s, **firing sau 39 s** | **Instances up** (3 → 2), `up{instance="172.20.0.7:8000"} = 0`; Traffic giảm 18.7 → 14.8 req/s; Error rate của agent **không tăng thêm** (20.8 % → 20.0 %, vẫn chỉ là mức của thí nghiệm A đang bật song song) vì request kẹt ở instance bị pause chưa bao giờ được agent xử lý xong — lỗi đó (nếu có) nằm ở Nginx, metric của agent không đếm |
| E | `AGENT_VERSION=v1` | **Không có** (alert cost lúc đó còn firing là "đuôi" của thí nghiệm C do cửa sổ 5 phút; cost cửa sổ 1 phút đã giảm còn 1.19 USD/giờ) | — | **Retrieval hit ratio** mất hẳn (`agent_retrieval_total` không còn tăng), **Tokens/s** giảm (input 1341 → 694/s), bảng version đổi sang `v1`; latency còn **tốt hơn** (p95 199 ms) |

Ảnh `prometheus-alert-firing.png` chụp lúc A và D cùng firing (`AgentHighErrorRate` value ≈ 0.196 + `AgentInstanceDown`). Sau thí nghiệm đã `docker unpause` và chạy lại compose không có biến môi trường để trả về mặc định.

Ghi chú: `LLMCostBurnRateHigh` (ngưỡng 2 USD/giờ) cũng tự firing **ở điều kiện bình thường** ngay sau 2 lần benchmark 100 user (cost ≈ 5.03 USD/giờ). Với 30 user cost ≈ 1.9–2.3 USD/giờ nằm sát ngưỡng nên alert này dễ bật/tắt chập chờn — production cần đặt ngưỡng theo traffic dự kiến (hoặc theo cost / request) chứ không phải một con số cố định.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Bản v1 vẫn "khoẻ" theo mọi chỉ số hạ tầng (nhanh hơn, rẻ hơn, không lỗi) nhưng trả lời sai gần hết (eval: 4.3 % so với 100 %). Metric hạ tầng chỉ trả lời "có chạy không", không trả lời "có đúng không". Cách phát hiện:

1. **Quality gate trong CI/CD** — chạy `run_eval.py` trên golden dataset trước khi deploy (job `.github/workflows/quality-gate.yml`): v1 so với baseline v2 cho exit code 1, 20 câu regression → bị chặn trước khi lên production.
2. **Eval định kỳ trên production** (synthetic check): mỗi vài phút gửi một nhóm câu hỏi chuẩn vào URL thật, đẩy accuracy thành metric (vd `agent_eval_accuracy`) và alert khi giảm.
3. **Metric chất lượng gián tiếp**: alert khi retrieval hit ratio tụt (`agent_retrieval_total{result="hit"}`), khi `agent_info{version}` đổi ngoài lịch deploy, khi tokens/câu giảm đột ngột.
4. **Tín hiệu từ người dùng**: nút 👍/👎, tỉ lệ hỏi lại; và LLM-as-judge chấm mẫu ngẫu nhiên câu trả lời thật.

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được (Jaeger → `observable-agent` → `POST /ask`, Min Duration `1s`, Longest First — ảnh `jaeger-slow-trace.png`):

- `trace_id`: `c735ced0fbbe3e129eac46000e369ab5`
- Tổng thời gian: **1706.4 ms**
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small`** (**1689.0 ms, 99.0 %**) — `gen_ai.request.model = mock-llm-small`, `input_tokens = 73`, `output_tokens = 35`. Các span khác: `agent.run` 1704.8 ms (span cha), `agent.retrieve_context` chỉ 15.6 ms (0.9 %). → Chậm nằm ở **LLM upstream** (request rơi vào nhóm "slow" của mock), không phải code agent hay retrieval.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep c735ced0fbbe3e129eac46000e369ab5`):

```json
{"ts": "2026-11-25T10:10:06.969842+00:00", "level": "INFO", "service": "observable-agent", "instance": "5083c5e8ceb7", "event": "llm_call", "trace_id": "c735ced0fbbe3e129eac46000e369ab5", "span_id": "ca2b730514336605", "request_id": "02833ade3b1b", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 73, "output_tokens": 35, "cost_usd": 3.2e-05, "llm_ms": 1688.0}
{"ts": "2026-11-25T10:10:06.970314+00:00", "level": "INFO", "service": "observable-agent", "instance": "5083c5e8ceb7", "event": "http_request", "trace_id": "c735ced0fbbe3e129eac46000e369ab5", "span_id": "ca2b730514336605", "request_id": "02833ade3b1b", "method": "POST", "route": "/ask", "status": 200, "ms": 1705.9}
```

`llm_ms: 1688.0` trong log khớp span `chat mock-llm-small` 1689.0 ms; `ms: 1705.9` khớp tổng 1706.4 ms.

**Trace lỗi** (ảnh `jaeger-error-trace.png`, tìm bằng attribute `error=true`): `trace_id` `b6354339c20fa54f48db619ed8588190`, `POST /ask` trả **503** sau 842 ms; span `chat mock-llm-small` (825 ms) có `error.type = upstream_unavailable` và event `exception`: `utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)` kèm stacktrace (`app/main.py` line 153 → `utils/mock_llm.py` line 199). Log cùng `trace_id`:

```json
{"ts": "2026-11-25T10:11:50.791451+00:00", "level": "INFO", "service": "observable-agent", "instance": "5083c5e8ceb7", "event": "llm_error", "trace_id": "b6354339c20fa54f48db619ed8588190", "span_id": "67645fe5d7dcb7ac", "request_id": "1f7afd0a73a4", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-25T10:11:50.791839+00:00", "level": "INFO", "service": "observable-agent", "instance": "5083c5e8ceb7", "event": "http_request", "trace_id": "b6354339c20fa54f48db619ed8588190", "span_id": "67645fe5d7dcb7ac", "request_id": "1f7afd0a73a4", "method": "POST", "route": "/ask", "status": 503, "ms": 841.6}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

```bash
docker compose -p k5-day14-observability up -d --build --scale agent=1
docker compose -p k5-day14-observability run --rm locust -f /mnt/locust/locustfile.py --host http://nginx \
  --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_1
docker compose -p k5-day14-observability up -d --scale agent=3
docker compose -p k5-day14-observability run --rm locust -f /mnt/locust/locustfile.py --host http://nginx \
  --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_3
```

Số liệu dòng **Aggregated** (tất cả request) trong CSV:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 66.37 | 190 | 220 | 1700 | 73 / 7905 (0.92 %) |
| 3 | 66.11 | 190 | 230 | 1700 | 72 / 7877 (0.91 %) |

Riêng `POST /ask`: 1 instance → 60.72 req/s, p50 190 / p95 230 / p99 1700 ms, avg 231.5 ms, 73 lỗi 503; 3 instance → 60.27 req/s, p50 190 / p95 230 / p99 1700 ms, avg 233.2 ms, 72 lỗi 503.
Nguồn: `benchmark-results/scale_1_stats.csv`, `benchmark-results/scale_3_stats.csv`.

**Giải thích kết quả:** Scale từ 1 lên 3 instance **không cải thiện** RPS hay p95 (chênh lệch nằm trong sai số đo). Lý do:

1. **1 instance chưa bão hoà.** Agent là async, phần lớn thời gian mỗi request là *chờ* LLM (~190 ms `asyncio.sleep` mô phỏng gọi API), không tốn CPU → một event loop giữ được hàng trăm request đồng thời; 100 user Locust chỉ tạo ~66 req/s, dưới mức một instance chịu được (ở develop đã đo ~124 req/s với concurrency 50).
2. **Tải bị giới hạn bởi chính Locust.** Mỗi user nghỉ 0.5–2 s giữa các lần gọi (`wait_time = between(0.5, 2)`), nên throughput tối đa ≈ 100 / (1.25 s + 0.23 s) ≈ 67 req/s — đúng bằng con số đo được dù có bao nhiêu instance.
3. **Nút thắt latency là LLM upstream**, không phải số instance: p50 ≈ 190 ms chính là latency mock LLM, p99 1700 ms là các request "slow" của LLM (span `chat …` chiếm 99 % trong trace). Thêm instance không làm LLM nhanh hơn; lỗi 503 (~0.9 %) cũng đến từ LLM nên không đổi.

Muốn thấy lợi ích của scale ngang thì cần tải vượt khả năng 1 instance (nhiều user hơn / bỏ think time / workload tốn CPU), hoặc khi 1 instance chết (thí nghiệm D) thì các instance còn lại vẫn phục vụ → scale giúp **availability** ngay cả khi chưa giúp latency.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Đừng nhìn trung bình.** Với 3 % request chậm, avg chỉ tăng 20 % nhưng p99 tăng 7.5 lần; SLO và alert phải đặt theo p95/p99.
  2. **Một instance có điểm bão hoà.** Tăng concurrency quá ~50 thì RPS không tăng mà latency nổ do xếp hàng; benchmark cho biết lúc nào phải scale.
  3. **Eval là test cho chất lượng.** Golden dataset + baseline + exit code biến "bản mới có tệ hơn không?" thành một bước CI tự động; so phiên bản bằng *cost / câu đúng* chứ không phải *cost / câu*.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

  - **Chất lượng:** metric `agent_eval_accuracy` từ eval định kỳ (synthetic check) + alert khi tụt dưới 85 %; alert khi retrieval hit ratio giảm mạnh hoặc `agent_info{version}` đổi ngoài lịch deploy (bắt được sự cố kiểu thí nghiệm E).
  - **Tầng load balancer:** metric/log của Nginx (tỉ lệ 502/504, upstream response time) và health check từ bên ngoài vào URL public — vì thí nghiệm D cho thấy lỗi 504 ở Nginx không hiện trên metric của agent.
  - **Cost:** alert theo **cost / request** và **ngân sách ngày** (burn rate so với budget) thay cho ngưỡng cố định 2 USD/giờ vốn bật cả khi traffic bình thường tăng; theo dõi cost theo model để thấy ngay khi ai đó đổi sang model đắt (thí nghiệm C).
  - **Lỗi upstream LLM:** `llm_requests_total{status="error"}` theo provider + alert riêng, để phân biệt lỗi do LLM với lỗi do code agent; thêm retry/circuit breaker và đo số lần retry.
  - **Saturation:** alert khi `agent_in_flight_requests` cao kéo dài và theo dõi CPU/RAM container — ở develop đã thấy quá concurrency ~50 thì p95 nổ do xếp hàng.
  - **SLO theo error budget** (multi-window burn rate, vd 1 h và 6 h) thay vì ngưỡng tức thời, để giảm alert giả nhưng vẫn bắt sự cố kéo dài.
