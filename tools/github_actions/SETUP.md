# GitHub Actions Setup for SCQ arXiv Digest

This runs the arXiv scraper daily on GitHub's servers — no need to leave your
computer on.

## Quick Setup (10 minutes)

### 1. Create the repo

Go to https://github.com/new and create a **private** repo named something
like `scq-arxiv-digest`.

### 2. Push the project files

Open a terminal in your `arXivScooper` folder and run:

```bash
cd arXivScooper
git init
git add tools/arxiv_digest.py tools/github_actions/requirements.txt
git add digests/.gitkeep
git commit -m "Initial commit: arXiv digest scraper"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/scq-arxiv-digest.git
git push -u origin main
```

### 3. Add the workflow file

GitHub Actions expects the workflow in a specific location:

```bash
mkdir -p .github/workflows
cp tools/github_actions/workflows/arxiv_digest.yml .github/workflows/arxiv_digest.yml
git add .github/workflows/arxiv_digest.yml
git commit -m "Add daily digest workflow"
git push
```

### 4. Set up email secrets

To get email notifications, you need a Gmail App Password:

1. Go to https://myaccount.google.com/apppasswords
2. Create an app password (select "Mail" and "Other", name it "SCQ Digest")
3. Copy the 16-character password

Then add secrets to your GitHub repo:

1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** and add these three:

| Secret name            | Value                                  |
|------------------------|----------------------------------------|
| `SCQ_EMAIL_FROM`       | your-gmail@gmail.com                   |
| `SCQ_EMAIL_APP_PASSWORD` | the 16-char app password from step 2 |
| `SCQ_EMAIL_TO`         | recipient@example.com                  |

### 5. Test it

Go to your repo → **Actions** tab → **SCQ arXiv Daily Digest** → **Run workflow** → click the green button.

Watch the run complete (~2 minutes). You should get an email and see a new
digest HTML committed to the `digests/` folder.

## What happens each day

- **7:00 AM EST**: GitHub runs the scraper
- It fetches new papers from quant-ph, cond-mat.supr-con, cond-mat.mtrl-sci
- Ranks them by relevance to your SCQ research interests
- Sends you an email with the top papers
- Commits the full HTML digest to the `digests/` folder in your repo
- On **Mondays**, it looks back 3 days to catch weekend posts

## Viewing the digest

You have a few options:

1. **Email**: Check the summary email for quick scanning
2. **GitHub**: Go to the repo, open `digests/digest_YYYY-MM-DD.html`, click "Raw" and save it locally to open in a browser
3. **GitHub Pages** (optional): Enable Pages in repo Settings to browse digests directly at `https://YOUR_USERNAME.github.io/scq-arxiv-digest/digests/digest_YYYY-MM-DD.html`

## Importing triaged papers into your database

1. Open the digest HTML in your browser
2. Click "+ Add to Read List" on papers you want
3. Click "Save Triage Selections" at the bottom — downloads `pending_papers.json`
4. Open `paper_database.html`, click **Import**, select the `pending_papers.json`

## Customizing

Edit `tools/arxiv_digest.py` to change:

- **Categories**: `ARXIV_CATEGORIES` list at the top
- **Keywords/weights**: `KEYWORD_WEIGHTS` dict — higher weight = more relevant
- **Schedule**: Edit `.github/workflows/arxiv_digest.yml` cron expression
  - The cron uses UTC. 12:00 UTC = 7:00 AM EST = 8:00 AM EDT

## Cost

GitHub gives private repos **2,000 free Actions minutes per month**. Each
digest run takes ~2 minutes, so 30 days × 2 min = 60 minutes/month. Well
within the free tier.
