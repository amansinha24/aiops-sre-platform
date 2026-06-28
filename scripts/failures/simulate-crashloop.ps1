# simulate-crashloop.ps1
# Simulates a CrashLoopBackOff failure and demonstrates AIOps detection

param(
    [string]$Namespace = "application",
    [string]$AgentPort = "9997"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  FAILURE SIMULATION: CrashLoopBackOff  " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Step 1: Deploy broken deployment
Write-Host "`n[1/5] Deploying broken deployment..." -ForegroundColor Yellow

kubectl apply -f D:\Aiops-Projects\aiops-sre-platform\scripts\failures\crashloop-deployment.yaml
# Step 2: Wait for crash
Write-Host "`n[2/5] Waiting for CrashLoopBackOff..." -ForegroundColor Yellow
$attempts = 0
while ($attempts -lt 12) {
    $status = kubectl get pods -n $Namespace -l app=crashloop-sim `
        --no-headers -o custom-columns="STATUS:.status.containerStatuses[0].state.waiting.reason" 2>$null
    Write-Host "  Pod status: $status"
    if ($status -match "CrashLoopBackOff|Error") {
        Write-Host "  CrashLoopBackOff detected!" -ForegroundColor Red
        break
    }
    Start-Sleep -Seconds 10
    $attempts++
}

# Step 3: Wait for K8sGPT
Write-Host "`n[3/5] Waiting for K8sGPT analysis (up to 60s)..." -ForegroundColor Yellow
$attempts = 0
while ($attempts -lt 6) {
    $results = kubectl get results -n k8sgpt --no-headers 2>$null
    if ($results -match "crashloop") {
        Write-Host "  K8sGPT finding created!" -ForegroundColor Green
        kubectl get results -n k8sgpt
        break
    }
    Write-Host "  Waiting for K8sGPT... ($($attempts * 10)s)"
    Start-Sleep -Seconds 10
    $attempts++
}

# Step 4: Trigger SRE Agent analysis
Write-Host "`n[4/5] Triggering AI analysis via SRE Agent..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod `
        -Uri "http://localhost:$AgentPort/analyze" `
        -Method POST
    
    Write-Host "`n  === AI ANALYSIS RESULT ===" -ForegroundColor Cyan
    Write-Host "  Incident: $($response.incidents[0].title)"
    Write-Host "  Severity: $($response.incidents[0].severity)"
    Write-Host "  Root Cause: $($response.incidents[0].rca.root_cause)"
    Write-Host "  Confidence: $($response.incidents[0].rca.confidence)%"
    Write-Host "  Action: $($response.incidents[0].rca.action_type)"
    Write-Host "  Auto-remediated: $($response.incidents[0].auto_remediated)"
} catch {
    Write-Host "  SRE Agent not reachable on port $AgentPort" -ForegroundColor Red
}

# Step 5: Cleanup
Write-Host "`n[5/5] Cleaning up simulation..." -ForegroundColor Yellow
kubectl delete deployment crashloop-sim -n $Namespace
Write-Host "  Cleanup complete!" -ForegroundColor Green

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  SIMULATION COMPLETE                    " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
