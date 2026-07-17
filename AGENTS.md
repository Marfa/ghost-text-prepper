# Ghost Text Prepper — agent notes

Daily prep of Ghost draft posts: short SEO/social excerpt via Hugging Face.

Follow [ponytail](https://github.com/DietrichGebert/ponytail):

1. Does this need to exist? If not, delete it.
2. Reuse helpers already in `app.py` before adding files.
3. Prefer stdlib over new packages.
4. Keep Ghost Admin JWT auth correct.
5. One entry file (`app.py`) is intentional — do not split without a hard reason.

## Dependencies (required)

This is a Python project — treat `requirements.txt` like `package.json`.

1. **Before adding a package:** check PyPI + OSV (see check-dep skill). Prefer packages already listed.
2. **Pin with a lower bound of the current latest** when adding (`pkg>=X.Y.Z,<next_major`), not a remembered old version.
3. **Immediately after install:** run `pip-audit -r requirements.txt` (Python ≥3.10). Fix or do not merge on known vulns.
4. **Periodically:** `pip list --outdated` (or `pip-audit`) and bump when safe.
5. **Never commit `.env`.** Secrets only in env / GitHub Actions secrets.
6. **CI enforces** `pip-audit` and `gitleaks` on every push/PR (`.github/workflows/security.yml`).

## Secrets / pre-commit

Install [gitleaks](https://github.com/gitleaks/gitleaks) (`brew install gitleaks`) and enable the hook:

```bash
pre-commit install
```

Config: `.pre-commit-config.yaml`. Do not bypass with `--no-verify`.
