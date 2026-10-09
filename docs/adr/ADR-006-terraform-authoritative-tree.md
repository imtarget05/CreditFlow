# ADR-006: infra/terraform/ Is the Authoritative Terraform Tree

## Status
Accepted

## Context
Repo từng có hai cây Terraform song song cho cùng tài nguyên Azure:

- `deploy/terraform/` — bản cũ (ACR admin credentials as secrets, replicas cố định 1, thiếu key-vault purge protection, thiếu storage wiring).
- `infra/terraform/` — bản mới hơn (User Assigned Identity cho Container Apps thay ACR admin password, `storage.tf`, `key_vault { purge_soft_delete_on_destroy = true }`, env `CREDITFLOW_ENV=production`, replicas lên 3).

Hai cây cùng sửa một resource group là drift thật: sửa một cây, cây kia im lặng sai. Audit repo hygiene 2026-10-06 xác nhận `git grep deploy/terraform` trả về 0 hit — không workflow, docs hay script nào phụ thuộc cây cũ.

Đồng thời `infra/bicep/main.bicep` từng bị xoá nhầm và đã được restore; nó là capability/evidence và KHÔNG được xoá chỉ vì Azure không phải production runtime canonical.

## Decision
1. `infra/terraform/` là Terraform IaC duy nhất có thẩm quyền (authoritative). Mọi thay đổi Terraform đi vào đây.
2. `deploy/terraform/` đã bị thay thế (superseded) và bị xoá trong commit `chore(infra): remove superseded terraform deployment tree`.
3. `deploy/scripts/deploy-azure.sh` GIỮ NGUYÊN — đây là deployment automation (Azure CLI), được `.github/workflows/deploy-azure.yml` gọi trực tiếp; nó không thuộc phạm vi Terraform và không bị ảnh hưởng.
4. **Không tuyên bố hạ tầng Terraform/Azure là "live" hay "deployed" trừ khi có runtime evidence** (state thật, endpoint trả lời, hoặc run deploy đã ghi nhận). Framing canonical: PRIVATE/ON-PREM là deployment architecture chính; demo plane = Azure Container Apps (live evidence 2026-10-07, `/health/live` 200) + Cloudflare Pages (`creditflow.pages.dev`) + Render mirror (`render.yaml`, API + Postgres). Terraform = capability / infrastructure-as-code evidence (đã superseded, xoá).

## Consequences
### Positive
- Một nguồn sự thật cho Terraform; hết drift giữa hai cây.
- Lock file (`infra/terraform/.terraform.lock.hcl`) được commit (`.gitignore` đã sửa: ignore `**/.terraform/`, `*.tfplan*`, crash logs — không ignore lock file) ⇒ reproduce được version provider.
- Ranh giới claim rõ: code IaC tồn tại ≠ hạ tầng đang chạy.

### Negative / Trade-offs
- Lịch sử thay đổi của cây Terraform cũ nằm trong git history, không còn trên cây hiện tại.
- Nếu sau này deploy Azure thật sự bằng Terraform, phải chứng minh bằng evidence trước khi đổi status ADR này.
