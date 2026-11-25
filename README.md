# TenderSignal UK

TenderSignal UK is a B2B tender-intelligence service. It monitors public procurement notices, scores opportunities against client profiles, and can email either high-relevance alerts or a daily all-category digest.

## Why this is the 30-day bet

This is not a passive-income guarantee. The practical goal for the first 30 days is to validate paid demand with 3–10 founding customers. A credible path to $10,000/month is 50 customers at roughly $199/month, not ad revenue or mass consumer traffic.

The service has four defensible pieces:

1. Public, structured source data from Find a Tender.
2. A client-specific relevance score, rather than a generic daily digest.
3. A concise bid/no-bid brief with a direct source link.
4. Subscription delivery and a repeatable outbound sales motion.

## Quick start

1. Copy `config/subscribers.example.json` to `config/subscribers.json` and add a test recipient.
2. Set the SMTP environment variables described below.
3. Run `python worker/tender_signal.py` on a machine with Python 3.11+.
4. Serve `site/` from any static host, replace the waitlist link, and begin selling founding memberships before automating scale.

The worker is deliberately dependency-free. It uses SQLite to avoid needing an external database for the first customers.

## Deployment

The repository is prepared for a no-cost starter deployment: GitHub Pages publishes the landing page from `site/`, while GitHub Actions runs the monitor on a schedule. Follow [DEPLOYMENT.md](DEPLOYMENT.md) for the small number of account-owned actions that remain.

### Email settings

Set these environment variables on the machine or in GitHub Actions secrets:

```
TENDERSIGNAL_SMTP_HOST=smtp.example.com
TENDERSIGNAL_SMTP_PORT=465
TENDERSIGNAL_SMTP_USER=alerts@example.com
TENDERSIGNAL_SMTP_PASSWORD=app-password
TENDERSIGNAL_SMTP_FROM=alerts@example.com
```

GitHub Actions also needs the same values as repository secrets. Do not commit `subscribers.json`, credentials, or customer data.

## 30-day execution plan

| Days | Outcome | Owner action |
| --- | --- | --- |
| 1–3 | Working demo and offer | Configure one niche (e.g. cyber-security consultancies), landing page, email sender, and 3 sample alert briefs. |
| 4–7 | 20 discovery conversations | Build a list of 100 UK firms with 5–50 people; send tailored messages asking whether missed tenders cost them deals. Do not bulk spam. |
| 8–14 | First 3 paid founders | Offer a 14-day paid pilot at £4.90/month; use their actual service keywords to prove alert relevance. |
| 15–21 | Repeatable acquisition | Publish two useful tender-analysis posts and add referral/partner outreach to bid writers and trade associations. |
| 22–30 | 10 paid customers / evidence | Add Stripe checkout, cancellation policy, privacy policy, and testimonials only from real customers. Improve conversion and retention before changing price. |

## Business model

- Founding tier: £4.90/month for one tailored profile and daily digest.
- Standard tier: £10.90/month for up to three tailored profiles.
- Agency tier: £19.90/month for up to ten separate customer or business-unit profiles.

At these low prices, the product must be largely self-service. The all-category scan is the shared data source; every paying customer receives a profile that can include any industry keywords, optional location preferences, and exclusions.

Do not claim that the service guarantees contracts. It identifies opportunities; the customer remains responsible for eligibility and submissions.

## Automation architecture

```
Find a Tender OCDS API -> filter + score -> SQLite de-duplication -> HTML email -> subscriber inbox
                                      ^
                              client profile rules
```

`worker/tender_signal.py` accepts only public notice metadata and sends an email once for each notice/subscriber pair. The default schedule checks every four hours.

### All-category digest profile

Use `"match_all": true`, `"delivery_mode": "daily_digest"`, and `"minimum_score": 0` in a subscriber profile to receive all new tender-stage notices as one digest each day. This avoids sending one email per notice while keeping categories and regions unrestricted.

## Before public launch

- Form a suitable business entity and obtain tax/accounting advice for your location.
- Use a legitimate email sender and comply with UK GDPR/PECR, including an unsubscribe path and privacy notice.
- Confirm current source-data terms and API behaviour before relying on them in production.
- Connect Stripe (or equivalent) only after you have a refund/cancellation policy and support email.

## Source data

Find a Tender provides UK procurement notice data, including an OCDS API and data made available under the Open Government Licence. See the official [developer documentation](https://www.find-tender.service.gov.uk/Developer/Documentation).
