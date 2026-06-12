# public-actions-runner-host

Self-hosted GitHub Actions runner for the GlacierEQ legal brief pipeline.

## What this does

1. **LaTeX compile** — compiles `.tex` briefs from any connected repo using `tectonic` or `pdflatex`
2. **Supabase upload** — pushes compiled PDFs to Supabase Storage under `case-briefs/{repo}/{branch}/{filename}.pdf`
3. **Notion sync** — updates a Notion case database row with the new PDF URL and build metadata
4. **MotherDuck analytics** — runs citation/word-count metrics over the compiled text and appends to a DuckDB cloud table

## Connected repos

- [`LawTeX`](https://github.com/GlacierEQ/lawtex) — LaTeX legal brief templates
- Any repo with `.tex` files in `briefs/` or `motions/` directories

## Secrets required

Set these in the **repo or org secrets** panel:

| Secret | Description |
|--------|-------------|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key |
| `NOTION_TOKEN` | Notion internal integration token |
| `NOTION_DATABASE_ID` | ID of your case management database |
| `MOTHERDUCK_TOKEN` | MotherDuck service token |

## Runner setup

```bash
# On your runner host machine (Linux/macOS)
mkdir actions-runner && cd actions-runner
curl -o actions-runner-linux-x64.tar.gz -L \
  https://github.com/actions/runner/releases/latest/download/actions-runner-linux-x64.tar.gz
tar xzf ./actions-runner-linux-x64.tar.gz
./config.sh --url https://github.com/GlacierEQ --token YOUR_TOKEN
./run.sh
```

Or run as a Docker container using the included `Dockerfile`.

## Workflow triggers

- `push` to any branch containing `.tex` files
- `workflow_dispatch` for manual builds
- `repository_dispatch` from Overleaf Git sync webhooks
