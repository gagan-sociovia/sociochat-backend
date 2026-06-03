# Meta Developer Console — callback URLs after GCP deploy
#
# Replace BASE with your Cloud Run URL, e.g.:
#   https://sociochat-backend-v2-362038465411.europe-west1.run.app
#
# Run after deploy:
#   .\scripts\meta-developer-urls.ps1 -BaseUrl "https://YOUR-SERVICE.run.app"

param(
    [Parameter(Mandatory=$true)]
    [string]$BaseUrl
)

$Base = $BaseUrl.TrimEnd("/")

Write-Host ""
Write-Host "=== Meta App Dashboard (developers.facebook.com) ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. App Settings -> Basic" -ForegroundColor Yellow
Write-Host "   App Domains: $(($Base -replace 'https?://','' -split '/')[0])"
Write-Host ""
Write-Host "2. Facebook Login -> Settings" -ForegroundColor Yellow
Write-Host "   Valid OAuth Redirect URIs:"
Write-Host "     $Base/api/whatsapp/connect/callback"
Write-Host ""
Write-Host "3. WhatsApp -> Configuration" -ForegroundColor Yellow
Write-Host "   Callback URL (Webhook):"
Write-Host "     $Base/api/whatsapp/webhook"
Write-Host "   Verify Token: (same as WHATSAPP_VERIFY_TOKEN in Cloud Run env)"
Write-Host "   Webhook fields: messages, message_template_status_update"
Write-Host ""
Write-Host "4. WhatsApp -> Embedded Signup" -ForegroundColor Yellow
Write-Host "   No redirect URL change needed - frontend uses POST /api/whatsapp/connect/exchange"
Write-Host "   Ensure WHATSAPP_CONFIG_ID matches VITE_WHATSAPP_CONFIG_ID on frontend"
Write-Host ""
Write-Host "5. Cloud Run env vars to verify:" -ForegroundColor Yellow
Write-Host "   APP_BASE_URL=$Base"
Write-Host "   WEBHOOK_PUBLIC_URL=$Base"
Write-Host "   OAUTH_REDIRECT_BASE=$Base"
Write-Host "   META_APP_ID / FB_APP_ID = your Meta app ID"
Write-Host "   META_APP_SECRET / FB_APP_SECRET = your Meta app secret"
Write-Host "   WHATSAPP_VERIFY_TOKEN = must match Meta webhook verify token"
Write-Host ""
Write-Host "6. Frontend (.env.production):" -ForegroundColor Yellow
Write-Host "   VITE_API_BASE_URL=$Base"
Write-Host "   VITE_FB_APP_ID=<same as Meta app>"
Write-Host "   VITE_WHATSAPP_CONFIG_ID=<Embedded Signup config ID>"
Write-Host ""
Write-Host "7. Test endpoints:" -ForegroundColor Yellow
Write-Host "   GET  $Base/api/status"
Write-Host "   GET  $Base/api/whatsapp/onboarding/local-setup"
Write-Host ('   GET  {0}/api/whatsapp/health?workspace_id=YOUR_WORKSPACE_ID' -f $Base)
Write-Host ""
