# Hướng dẫn sử dụng Config tab

File này ghi lại cách tab `Config` trong `app_gradio.py` đang hoạt động.

Giải thích: tab này dùng để chỉnh `ValidationPolicy` cho quá trình generate/publish skill.  
Nó chủ yếu ảnh hưởng đến bước **static validation**, không phải toàn bộ runtime policy.

## Luồng hiện tại

Theo architecture hiện tại, tab `Config` nằm trong nhánh build/publish skill:

```text
Config Tab
-> ValidationPolicyLoader / UI overrides
-> ValidationPolicy
-> SkillChatAgent
-> build_skill_from_spec
-> StaticValidator
-> SandboxRunner
-> PublishGateway
````

Giải thích:

* UI config tạo ra một `ValidationPolicy`
* `SkillChatAgent` dùng policy này khi build skill
* `StaticValidator` đọc policy để check metadata, activation, dependency, code safety
* nếu static validation pass thì mới chạy sandbox
* nếu sandbox pass thì mới publish

Điểm dễ nhầm:

* tab `Config` không điều khiển trực tiếp `PolicyEngine`
* metadata được validate ở đây sẽ ảnh hưởng về sau, vì runtime dùng metadata để chọn skill, check capability, hiển thị risk/review context

## Cách dùng nhanh

Chạy app:

```bash
uv run python app_gradio.py
```

Vào tab `Config`.

Nếu muốn load policy từ YAML:

```text
policies/mvp-safe.yaml
```

Sau đó bấm:

```text
Load Policy File
```

Chỉnh field trên form rồi bấm:

```text
Apply UI Overrides
```

Từ lúc đó trở đi, các lần build skill tiếp theo sẽ dùng policy mới.

Nếu muốn quay lại policy đang active thì bấm:

```text
Reset Form To Active
```

Lưu ý: đổi config không tự chạy lại validation cho skill cũ. Nó chỉ ảnh hưởng các lần build/validate sau.

---

# Các field đang dùng thật

## `Policy YAML Path`

Dùng để load toàn bộ `ValidationPolicy` từ file YAML.

Nó làm 3 việc:

* đọc policy từ file
* đổ dữ liệu policy lên form
* cập nhật active policy cho các lần build skill tiếp theo

Nó không tự validate skill. Nó chỉ đổi cấu hình.

---

## `activation.min_description_chars`

Rule này check `skill.metadata.description`.

Nếu description ngắn hơn số này thì fail static validation.

Đây là hard gate.

Nói dễ hiểu: description quá sơ sài thì skill không được chạy tiếp sang sandbox.

---

## `activation.max_description_chars`

Cũng check `skill.metadata.description`.

Hiện tại nếu description dài hơn ngưỡng này thì chỉ warning, chưa fail.

Đây là quality warning, không phải hard gate.

---

## `activation.require_action_verb`

Rule này check description có vẻ “mang tính hành động” hay không.

Ví dụ description nên có kiểu:

```text
create note
summarize file
validate config
search documents
```

Hiện tại rule này chỉ là heuristic. Nếu không match thì thêm warning, chưa fail.

Bản chất là một check đơn giản để tránh description quá mơ hồ.

---

## `activation.forbidden_placeholder_patterns`

Danh sách regex cấm trong description.

Ví dụ:

```text
TODO
FIXME
PLACEHOLDER
<something>
```

Nếu description match các pattern này thì fail validation.

Đây là hard gate.

Mục đích: chặn skill còn đang dở, chưa được generate sạch.

---

## `dependencies.allowed_imports`

Đây là allowlist cho third-party imports.

Nếu skill import package ngoài stdlib mà không nằm trong allowlist thì fail validation.

Hạn chế:

* đây chỉ là static check
* hiện tại detector chưa bắt được mọi kiểu import
* dynamic import vẫn có thể bypass nếu chỉ scan bằng regex

Ví dụ:

```python
__import__("requests")
```

```python
import importlib
importlib.import_module("requests")
```

---

## `dependencies.forbidden_files`

Danh sách file không được xuất hiện trong generated skill package.

Ví dụ:

```text
requirements.txt
pyproject.toml
setup.py
```

Nếu generated skill có mấy file này thì fail validation.

Mục tiêu hiện tại: giữ skill package đơn giản, không để skill tự mang dependency vào publish path.

```text
./requirements.txt
scripts/../requirements.txt
Requirements.txt
pyproject.TOML
```

---

## `capability.operation_taxonomy`

Danh sách action chuẩn cho metadata.

Ví dụ:

```text
read
write
search
summarize
validate
convert
```

Validator dùng nó để check:

```text
metadata.supported_actions
metadata.forbidden_actions
```

Nếu action nằm ngoài taxonomy thì hiện tại chỉ warning, chưa fail.

Vai trò chính: chuẩn hóa metadata để runtime dễ hiểu hơn.

---

## `capability.allowed_side_effects`

Danh sách side effect hợp lệ.

Ví dụ:

```text
file_read
file_write
file_delete
network
subprocess
```

Nếu skill khai side effect ngoài danh sách này thì fail validation.

---

## `code_safety.risky_patterns`

Phần này hiện đang read-only trên UI.

Nó dùng để scan các file `.py` bằng regex.

Rule có:

```text
severity: error
```

thì fail validation.

Rule có:

```text
severity: warning
```

thì chỉ warning.

Ví dụ rule có thể bắt:

```python
eval(...)
```

```python
os.system(...)
```

```python
subprocess.run(...)
```

Các kiểu bypass có thể có:

```python
from os import system
system("ls")
```

```python
import subprocess as sp
sp.run(...)
```

```python
__import__("subprocess").run(...)
```

---

# Các field có trong policy nhưng chưa dùng đầy đủ

## `activation.require_domain`

Có trong schema/YAML.

Nhưng hiện tại UI chưa có control riêng.

Theo flow hiện tại, validator vẫn đang yêu cầu `metadata.domain`. Flag này chưa thực sự điều khiển behavior rõ ràng.

Implementation chưa hoàn chỉnh.

---

## `capability.action_side_effect_hints`

Có trong schema/YAML.

Ví dụ:

```yaml
file_write:
  - create
  - write
  - append
```

Ý tưởng là: nếu skill khai action `write` mà không khai `file_write`, validator có thể warning.

Nhưng hiện tại phần này chưa được dùng đầy đủ để suy luận/block build.

Nên coi là TODO.

---

## `package.*`

Có trong YAML/schema.

Ví dụ:

```text
allowed_top_level_paths
forbidden_paths
max_file_size_bytes
max_skill_md_chars
```

Khi implement, nó sẽ check:

* path traversal
* absolute path
* hidden file
* cache folder
* file quá lớn
* folder ngoài layout cho phép

---

## `prompt_eval.*`

Có trong policy nhưng hiện chưa có prompt eval runner.

Nó chưa tham gia publish gate thật.

Về sau phần này nên dùng để test skill bằng prompt thực tế, không chỉ test script.

Ví dụ:

```text
User prompt -> skill selection -> skill execution/use -> final answer -> judge
```

Hiện tại nên coi là future config.

---

---

# Chia nhóm field cho dễ nhớ

## Hard gate

Các field này đang hoặc nên fail validation nếu vi phạm:

```text
activation.min_description_chars
activation.forbidden_placeholder_patterns
dependencies.forbidden_files
dependencies.allowed_imports
capability.allowed_side_effects
code_safety rule có severity=error
```

## Warning 

Các field này chủ yếu giúp nâng quality, chưa nên coi là tuyệt đối:

```text
activation.max_description_chars
activation.require_action_verb
capability.operation_taxonomy với action ngoài taxonomy
code_safety rule có severity=warning
```

## Có config nhưng chưa enforce đầy đủ

Các phần này nên document rõ là planned/partial:

```text
activation.require_domain
capability.action_side_effect_hints
package.*
prompt_eval.*
review.*
```

---

