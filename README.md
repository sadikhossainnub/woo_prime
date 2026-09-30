### Woo Prime

Woocommerce intregation application for any wordpress website for sales order and itme push

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app woo_prime
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/woo_prime
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade
### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### Configuration & Setup Guide

#### 1. Security & Shared Secret Setup
- In **Woo Settings** in ERPNext, set an **API Shared Secret** token (click **Generate Shared Secret** to generate a random 40-character token).
- In WordPress Admin → **WooCommerce → Woo Prime ERPNext**, paste the same token into the **Shared Secret** field.
- All live requests (`calculate_cart_price`, `get_customer_loyalty_points`, `get_items_for_sync`, `get_dashboard_stats`) will send the header `X-Woo-Prime-Token` for authentication.

#### 2. Cloudflare Setup Guide (If your WooCommerce site uses Cloudflare)
If Cloudflare WAF or Bot Protection intercepts API calls between ERPNext and WooCommerce:
1. Go to **Cloudflare Dashboard → Security → WAF → Custom Rules**.
2. Add a **Skip Rule** scoped to your ERPNext server's IP address:
   ```text
   (http.host eq "shop.yourdomain.com" and starts_with(http.request.uri.path, "/wp-json/") and ip.src in {<server_IPv4> <server_IPv6_/64>})
   ```
   *Action:* **Skip** (WAF components, Bot Fight Mode, Browser Integrity Check).
   *Note:* Ensure both IPv4 and IPv6 `/64` ranges of your ERPNext server are included.
3. You can also customize the **API User Agent** in Woo Settings (Default: `curl/8.5.0`).

#### 3. Webhook Secret Requirement
- In ERPNext Woo Settings, set a **Webhook Secret**.
- Copy the **Webhook Delivery URL** from ERPNext.
- In WooCommerce Admin → **Settings → Advanced → Webhooks**, create a webhook for Order events, set Delivery URL, and paste the same Webhook Secret.
- Webhook signature verification operates on a *fail-closed* policy; requests without a configured secret will be rejected for security.

### License

mit

