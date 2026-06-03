# Local webhook tunnel for Meta WhatsApp testing
# 1. Install ngrok: https://ngrok.com/download
# 2. Run this script while backend is on http://localhost:5000
# 3. Copy the HTTPS URL into .env as WEBHOOK_PUBLIC_URL
# 4. Update Meta App Dashboard → WhatsApp → Configuration → Callback URL

$ErrorActionPreference = "Stop"

Write-Host "Starting ngrok tunnel to localhost:5000..." -ForegroundColor Cyan
Write-Host ""
Write-Host "After ngrok starts:" -ForegroundColor Yellow
Write-Host "  1. Copy the https://....ngrok-free.app URL"
Write-Host "  2. Set in backend/.env:  WEBHOOK_PUBLIC_URL=https://YOUR-NGROK-URL"
Write-Host "  3. Set in backend/.env:  APP_BASE_URL=https://YOUR-NGROK-URL  (for OAuth callback)"
Write-Host "  4. Meta Dashboard → WhatsApp → Webhook URL:"
Write-Host "     https://YOUR-NGROK-URL/api/whatsapp/webhook"
Write-Host "  5. Restart backend (python app.py)"
Write-Host "  6. Check: GET https://YOUR-NGROK-URL/api/whatsapp/onboarding/local-setup"
Write-Host ""

if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
    Write-Host "ngrok not found. Install from https://ngrok.com/download" -ForegroundColor Red
    Write-Host "Alternative: use Cloudflare Tunnel or VS Code devtunnels (already allowed in CORS)" -ForegroundColor Yellow
    exit 1
}

ngrok http 5000
