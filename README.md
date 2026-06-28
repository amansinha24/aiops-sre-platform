<div align="center">

# 🤖 AIOps SRE Platform

### Autonomous Incident Detection • AI-Powered Root Cause Analysis • Safe Auto-Remediation

[![AWS](https://img.shields.io/badge/AWS-EKS%20%7C%20Bedrock%20%7C%20ECR-FF9900?style=for-the-badge&logo=amazonaws&logoColor=white)](https://aws.amazon.com)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-1.31-326CE5?style=for-the-badge&logo=kubernetes&logoColor=white)](https://kubernetes.io)
[![Terraform](https://img.shields.io/badge/Terraform-IaC-7B42BC?style=for-the-badge&logo=terraform&logoColor=white)](https://terraform.io)
[![Claude](https://img.shields.io/badge/Claude-Amazon%20Bedrock-FF6B35?style=for-the-badge&logo=anthropic&logoColor=white)](https://aws.amazon.com/bedrock)
[![FastAPI](https://img.shields.io/badge/FastAPI-SRE%20Agent-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Grafana](https://img.shields.io/badge/Grafana-Observability-F46800?style=for-the-badge&logo=grafana&logoColor=white)](https://grafana.com)

> **Reducing MTTR from 45 minutes to under 3 minutes using AI-powered autonomous remediation**

</div>

---

## What Is This?

A production-grade AIOps platform built on AWS EKS that autonomously:

- **Detects** Kubernetes failures using K8sGPT (CrashLoopBackOff, OOMKilled, bad deployments)
- **Analyzes** failures using Claude 3 on Amazon Bedrock — generating root cause analysis with confidence scores
- **Remediates** automatically when confidence ≥ 70% and action is safe (restart or rollback)
- **Visualizes** the complete incident lifecycle in a custom dashboard and Grafana

---

## Architecture

Developer → GitHub Actions → Amazon ECR → Argo CD → AWS EKS

│

┌──────────────────────────┤

│                          │

Application Namespace      Observability Namespace

Flask + FastAPI + PostgreSQL  Prometheus + Grafana + Loki

│

AIOps Namespace

K8sGPT + SRE Agent (FastAPI)

│

Amazon Bedrock (Claude 3 Haiku)

**Incident Lifecycle:**

Failure → K8sGPT detects → Claude analyzes → Decision gate → Remediation

0s          <30s              <5s          confidence≥70%   restart/rollback

---

## Tech Stack

| Category | Technologies |
|----------|-------------|
| Infrastructure | AWS EKS, Terraform, VPC, ECR, IAM/IRSA |
| GitOps | GitHub Actions, Argo CD, Helm |
| Observability | Prometheus, Grafana, Loki, AlertManager |
| AIOps | K8sGPT Operator, Amazon Bedrock, Claude 3 Haiku |
| Application | FastAPI, Flask, PostgreSQL, Python 3.11 |
| Security | IRSA, Private subnets, Least privilege IAM |

---

## Prerequisites

Install these tools before starting:

```bash
# Required
aws --version          # AWS CLI v2
terraform --version    # >= 1.6.0
kubectl version        # compatible with EKS 1.31
helm version           # >= 3.x
git --version
docker --version
python3 --version      # >= 3.11
```

**AWS Requirements:**
- AWS account with programmatic access
- AWS CLI configured (`aws configure`)
- Amazon Bedrock model access enabled for Claude 3 Haiku
  - Go to: AWS Console → Bedrock → Model access → Enable Claude 3 Haiku

---

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/amansinha24/aiops-sre-platform.git
cd aiops-sre-platform
```

### 2. Create Terraform State Backend

```bash
# Create S3 bucket for state (replace YOUR_ACCOUNT_ID)
aws s3api create-bucket \
    --bucket aiops-terraform-state-YOUR_ACCOUNT_ID \
    --region ap-south-1 \
    --create-bucket-configuration LocationConstraint=ap-south-1

aws s3api put-bucket-versioning \
    --bucket aiops-terraform-state-YOUR_ACCOUNT_ID \
    --versioning-configuration Status=Enabled

# Create DynamoDB table for state locking
aws dynamodb create-table \
    --table-name aiops-terraform-locks \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region ap-south-1
```

### 3. Configure Terraform Backend

Edit `terraform/environments/dev/backend.tf`:

```hcl
terraform {
  backend "s3" {
    bucket       = "aiops-terraform-state-YOUR_ACCOUNT_ID"
    key          = "dev/terraform.tfstate"
    region       = "ap-south-1"
    use_lockfile = true
    encrypt      = true
  }
}
```

### 4. Deploy Infrastructure

```bash
cd terraform/environments/dev
terraform init
terraform apply
```

This creates:
- VPC with public and private subnets
- EKS cluster (v1.31)
- Node group (2x nodes)
- ECR repositories (frontend, api, sre-agent)

Takes approximately 15 minutes.

### 5. Configure kubectl

```bash
aws eks update-kubeconfig --region ap-south-1 --name aiops-dev
kubectl get nodes  # verify both nodes are Ready
```

### 6. Install EBS CSI Driver

Required for PostgreSQL persistent storage:

```bash
# Create IAM role for EBS CSI (see docs/architecture/ for trust policy)
aws eks create-addon \
    --cluster-name aiops-dev \
    --addon-name aws-ebs-csi-driver \
    --addon-version v1.36.0-eksbuild.1 \
    --service-account-role-arn arn:aws:iam::YOUR_ACCOUNT_ID:role/AmazonEKS_EBS_CSI_DriverRole \
    --region ap-south-1
```

### 7. Add GitHub Secrets

Go to your GitHub repo → Settings → Secrets → Actions and add:

AWS_ACCESS_KEY_ID      → your AWS access key

AWS_SECRET_ACCESS_KEY  → your AWS secret key

AWS_REGION             → AWS Region of your choice

### 9. Install Argo CD

```bash
kubectl create namespace argocd
kubectl apply -n argocd \
    -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Apply project and applications
kubectl apply -f argocd/projects/sre-platform.yaml
kubectl apply -f argocd/applications/application.yaml
kubectl apply -f argocd/applications/sre-agent.yaml
```

### 10. Apply Kubernetes Namespaces

```bash
kubectl apply -f kubernetes/namespaces/application.yaml
kubectl apply -f kubernetes/namespaces/observability.yaml
kubectl apply -f kubernetes/namespaces/aiops.yaml
```

### 11. Install Observability Stack

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

# Prometheus + Grafana + AlertManager
helm install prometheus prometheus-community/kube-prometheus-stack \
    --namespace observability \
    --values observability/prometheus/values.yaml \
    --set prometheusOperator.admissionWebhooks.enabled=false \
    --set prometheusOperator.admissionWebhooks.patch.enabled=false \
    --set prometheusOperator.tls.enabled=false

# Loki + Promtail
helm install loki grafana/loki-stack \
    --namespace observability \
    --values observability/loki/values.yaml

# Custom alert rules
kubectl apply -f observability/prometheus/alerts/pod-alerts.yaml
```

### 12. Install K8sGPT

```bash
helm repo add k8sgpt https://charts.k8sgpt.ai/
helm repo update

helm install k8sgpt-operator k8sgpt/k8sgpt-operator \
    --namespace k8sgpt \
    --create-namespace \
    --values k8sgpt/k8sgpt-values.yaml

kubectl apply -f k8sgpt/k8sgpt-config.yaml
```

### 13. Configure SRE Agent IAM Role

Create an IAM role for the SRE Agent to access Amazon Bedrock:

```bash
# Role name: aiops-sre-agent-role
# Permissions: AmazonBedrockFullAccess
# Trust: OIDC provider for your EKS cluster + aiops:sre-agent ServiceAccount

kubectl annotate serviceaccount sre-agent -n aiops \
    eks.amazonaws.com/role-arn=arn:aws:iam::YOUR_ACCOUNT_ID:role/aiops-sre-agent-role
```

### 14. Apply RBAC for SRE Agent

```bash
kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: sre-agent-role
rules:
  - apiGroups: ["core.k8sgpt.ai"]
    resources: ["results"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods", "pods/log", "events", "namespaces"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets"]
    verbs: ["get", "list", "watch", "patch", "update"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: sre-agent-rolebinding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: sre-agent-role
subjects:
  - kind: ServiceAccount
    name: sre-agent
    namespace: aiops
EOF
```

### 15. Verify Everything Is Running

```bash
kubectl get pods -n application    # frontend, api, db
kubectl get pods -n observability  # prometheus, grafana, loki
kubectl get pods -n k8sgpt         # k8sgpt-operator, k8sgpt-analyzer
kubectl get pods -n aiops          # sre-agent
```

---

## Accessing the Platform

**AIOps Incident Dashboard:**
```bash
kubectl port-forward svc/sre-agent-service <local-port>:8080 -n aiops
# Open: http://localhost:<local-port>/ui
```

**SRE Agent API Docs:**
```bash
# Open: http://localhost:<local-port>/docs
```

**Grafana:**
```bash
kubectl port-forward svc/prometheus-grafana <local-port>:80 -n observability
# Open: http://localhost:<local-port>
# Username: admin
# Password: aiops-admin-2024
```

**Argo CD:**
```bash
kubectl port-forward svc/argocd-server <local-port>:443 -n argocd
# Open: https://localhost:<local-port>
# Username: admin
# Password: kubectl -n argocd get secret argocd-initial-admin-secret \
#           -o jsonpath="{.data.password}" | base64 -d
```

---

## Failure Simulation

```bash
# Simulate CrashLoopBackOff
.\scripts\failures\simulate-crashloop.ps1

# Simulate OOMKilled
.\scripts\failures\simulate-oom.ps1

# Simulate bad deployment
.\scripts\failures\simulate-bad-deploy.ps1

# Full end-to-end demo
.\scripts\failures\full-demo.ps1
```

---

## Trigger AI Analysis

Once a failure is detected by K8sGPT:

```bash
# Check K8sGPT findings
kubectl get results -n k8sgpt

# Trigger SRE Agent analysis
curl -X POST http://localhost:<port>/analyze

# View incidents with Claude RCA
curl http://localhost:<port>/incidents
```

---

## Cost Estimate

| Resource | Monthly Cost |
|---------|-------------|
| EKS Control Plane | ~$72 |
| EC2 Nodes (2x) | ~$120 |
| NAT Gateway | ~$32 |
| Amazon Bedrock (dev usage) | ~$5-15 |
| ECR + S3 | ~$2 |
| **Total** | **~$230** |

> **Cost tip:** Run `terraform destroy` when not actively using. Rebuild takes ~15 minutes.

---

## Security Design

- **IRSA** — Pods assume IAM roles via OIDC, zero hardcoded credentials
- **Private subnets** — EKS nodes never directly exposed to internet
- **Least privilege** — Separate IAM roles per service with minimal permissions
- **Safe action allowlist** — AI can only execute pre-approved remediations
- **Confidence threshold** — Auto-remediation requires ≥70% AI confidence

---

## Project Structure

aiops-sre-platform/

├── terraform/                    # AWS Infrastructure

│   ├── modules/vpc/              # Networking

│   ├── modules/eks/              # Kubernetes cluster

│   ├── modules/ecr/              # Container registry

│   └── environments/dev/         # Dev environment

├── kubernetes/namespaces/        # Namespace definitions

├── helm/apps/sre-platform/       # Helm charts

├── app/                          # 3-tier application

│   ├── frontend/                 # Flask UI

│   ├── api/                      # FastAPI + chaos endpoints

│   └── db/                       # PostgreSQL scripts

├── sre-agent/                    # AIOps engine

│   └── src/

│       ├── main.py               # FastAPI + /ui dashboard

│       ├── services/

│       │   ├── bedrock_service.py

│       │   ├── k8sgpt_service.py

│       │   └── incident_service.py

│       └── utils/prompts.py

├── observability/                # Prometheus + Grafana + Loki

├── k8sgpt/                       # K8sGPT configuration

├── argocd/                       # GitOps applications

└── scripts/

├── setup/                    # Startup scripts

└── failures/                 # Failure simulation

---

<div align="center">

**Built by [Aman Sinha](https://github.com/amansinha24)**

*If this project helped you, please ⭐ star the repository!*

</div>

