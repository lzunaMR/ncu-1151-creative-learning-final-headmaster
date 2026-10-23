# 校長 AI 決策助手：Windows 一鍵 demo
#
# 用法（在 repo 根目錄的 PowerShell 執行）：
#   .\demo.ps1               # 版本 A（預設）
#   .\demo.ps1 -Version B    # 版本 B（向量＋BM25）
#   .\demo.ps1 -Stop         # 關閉
# 若出現「已停用指令碼執行」：powershell -ExecutionPolicy Bypass -File .\demo.ps1
#
# 會做的事：檢查 Docker 與 Ollama → 讓 Ollama 接受容器連線 → 下載缺少的模型
#           → docker compose up --build → 等待就緒 → 開啟瀏覽器

param(
    [ValidateSet("A", "B")] [string]$Version = "A",
    [switch]$Stop
)
# 不用 Stop：Windows PowerShell 5.1 會把原生指令（docker、ollama）寫到 stderr 的進度訊息當成錯誤
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$Models = @("qwen2.5:3b", "qwen2.5:0.5b", "bge-m3")

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`n[錯誤] $msg" -ForegroundColor Red; exit 1 }

if ($Stop) { docker compose down; exit 0 }

Step "檢查 Docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "找不到 docker，請先安裝並開啟 Docker Desktop：https://www.docker.com/products/docker-desktop/" }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker 沒有在執行，請先開啟 Docker Desktop，等它顯示 Engine running 後再執行一次。" }

Step "檢查 Ollama"
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
if (-not (Test-Path $ollama)) { Fail "找不到 Ollama，請先安裝：https://ollama.com/download" }

function Test-Ollama { try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 3 | Out-Null; $true } catch { $false } }
function Start-Ollama {
    Get-Process "ollama*" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not (Test-Ollama); $i++) { Start-Sleep 1 }
}

# 容器要透過 host.docker.internal 連進來，Ollama 預設只聽 127.0.0.1 會連不到
$listen = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
$onlyLocal = $listen -and -not ($listen | Where-Object { $_.LocalAddress -in @("0.0.0.0", "::") })
if (-not (Test-Ollama) -or $onlyLocal) {
    if ($onlyLocal -or [Environment]::GetEnvironmentVariable("OLLAMA_HOST", "User") -ne "0.0.0.0") {
        Write-Host "    設定 OLLAMA_HOST=0.0.0.0（使用者環境變數），讓容器能連到 Ollama"
        [Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0", "User")
    }
    $env:OLLAMA_HOST = "0.0.0.0"
    Write-Host "    重新啟動 Ollama…"
    Start-Ollama
}
if (-not (Test-Ollama)) { Fail "Ollama 啟動失敗，請手動開啟 Ollama 後再執行一次。" }
Write-Host "    Ollama 正常"

Step "檢查模型"
$have = (Invoke-RestMethod http://127.0.0.1:11434/api/tags).models.name
foreach ($m in $Models) {
    if ($have -contains $m -or $have -contains "$($m):latest") { Write-Host "    $m 已安裝" }
    else { Write-Host "    下載 $m（只有第一次需要）"; & $ollama pull $m; if ($LASTEXITCODE -ne 0) { Fail "下載 $m 失敗" } }
}

Step "建置並啟動容器（檢索版本 $Version）"
$env:RETRIEVER_VERSION = $Version
$t0 = Get-Date
docker compose up -d --build --force-recreate
if ($LASTEXITCODE -ne 0) { Fail "docker compose 失敗，請看上方訊息。" }

Step "等待服務就緒"
$ready = $false
for ($i = 0; $i -lt 200; $i++) {
    try {
        $h = Invoke-RestMethod http://localhost:8000/health -TimeoutSec 3
        Invoke-WebRequest http://localhost:8501 -UseBasicParsing -TimeoutSec 3 | Out-Null
        $ready = $true; break
    } catch { Start-Sleep 3 }
}
if (-not $ready) { docker compose logs --tail 40 api; Fail "等候逾時，以上是 api 的 log。" }
$sec = [int]((Get-Date) - $t0).TotalSeconds
if (-not $h.ollama.ready) {
    Write-Host "`n[注意] API 回報 Ollama 狀態：$($h.ollama | ConvertTo-Json -Compress)" -ForegroundColor Yellow
    Write-Host "       請看 docker compose logs api 的 [ollama] 訊息。" -ForegroundColor Yellow
}

Write-Host "`n完成（$sec 秒）。" -ForegroundColor Green
Write-Host "  介面：http://localhost:8501"
Write-Host "  API ：http://localhost:8000/docs"
Write-Host "  切換版本：.\demo.ps1 -Version B　　關閉：.\demo.ps1 -Stop"
Start-Process "http://localhost:8501"
