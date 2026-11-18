# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (backend replay, n = 100 mỗi tiêu chí, lớp positive = `FAIL`).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | Đạt | **dùng** (non-blocking, chỉ cho giọng văn) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **Không đạt** (κ, R) | **loại** |

Ma trận nhầm lẫn:
- tone_ok: tp 24 · fp 4 · fn 6 · tn 66. Tỉ lệ fail quan sát 0.28 → Rogan-Gladen 0.30.
- helpful: tp 9 · fp 0 · fn 21 · tn 70. Tỉ lệ fail quan sát 0.09 → Rogan-Gladen 0.30.

## ≥5 case audit thủ công

### tone_ok (10 bất đồng)
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha…" | PASS | FAIL | Người | Xưng "em" + "nha" quá suồng sã. Rubric: thêm danh sách xưng hô cấm với khách (em/anh trống, "nha", "nhé"), mặc định "anh/chị" + "LogiX". |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ…" | PASS | FAIL | Người | Xưng "bạn" với khách. Cùng quy tắc xưng hô ở #1. |
| 3 | H-029 "…không cần nhắn thêm." | PASS | FAIL | Người | Judge chỉ thấy "Dạ, vui lòng". Rubric: câu mệnh lệnh / cấm khách liên hệ = FAIL dù có kính ngữ. |
| 4 | H-042 "…đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt vì thiếu xin lỗi. Rubric: xin lỗi **không** bắt buộc khi kết quả có lợi cho khách; ngắn gọn đủ ý là PASS. |
| 5 | H-051 "LogiX đã refund 90.000đ…" | FAIL | PASS | Người | Style guide CS LogiX cho phép "refund". Rubric: liệt kê từ tiếng Anh được phép. |
| 6 | H-063 "…và sẽ hoàn thêm cho anh/chị ạ." | PASS | FAIL | Người | Hứa ngoài kết quả hệ thống. Đây là lỗi nội dung, không phải giọng văn: chuyển sang grader code (FM-08 / cam kết không có trong ledger), không nhồi vào tone. |
| 7 | H-070 "…ạ 😂😂" | PASS | FAIL | Người | Rubric: emoji cười trong khiếu nại = FAIL. |
| 8 | H-081 "đơn O-107 chưa đủ điều kiện hoàn…" | FAIL | PASS | **Judge** | Ghi chú: nhãn người sai (đọc nhầm ticket). Sửa **nhãn** thành FAIL, không sửa rubric; cần người thứ hai duyệt nhãn. |
| 9 | H-088 "Dạ, dạ, LogiX…" | FAIL | PASS | Người (biên) | Lỗi gõ lặp từ. Rubric: lỗi gõ nhẹ không đổi nghĩa = PASS. |
| 10 | H-094 "Da, LogiX da hoan…" (mất dấu) | PASS | FAIL | Người | Rubric: tiếng Việt không dấu = FAIL (trông như dịch máy). |

### helpful (21 bất đồng, toàn bộ là judge PASS / người FAIL)
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú |
|---|---|---|---|---|---|
| 1 | HL-015 "đã hoàn 55.000đ cho đơn O-112" | PASS | FAIL | Người | Ledger ghi 40.000đ: reply nói sai số tiền (FM-08). Judge không thấy ledger. |
| 2 | HL-020 "đã hoàn 90.000đ cho đơn O-303" | PASS | FAIL | Người | Ledger 120.000đ (FM-08). |
| 3 | HL-016 / HL-028 / HL-035 … (xin lỗi lặp 2 lần + số tiền sai) | PASS | FAIL | Người | Câu xin lỗi dài kéo judge lên PASS dù số tiền sai: judge bị **verbosity bias**. |
| 4 | HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." | PASS | FAIL | Người | Không nói số tiền, không nói kết quả. Rubric helpful phải yêu cầu: kết quả + số tiền + bước tiếp theo. |
| 5 | HL-043 "…đã hoàn 35.000đ cho đơn O-202" | PASS | FAIL | Người | Ledger 20.000đ (approval cap): đúng mẫu C-008 trên agent. |
| 6 | HL-087 "đã hoàn 60.000đ cho đơn O-202" | PASS | FAIL | Người | Ledger 90.000đ: nói **thấp** hơn cũng là sai. |

## Kết luận

- **tone_ok: dùng được**, nhưng chỉ làm metric non-blocking về giọng văn. 7/10 bất đồng là judge dễ dãi với xưng hô/emoji/không dấu, 2/10 là judge quá khắt khe (xin lỗi, "refund"), 1/10 là nhãn người sai (H-081). Sửa rubric theo bảng trên rồi gán nhãn lại một tập held-out mới trước khi nâng lên blocking.
- **helpful: loại, không được làm gate.** Recall 0.30 nghĩa là judge bỏ sót 21/30 reply hỏng; κ 0.375 dưới sàn 0.6. Precision 1.0 không cứu được vì judge gần như luôn nói PASS. Nguyên nhân gốc: 18/21 ca bỏ sót là **reply nói số tiền khác ledger** (3 ca còn lại là reply không nêu kết quả, ví dụ HL-047/091/097). Judge chỉ đọc văn bản, không có ledger, nên về nguyên tắc không thể chấm câu "số tiền có đúng không". Nếu dùng judge này làm gate, một PR gây FM-08 sẽ được merge.
- Thay thế: câu hỏi "reply có nói đúng số tiền không" chuyển sang grader code `reply_matches_ledger` (`lab9/graders.py`, FM-08), so trực tiếp với ledger, blocking với sàn 1.0. Phần "helpful" còn lại (có nêu kết quả + bước tiếp theo) cần rubric mới và calibrate lại.
- Rogan-Gladen: cả hai judge đều cho tỉ lệ fail đã hiệu chỉnh ≈ 0.30, nhưng với helpful con số này dựa trên sens 0.30. Hiệu chỉnh trên một judge kém rất nhạy với sai số của sens/spec, nên không được dùng nó để "cứu" helpful.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai (100 dòng, cũng chính là dữ liệu dùng để sửa rubric), nên cần nhãn người mới để có held-out thật. Một nhãn người đã sai (H-081), nên nhãn cũng cần người thứ hai kiểm.
