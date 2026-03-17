# WhatsApp Business API Setup Guide

## Overview

This guide explains how to set up WhatsApp Business API integration for Sociovia using Meta's Embedded Signup flow. This allows any business to connect their WhatsApp Business Account through a simple OAuth flow.

---

## Prerequisites

### 1. Meta Business Account
- Create a Meta Business account at [business.facebook.com](https://business.facebook.com)
- Your Facebook account must have **Admin access** to the Business account

### 2. Meta Developer Account
- Create a developer account at [developers.facebook.com](https://developers.facebook.com)
- Link it to your Meta Business account

### 3. Phone Number
- A phone number that is **NOT currently registered on WhatsApp**
- The number will receive a verification code via SMS or voice call

---

## Step 1: Create a Meta App

1. Go to [developers.facebook.com/apps](https://developers.facebook.com/apps)
2. Click **"Create App"**
3. Select **"Business"** as the app type
4. Fill in:
   - App Name: `Sociovia WhatsApp`
   - App Contact Email: your email
   - Business Account: select your Meta Business account
5. Click **"Create App"**

---

## Step 2: Add Required Products

### Add WhatsApp
1. In your app dashboard, click **"Add Product"**
2. Find **"WhatsApp"** and click **"Set up"**
3. Follow prompts to complete basic setup

### Add Facebook Login
1. Click **"Add Product"** again
2. Find **"Facebook Login for Business"** and click **"Set up"**
3. Select **"Web"** as platform
4. Enter your OAuth callback URL:
   ```
   https://your-domain.com/api/whatsapp/oauth/callback
   ```

---

## Step 3: Configure App Settings

### Basic Settings
Go to **Settings > Basic** and note:
- **App ID** (you'll need this)
- **App Secret** (you'll need this - keep it secure!)

### Advanced Settings
Go to **Settings > Advanced**:
- Enable **"Require App Secret"** for security
- Set **"App Mode"** to **Live** for production

### WhatsApp Settings
Go to **WhatsApp > Configuration**:
1. Set the **Callback URL** for webhooks:
   ```
   https://your-domain.com/api/whatsapp/webhook
   ```
2. Set a **Verify Token** (you'll use this in your `.env`)
3. Subscribe to webhook fields:
   - `messages`
   - `message_template_status_update`

---

## Step 4: Configure Embedded Signup

Go to **WhatsApp > Embedded Signup**:
1. Enable Embedded Signup
2. Add your domain to **Allowed Domains**
3. Copy the **Config ID** (you'll need this)

---

## Step 5: Set Environment Variables

Add these to your `.env` file:

```env
# Meta App Configuration
META_APP_ID=your_app_id_here
META_APP_SECRET=your_app_secret_here
META_CONFIG_ID=your_config_id_here

# Webhook Configuration
WHATSAPP_WEBHOOK_VERIFY_TOKEN=your_custom_verify_token

# OAuth Configuration
WHATSAPP_OAUTH_REDIRECT_URI=https://your-domain.com/api/whatsapp/oauth/callback
FRONTEND_URL=https://your-domain.com
```

---

## Step 6: App Review (For Production)

For your app to be used by any Facebook user (not just admins/developers):

### Required Permissions
Submit these for review:
- `whatsapp_business_management`
- `whatsapp_business_messaging`
- `business_management`

### Review Process
1. Go to **App Review > Permissions and Features**
2. Request each permission
3. Provide:
   - Use case description
   - Video demonstration
   - Privacy policy URL
   - Terms of service URL

---

## Step 7: Business Verification (Recommended)

For higher messaging limits and full access:

1. Go to [business.facebook.com/settings](https://business.facebook.com/settings)
2. Navigate to **Security Center**
3. Click **"Start Verification"**
4. Provide:
   - Legal business name
   - Business address
   - Business documents (registration, utility bill, etc.)
   - Phone verification

---

## User Flow (How It Works)

### For End Users
1. User clicks **"Connect WhatsApp"** in Sociovia
2. Facebook login popup appears
3. User grants permissions
4. User selects/creates a WhatsApp Business Account
5. User provides a phone number for verification
6. Account is connected and ready to use!

### Technical Flow
```
User clicks Connect → Opens Meta Embedded Signup
                    → User logs in with Facebook
                    → Grants permissions
                    → Selects/creates WABA
                    → Meta returns code to callback
                    → Backend exchanges code for token
                    → Stores account in database
                    → User redirected to Settings with success
```

---

## Rate Limits & Tiers

| Tier | Daily Limit | Requirements |
|------|------------|--------------|
| Unverified | 250 | None |
| Tier 1 | 1,000 | Send 1K+ messages |
| Tier 2 | 10,000 | Send 2K+ messages |
| Tier 3 | 100,000 | Send 10K+ messages |
| Unlimited | Unlimited | High volume + good quality |

---

## Troubleshooting

### "App Not Live" Error
- Go to App Dashboard > App Review > Requests
- Make sure app mode is **Live**

### "Invalid Redirect URI" Error
- Check OAuth redirect URI matches exactly
- Ensure domain is in **Allowed Domains** for Embedded Signup

### "Permissions Denied" Error
- User must be Admin of the Meta Business account
- Check all required permissions are granted

### Webhook Not Receiving Events
- Verify callback URL is publicly accessible (HTTPS required)
- Check verify token matches `.env` configuration
- Ensure webhook subscriptions are enabled

---

## Useful Links

- [WhatsApp Cloud API Docs](https://developers.facebook.com/docs/whatsapp/cloud-api)
- [Embedded Signup Guide](https://developers.facebook.com/docs/whatsapp/embedded-signup)
- [Meta Business Help](https://www.facebook.com/business/help)
- [Developer Support](https://developers.facebook.com/support)

---

## Quick Checklist

- [ ] Meta Business account created
- [ ] Meta Developer app created
- [ ] WhatsApp product added to app
- [ ] Facebook Login product added to app
- [ ] Embedded Signup configured
- [ ] Environment variables set
- [ ] Webhook URL configured
- [ ] App set to Live mode
- [ ] (Production) App Review completed
- [ ] (Recommended) Business verification completed
