# Hướng Dẫn Cấu Hình Policy Trên UI

Tài liệu này giải thích tab `Config` trong `app_gradio.py` theo đúng kiến trúc hiện tại của project: field nào đi vào đâu, ảnh hưởng bước nào, và field nào mới chỉ là config/schema.

## Policy Đi Theo Nhánh Nào Trong Architecture

Trong `docs/architecture.md`, tab `Config` nằm trên nhánh generate/publish:

```text
Config Tab
-> ValidationPolicyLoader / UI overrides
-> ValidationPolicy
-> SkillChatAgent
-> build_skill_from_spec
-> StaticValidator
-> SandboxRunner
-> PublishGateway
```

Ý nghĩa thực tế:

- cấu hình trong tab `Config` chủ yếu tác động đến bước `StaticValidator`
- nếu validation pass thì skill mới được đưa sang `SandboxRunner`
- nếu sandbox pass thì skill mới đi tiếp tới `PublishGateway`
- các field này không điều khiển trực tiếp `PolicyEngine` của runtime demo
- tuy vậy, metadata được validate ở đây sẽ được runtime dùng lại để chọn skill, check capability, và hiển thị review/risk context

## Cách Dùng Nhanh

1. Mở app:

```bash
uv run python app_gradio.py
```

2. Vào tab `Config`.

3. Nếu muốn nạp policy từ file YAML:

- điền `Policy YAML Path`
- bấm `Load Policy File`

Ví dụ:

```text
policies/mvp-safe.yaml
```

4. Chỉnh các field trên form.

5. Bấm `Apply UI Overrides` để áp dụng policy mới cho các lần build tiếp theo.

6. Muốn quay về policy đang active thì bấm `Reset Form To Active`.

## Giải Thích Từng Field Trên UI

### 1. `Policy YAML Path`

Field này dùng để nạp toàn bộ `ValidationPolicy` từ file YAML thông qua `ValidationPolicyLoader`.

Nó có vai trò:

- thay policy mặc định bằng policy riêng
- làm dữ liệu gốc để điền lại form UI
- cập nhật policy active mà `SkillChatAgent` sẽ dùng cho các lần build skill tiếp theo

Nó không tự chạy validation. Nó chỉ đổi config mà pipeline sẽ dùng ở turn sau.

### 2. `activation.min_description_chars`

Field này đi vào `validate_skill_activation()`.

Tác dụng:

- kiểm tra `skill.metadata.description` có quá ngắn hay không
- nếu ngắn hơn ngưỡng này thì validation fail ngay trước sandbox

Về mặt kiến trúc, đây là quality gate sớm ở lớp `StaticValidator`.

### 3. `activation.max_description_chars`

Field này cũng đi vào `validate_skill_activation()`.

Tác dụng hiện tại:

- nếu description dài hơn ngưỡng thì chỉ tạo warning
- chưa block publish

Nghĩa là đây là rule chất lượng mềm, không phải hard gate.

### 4. `activation.require_action_verb`

Field này bật hoặc tắt heuristic kiểm tra mô tả có động từ hành động hay không.

Tác dụng hiện tại:

- nếu bật và description không match heuristic verb regex, validator thêm warning
- chưa fail build

Mục đích kiến trúc:

- làm cho activation description mang tính hành động hơn
- giúp metadata dễ hiểu hơn cho agent/runtime khi chọn và diễn giải skill

### 5. `activation.forbidden_placeholder_patterns`

Đây là danh sách regex cấm trong description.

Tác dụng:

- chạy trong `validate_skill_activation()`
- nếu description match các pattern kiểu `TODO`, `FIXME`, `PLACEHOLDER`, `<...>` thì validation fail

Đây là hard gate ở bước validation, dùng để chặn skill mô tả còn placeholder.

### 6. `dependencies.allowed_imports`

Đây là allowlist cho một bộ third-party import detector dạng regex.

Tác dụng:

- chạy trong `_validate_no_external_dependencies()`
- nếu code import package ngoài stdlib và package đó không nằm trong allowlist này thì validation fail
- nếu package nằm trong allowlist thì validator bỏ qua package đó

Giới hạn hiện tại:

- detector chỉ bắt một số package đã hardcode
- đây không phải dependency sandbox thật
- nó không đảm bảo chặn hết dynamic import

Nói ngắn gọn: field này là van nới lỏng rule dependency ở lớp static validation.

### 7. `dependencies.forbidden_files`

Đây là danh sách file path không được xuất hiện trong generated skill package.

Tác dụng:

- chạy trong `_validate_no_external_dependencies()`
- nếu generated files chứa path nằm trong danh sách này thì validation fail

Ví dụ thường dùng:

- `requirements.txt`
- `pyproject.toml`
- `setup.py`

Mục đích kiến trúc:

- giữ skill package theo dạng local artifact đơn giản
- tránh đưa package manager / dependency manifest vào publish path

### 8. `capability.operation_taxonomy`

Đây là bộ verb chuẩn cho metadata capability, ví dụ `read`, `write`, `search`, `summarize`.

Tác dụng:

- chạy trong `_validate_capability_metadata()`
- validator dùng taxonomy này để kiểm tra `metadata.supported_actions` và `metadata.forbidden_actions`
- action ngoài taxonomy hiện chỉ tạo warning, chưa fail build

Vai trò trong architecture:

- chuẩn hóa metadata trước khi skill đi vào runtime
- giúp `select_skill()`, `check_capability()`, và các quyết định policy có metadata nhất quán hơn để dựa vào

Nó là semantic normalization layer, không phải runtime enforcement trực tiếp.

### 9. `capability.allowed_side_effects`

Đây là danh sách side effect hợp lệ cho `metadata.side_effects`.

Tác dụng:

- chạy trong `_validate_capability_metadata()`
- nếu skill khai báo side effect ngoài danh sách này thì validation fail

Ví dụ side effect thường có:

- `file_read`
- `file_write`
- `file_delete`
- `network`
- `subprocess`

Vai trò trong architecture:

- chuẩn hóa phần rủi ro của metadata
- giúp runtime/review hiểu skill có thể tác động tới filesystem, network, hoặc process hay không

Field này là hard gate cho giá trị metadata, nhưng chưa phải runtime sandbox permission thật.

### 10. `code_safety.risky_patterns`

Phần này đang hiển thị read-only trên UI.

Tác dụng:

- chạy trong `validate_code_safety()`
- quét regex trên các file `.py`
- rule có severity `error` sẽ fail validation
- rule có severity `warning` sẽ chỉ thêm warning

Vai trò kiến trúc:

- đây là lớp code lint/risk scan nằm giữa `Generator` và `SandboxRunner`
- nó giúp chặn một số pattern nguy hiểm trước khi skill được chạy thử

Giới hạn:

- đây là regex scan MVP
- không phải AST validator hoàn chỉnh
- không phải security boundary tuyệt đối

Muốn sửa phần này, hãy đổi YAML rồi nạp lại bằng `Policy YAML Path`.

## Những Field Có Trong Policy Nhưng Chưa Có Hoặc Chưa Dùng Thật Trên UI

Một số phần có trong `ValidationPolicy` nhưng hiện chưa phải luồng enforce đầy đủ:

### `activation.require_domain`

- có trong schema/YAML
- không có control riêng trên tab `Config`
- theo code hiện tại, validator vẫn luôn đòi `metadata.domain` dù flag này có bật hay không

### `capability.action_side_effect_hints`

- có trong schema/YAML
- không có control trên UI
- hiện chưa được validator dùng để suy luận hoặc block build

### `package.*`

- có trong schema/YAML
- hiện chưa có package validator thật để enforce layout, size, hay path policy

### `prompt_eval.*`

- có trong schema/YAML
- chưa có prompt eval runner
- chưa tham gia publish gate

### `review.*`

- có trong schema/YAML
- chưa được dùng để quyết định `requires_review`
- human review hiện do flow agent/UI điều khiển, không phải do policy YAML này điều khiển trực tiếp

## Kết Luận Thực Dụng

Nếu nhìn theo kiến trúc hiện tại, tab `Config` chủ yếu là UI để chỉnh phần policy của `StaticValidator`.

Bạn nên hiểu các field thành 3 nhóm:

- hard gate: `min_description_chars`, `forbidden_placeholder_patterns`, `forbidden_files`, `allowed_imports`, `allowed_side_effects`, `code_safety` với severity `error`
- warning/heuristic: `max_description_chars`, `require_action_verb`, `operation_taxonomy` với action ngoài taxonomy
- config/planned nhưng chưa enforce đầy đủ: `require_domain`, `action_side_effect_hints`, `package`, `prompt_eval`, `review`

Nếu cần thay đổi hành vi ở runtime execution thật sự, chỉ sửa tab `Config` là chưa đủ. Lúc đó phải nhìn thêm vào:

- `PolicyEngine`
- `check_capability()`
- `SandboxRunner`
- luồng review trong `SkillChatAgent` và `InteractionGateway`
