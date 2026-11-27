# CivicBid Google onboarding

This folder is the source for a private Google Apps Script project. It uses a
custom Apps Script web form rather than a standard Google Form so it can create
one unique Razorpay subscription link and send the customer straight to checkout.

## What it creates

Running `setupCivicBid` once creates a private Google Sheet with these tabs:

- `Onboarding`: every submitted request and payment state.
- `Subscribers`: only profiles enabled after Razorpay reports `active`.
- `Read me`: operating notes.

It also creates an hourly `reconcilePayments` trigger. Google may run the trigger
near, rather than exactly on, the hour.

## Install before payment KYC completes

1. Go to [script.new](https://script.new) while signed in as `rakshitbhatt@gmail.com`.
2. Create files named `Code.gs`, `Index.html`, and `appsscript.json`, copying the
   matching source from this folder. Do not paste secrets in source files.
3. Run `setupCivicBid` from the Apps Script editor and approve Google's requested
   permissions. Keep the returned Sheet URL and `SYNC_TOKEN` private.
4. Deploy the script as a **Web app**. It must execute as you and allow access to
   **Anyone** so potential CivicBid customers can use the public form. Use the
   deployed `/exec` URL, not the editor or `/dev` URL.

The public form will safely save a request before Razorpay has been configured,
but it must not be advertised until payment setup is complete.

## Configure after CivicBid Razorpay KYC is approved

Create the £4.99 GBP monthly plan in the new CivicBid Razorpay merchant account.
Then add these Apps Script **Project Settings → Script properties**:

| Property | Value |
| --- | --- |
| `RAZORPAY_KEY_ID` | CivicBid live API Key ID |
| `RAZORPAY_KEY_SECRET` | CivicBid live API Key Secret |
| `RAZORPAY_PLAN_ID` | The CivicBid £4.99/month Razorpay plan ID |

Never put these values in the Sheet, source code, GitHub, or chat.

## Connect the existing daily alert worker

Add these GitHub Actions secrets after deployment:

| Secret | Value |
| --- | --- |
| `TENDERSIGNAL_SUBSCRIBERS_ENDPOINT` | Apps Script `/exec` URL |
| `TENDERSIGNAL_SUBSCRIBERS_TOKEN` | `SYNC_TOKEN` shown by `setupCivicBid` |

The existing GitHub Action will fetch only active profiles over HTTPS. A
cancelled, expired, halted or unpaid subscription is excluded automatically.
