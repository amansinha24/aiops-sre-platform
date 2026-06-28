# simulate-oom.ps1
# Simulates OOMKilled by setting very low memory limits

param(
    [string]$Namespace = "application",
    [string]$AgentPort = "9997"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  FAILURE SIMULATION: OOMKilled          " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

Write-Host "`n[1/4] Deploying memory-hungry pod..." -ForegroundColor Yellow

kubectl apply -f D:\Aiops-Projects\aiops-sre-platform\scripts\failures\oom-pod.yaml

Write-Host "`n[2/4] Waiting for OOMKilled (up to 60s)..." -ForegroundColor Yellow
$attempts = 0
while ($attempts -lt 12) {
    $reason = kubectl get pod oom-sim -n $Namespace `
        -o jsonpath="{.status.containerStatuses[0].lastState.terminated.reason}" 2>$null
    Write-Host "  Termination reason: $reason"
    if ($reason -match "OOMKilled") {
        Write-Host "  OOMKilled detected!" -ForegroundColor Red
        break
    }
    Start-Sleep -Seconds 5
    $attempts++
}

Write-Host "`n[3/4] Checking K8sGPT findings..." -ForegroundColor Yellow
Start-Sleep -Seconds 30
kubectl get results -n k8sgpt

Write-Host "`n[4/4] Cleaning up..." -ForegroundColor Yellow
kubectl delete pod oom-sim -n $Namespace
Write-Host "  Done!" -ForegroundColor Green