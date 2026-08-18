# Free deployment setup

## Stack selected

| Need | Choice | Cost to start |
| --- | --- | --- |
| Source control, scheduled worker, deployments | GitHub Free | Free for a public repository; check current plan limits before scale. |
| Landing page hosting | GitHub Pages | Free for public repositories on GitHub Free. |
| Worker runtime | GitHub Actions scheduled workflow | Uses the official workflow already included in this repository. |
| Database for delivery de-duplication | SQLite file committed only by the scheduled workflow | Free; suitable only for the initial pilot. |
| Email delivery | Any SMTP provider you own | Free trial/free tier may be suitable initially; compare deliverability and terms before choosing. |
| Payments | Stripe Payment Links later | No software installation is needed; transaction fees apply, so it is not a free payment processor. |

GitHub Pages supports public repositories on GitHub Free, and GitHub Actions workflows can be scheduled. See GitHub's official documentation: [Pages availability](https://docs.github.com/en/pages/getting-started-with-github-pages) and [workflow schedules](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows).

## What is installed locally

- Python 3.12 (official python.org package installed via Winget)
- Git (already present)
- GitHub CLI (official GitHub package installed via Winget)

The application uses only Python's standard library. There is no `pip install` step and no unverified third-party Python package.

## One-time setup you must perform

These steps require your personal accounts, so they cannot be completed safely without you signing in:

1. Run `& 'C:\Program Files\GitHub CLI\gh.exe' auth login`, select GitHub.com and sign in in the browser.
2. Create a public repository and push this project. The Pages workflow is ready to publish `site/` after each push to either `main` or `master`.
3. In GitHub: **Settings → Pages → Build and deployment → Source**, select **GitHub Actions**.
4. Create a dedicated sending mailbox or SMTP account. Add the five `TENDERSIGNAL_SMTP_*` secrets from `README.md`, plus `TENDERSIGNAL_UNSUBSCRIBE_ADDRESS`, under **Settings → Secrets and variables → Actions**. This unsubscribe address must be a mailbox you monitor.
5. Make a test customer profile from `config/subscribers.example.json`, replace the example recipient with your own address, and save the complete JSON array as the `TENDERSIGNAL_SUBSCRIBERS_JSON` Actions secret. Never commit it.
6. Manually run **TenderSignal monitor** from the Actions tab. Confirm delivery to yourself before adding customers.

## Before taking money

- The dedicated TenderSignal Gmail mailbox is wired into the public contact, privacy, support and unsubscribe links. Replace the remaining bracketed legal-identity placeholders before accepting customers.
- Add your legal business details to the privacy notice, terms and cancellation policy already prepared in `site/`, then obtain legal review before launch.
- Add a Stripe Payment Link only after these policies are reviewed for your business and location.
- Do not use this tool for unsolicited bulk email. Customer operational alerts should only go to subscribers who asked for them. When someone requests removal, change their profile `status` to `inactive` in the subscriber-secret JSON before the next scheduled run.

## Cost controls

Keep the repository public during the proof-of-demand phase if you want the GitHub Free Pages route. Review GitHub Actions usage before adding heavier jobs or private-repository hosting. Move the SQLite delivery state to a managed database only when the customer count or reliability requirement justifies it.
