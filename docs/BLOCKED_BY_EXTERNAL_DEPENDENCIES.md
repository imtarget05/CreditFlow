# BLOCKED BY EXTERNAL DEPENDENCIES — O1 và O4

**Ngày:** 2026-11-04 · **Trạng thái:** Chờ môi trường và quyền truy cập thật  
**Nội dung:** Lý do cụ thể (không mơ hồ) tại sao 2 open item không thể close từ local repo.

---

## O1 — AKS live apply/sync không thể từ máy local

### Lý do chặn

1. **Cluster Azure nằm private-link / Private DNS.** Không có kubectl từ máy local vì không có đường mạng trực tiếp đến control plane AKS. Không có bastion / approved subnet để test connectivity.
2. **Terraform đã apply nhưng không verify ArgoCD sync.** Apply đã chạy trên Azure nhưng không thể xác nhận ArgoCD đã reconcile workload thật từ môi trường local. Không claim live Azure deployment.
3. **Ràng buộc `no_azure_production_mutations`.** Không được mutate Azure/production từ local. kind-local-platform là target duy nhất cho validation cục bộ.

### Cần gì để unblock

| Yêu cầu | Chi tiết |
|---------|----------|
| Tenant Azure thật | Cần subscription, resource group, và quyền quản trị AKS cluster |
| Quyền Private Link / Private DNS | Cần cấu hình private endpoint và DNS resolution cho AKS control plane |
| Approved subnet hoặc bastion | Cần chạy kubectl / ArgoCD sync từ bên trong VNet |
| Xác nhận cluster AKS private | Kiểm tra network policy, ingress, private endpoint |
| Test connectivity | Ping / telnet đến AKS API server từ bastion hoặc approved subnet |

### Không có cách “bypass” an toàn

Không thể giả lập private-link từ local. Không thể dùng kubeconfig giả để claim ArgoCD sync. Không thể skip NetworkPolicy validation trên Azure CNI.

---

## O4 — GitOps external validation chưa có evidence live

### Lý do chặn

1. **External repositories chưa checkout.** GitOps validator báo `NOT_PRESENT` cho 2 external repo (không tính fail, nhưng không có evidence live).
2. **Chưa có quyền vào tenant / GitOps repo / backup repo / infra repo.** Không thể chạy ArgoCD sync, drift reconciliation, backup/restore, DR drill.
3. **Chưa có owner vận hành rõ ràng theo vai trò.** Cần chỉ định owner cho monitoring, alerting, scaling, DR, NetworkPolicy, ArgoCD sync, Terraform state.

### Cần gì để unblock

| Yêu cầu | Chi tiết |
|---------|----------|
| Quyền vào external repos | Checkout 2 external repository cho GitOps validator |
| Quyền vào tenant | Azure tenant thật để quản lý ArgoCD Application |
| GitOps repo write | Cấu hình ArgoCD sync policy, drift reconciliation |
| Backup repo + infra repo | Backup/restore drill, DR drill, HPA / cluster-autoscaler |
| Terraform remote backend thực tế | AzureRM backend trên storage thực (không local) |
| Owner vận hành | Xác định owner rõ ràng cho mỗi vùng vận hành AKS |

### Không có cách “bypass” an toàn

Không thể fake external repo checkout. Không thể claim ArgoCD sync mà không có live cluster. Không thể claim DR drill mà không có backup thực.

---

## Kết luận

O1 và O4 là **external dependencies** — cần môi trường và quyền truy cập thật, không thể fix bằng code trong local repo. Không thêm code để “chạy qua” O1/O4 vì đó sẽ là fake/unsafe.

**Hành động khuyến nghị:** Nếu có quyền external tenant/repo → tiếp tục unblock O1/O4 thật. Nếu không → dừng và đóng bằng status “portfolio-ready, production blocked by external infra governance”.
