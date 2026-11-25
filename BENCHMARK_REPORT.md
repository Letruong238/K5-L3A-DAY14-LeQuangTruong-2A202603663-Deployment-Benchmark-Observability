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

> _(CP8 — điền sau khi chạy stack Docker)_

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Đừng nhìn trung bình.** Với 3 % request chậm, avg chỉ tăng 20 % nhưng p99 tăng 7.5 lần; SLO và alert phải đặt theo p95/p99.
  2. **Một instance có điểm bão hoà.** Tăng concurrency quá ~50 thì RPS không tăng mà latency nổ do xếp hàng; benchmark cho biết lúc nào phải scale.
  3. **Eval là test cho chất lượng.** Golden dataset + baseline + exit code biến "bản mới có tệ hơn không?" thành một bước CI tự động; so phiên bản bằng *cost / câu đúng* chứ không phải *cost / câu*.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

_(bổ sung sau CP8)_
