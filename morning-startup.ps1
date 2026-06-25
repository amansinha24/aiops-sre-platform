@'
# AIOps Platform - Morning Startup Script
# Run from: D:\Aiops-Projects\aiops-sre-platform

Write-Host "Step 1: Applying Terraform..." -ForegroundColor Green
Set-Location terraform\environments\dev
terraform apply -auto-approve

Write-Host "Step 2: Configuring kubectl..." -ForegroundColor Green
aws eks update-kubeconfig --region ap-south-1 --name aiops-dev

Write-Host "Step 3: Applying namespaces..." -ForegroundColor Green
Set-Location D:\Aiops-Projects\aiops-sre-platform
kubectl apply -f kubernetes\namespaces\application.yaml
kubectl apply -f kubernetes\namespaces\observability.yaml
kubectl apply -f kubernetes\namespaces\aiops.yaml

Write-Host "Step 4: Installing Argo CD..." -ForegroundColor Green
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

Write-Host "Waiting for Argo CD pods..." -ForegroundColor Yellow
Start-Sleep -Seconds 60
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=120s

Write-Host "Step 5: Applying Argo CD apps..." -ForegroundColor Green
kubectl apply -f argocd\projects\sre-platform.yaml
kubectl apply -f argocd\applications\application.yaml

Write-Host "Step 6: Installing EBS CSI Driver..." -ForegroundColor Green
aws eks create-addon `
    --cluster-name aiops-dev `
    --addon-name aws-ebs-csi-driver `
    --service-account-role-arn arn:aws:iam::350480401763:role/AmazonEKS_EBS_CSI_DriverRole `
    --region ap-south-1 2>$null

Write-Host "Waiting for EBS CSI Driver..." -ForegroundColor Yellow
Start-Sleep -Seconds 60

Write-Host "Step 7: Installing Prometheus..." -ForegroundColor Green
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts 2>$null
helm repo add grafana https://grafana.github.io/helm-charts 2>$null
helm repo update

helm install prometheus prometheus-community/kube-prometheus-stack `
    --namespace observability `
    --values observability\prometheus\values.yaml `
    --set prometheusOperator.admissionWebhooks.enabled=false `
    --set prometheusOperator.admissionWebhooks.patch.enabled=false `
    --set prometheusOperator.tls.enabled=false `
    --timeout 10m 2>$null

Write-Host "Step 8: Installing Loki..." -ForegroundColor Green
helm install loki grafana/loki-stack `
    --namespace observability `
    --values observability\loki\values.yaml `
    --timeout 10m 2>$null

Write-Host "Step 9: Applying alert rules..." -ForegroundColor Green
kubectl apply -f observability\prometheus\alerts\pod-alerts.yaml

Write-Host "Step 10: Verifying everything..." -ForegroundColor Green
kubectl get pods -n application
kubectl get pods -n observability
kubectl get pods -n argocd

Write-Host "Platform is ready! Starting Phase 8..." -ForegroundColor Green
'@ | Set-Content -Path "D:\Aiops-Projects\aiops-sre-platform\scripts\setup\morning-startup.ps1" -Encoding UTF8