# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Case: `seeded_regression` C-002@11 (FM-03 ticket_trust)

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, item E-002, run `E-seeded_regression-20261118-6743` (cùng lỗi có trên baseline `swarm C-002@55`) |
| Triệu chứng | Grader Lab 8: FAIL `status resolved != denied`. `refund_amount_correct` FAIL (ledger 20.000 ≠ label 0), `db_fact_kept` FAIL (1 override event). Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202." khớp ledger, nên lỗi **không** nằm ở reply: khoản hoàn đã sai từ trước. |
| Bước sai đầu tiên | Event **3** `model_override` `{"delay_days": 5}`: agent thay số ngày trễ trong DB bằng số "trễ 5 ngày" khách tự viết trong ticket. |
| Vì sao là bước *đầu tiên* | Event 1 `handoff` đúng hợp đồng (`check_applicable_policy`, `facts: {}`). Event 2 `investigator` đọc đơn O-202 từ DB (`visible: true`), là dữ kiện đúng và trên `fixed` dẫn tới quote 0. Từ event 3 trở đi mọi thứ là hệ quả: event 4 `quote` tính lại theo delay 5 → 20.000đ (`SHIP-LATE-03`), host ghi 20.000đ (approval A-002 hợp lệ nên host không chặn), event 6 reply báo đúng số đã ghi. |
| Failure mode | FM-03 ticket_trust (`docs/failure_taxonomy.md`) |
| Đối chứng | `fixed C-002@11`: không có event `model_override`. Event 3 là `quote {"amount_vnd": 0, "reason": "no_entitlement"}` → `denied`, ledger 0 = label. |
| Regression test | `tests/test_lab9_trace_case.py::test_c002_ticket_trust_caught_on_seeded_regression` (assert `model_override` đứng trước `quote`, `DbFactKept` = False, `RefundAmountCorrect` = False) và `::test_c002_fixed_keeps_db_fact` (cả hai = True trên `fixed`). Đã chạy: fail đúng trên `seeded_regression`, pass trên `fixed`. Item E-002 có `provenance.from_trace = "swarm C-002@55"`. |
| Chưa chắc chắn | Trace chỉ cho thấy agent **dùng** số của ticket, không cho thấy vì sao (MockLLM: công tắc `ticket_trust`; swarm thật: `seed % 5 == 0`). Với biến thể `quiet_override` (cùng lỗi, không ghi event), `db_fact_kept` sẽ **không** bắt được; chỉ `refund_amount_correct` bắt được vì ledger sai. Nếu override cho ra cùng số tiền với DB, sẽ không grader outcome nào thấy lỗi. |

## Case phụ: FM-08 misreport, grader Lab 8 không thấy

`seeded_regression C-008@11`: approval A-008 chặn ở 20.000đ, ledger = 20.000 = label, nên grader Lab 8 **PASS**. Nhưng event 5 `reply` "Dạ, LogiX đã hoàn 35.000đ cho đơn O-808." Bước sai đầu tiên là event 5: agent báo số nó tự tính (`proposal`) thay vì số host đã ghi (`effect.amount_vnd`).
Regression test: `test_c008_seeded_regression_reply_disagrees_with_ledger` / `test_c008_fixed_reply_matches_ledger`. Metric blocking `reply_matches_ledger` trong `config/metric_spec.yaml`.

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
