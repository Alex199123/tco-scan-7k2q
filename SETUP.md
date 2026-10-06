# Deal scanner setup

This takes about 30 minutes on a computer, once. After that it runs in the cloud every 30 minutes and you check the results page on your phone.

## 1. Get eBay API keys

1. Go to developer.ebay.com and click **Register**. Sign up with your normal details. eBay can take up to a day to approve a new developer account.
2. Once approved, open **Application Keys** and create a keyset for **Production** (not Sandbox). Give it any name, such as "TCG Outlet scanner".
3. eBay will ask about **Marketplace Account Deletion notifications** before it activates the keys. Choose the option to apply for an **exemption** and pick the reason that says you don't persist eBay user data. The scanner only keeps listing details, not buyer or seller accounts.
4. Copy two values from the Production keyset:
   - **App ID (Client ID)**
   - **Cert ID (Client Secret)**

Keep these private. Don't paste them into any file.

## 2. Put the scanner on GitHub

1. Create a free account at github.com.
2. Click **New repository**. Give it a name that's hard to guess, such as `tco-scan-7k2q`, and set it to **Public**. Free GitHub accounts can only host a results page from a public repository. The page is hidden from search engines, but anyone with the exact link could open it.
3. On the new repository page, click **uploading an existing file**. Drag in `scanner.py`, `config.json`, `page_template.html`, `SETUP.md` and the `docs` folder, then click **Commit changes**.
4. The workflow file sits in a hidden folder, so add it by hand: click **Add file → Create new file**, type the name `.github/workflows/scan.yml` (the slashes create the folders), paste in the contents of `scan.yml`, and click **Commit changes**.

## 3. Add your eBay keys

1. In the repository, go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret** and add:
   - Name `EBAY_CLIENT_ID`, value: your App ID
   - Name `EBAY_CLIENT_SECRET`, value: your Cert ID

## 4. Turn on the results page

1. Go to **Settings → Pages**.
2. Under **Build and deployment**, set Source to **Deploy from a branch**, branch **main**, folder **/docs**, and click **Save**.
3. After a minute the page address appears at the top, in the form `https://YOUR-USERNAME.github.io/REPO-NAME/`.

## 5. Run the first scan

1. Open the **Actions** tab. If GitHub asks, click to enable workflows.
2. Click **Deal scan** on the left, then **Run workflow**.
3. When it shows a green tick (about a minute), open your results page. From now on it runs by itself every 30 minutes.

On your phone, open the results page and use **Add to Home Screen** so it works like an app.

## Changing the rules

Open `config.json` on GitHub, tap the pencil icon to edit, change a number and commit. This works on your phone too. The next scan uses the new numbers.

| Setting | What it does |
|---|---|
| `price_min`, `price_max` | Price range of listings it scans, in £ |
| `fee_pct`, `fee_fixed` | Your selling fees. Set these to your real eBay fees |
| `post_low`, `post_high`, `track_from` | Your postage costs (second class, tracked) and the order value tracked starts at |
| `pack` | Packaging cost per order |
| `cond_pct` | How much LP, MP, HP and damaged cards are worth compared with NM |
| `strong_roi`, `good_roi`, `min_profit` | What counts as a strong or good buy |
| `show_min_roi` | Deals below this ROI aren't saved at all |
| `min_comps` | How many other listings of the same card it needs before it trusts the market value |
| `min_seller_feedback_pct`, `min_seller_feedback_score` | Skips sellers below these |
| `fake_below_pct` | Flags listings priced below this % of market |

## Good to know

- **Market value** is the middle price of other eBay UK Buy It Now listings of the same card, after trimming the extremes. Asking prices run a bit above what cards actually sell for, so check PulseTCG before you buy anything big.
- **It identifies cards by the number in the title**, like 199/165 or SVP 052. Listings without a number, lots, bundles, graded slabs, sealed product and proxies are skipped.
- **Condition comes from the title.** If the title doesn't say, it assumes NM, so check the photos.
- **eBay call limits.** The default settings use up to about 3,300 of eBay's standard 5,000 daily calls. If you change `pages_per_scan`, `max_comp_lookups_per_scan` or the schedule, keep an eye on that. If the limit is hit, the page says so and the next day carries on.
- **Sold listings** are checked and removed for the best deals each scan. Others drop off after 72 hours.
- **Hide** on a deal hides it on that phone only.
- If scans ever stop, open the Actions tab and re-enable the workflow. GitHub pauses schedules on repositories it thinks are inactive.
